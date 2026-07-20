#!/usr/bin/env python3
"""Render the widget's meters and sparklines as retina PNGs, with zero dependencies.

SwiftBar accepts `image=<base64 png>` on any menu row, so instead of drawing bars
out of block characters we draw real graphics: rounded capsule meters with a
horizontal gradient, and sparklines with an area fill and an emphasised endpoint.

No Pillow. The venv runs a very new Python where a Pillow wheel may not exist,
and a source build would fail on someone else's Mac - the whole point of this
tool is that it installs in one command. So we encode PNGs directly (zlib +
struct) and anti-alias by supersampling coverage per pixel.

Retina: we render at 2x pixels and stamp a pHYs chunk of 144 DPI, so AppKit
sizes the image at half its pixel dimensions in points and it stays crisp.

Usage (one process renders every image, so the plugin shells out once):
    echo '<spec-json>' | render_assets.py
where spec-json is a list of objects:
    {"key": "session", "type": "meter", "frac": 0.6,  "color": "#FF9500"}
    {"key": "cc7d",    "type": "spark", "values": [3,1,5,8,1,4,1], "color": "#5E5CE6"}
optionally with "dark": true for dark-mode track colours.
Prints one `key<TAB>base64` line per entry, in order.
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import zlib

SCALE = 2                 # render at 2x device pixels
DPI = 72 * SCALE          # stamped into pHYs so AppKit halves it back to points
PX_PER_METRE = int(round(DPI / 0.0254))
SS = 4                    # supersampling grid per axis for anti-aliasing


# --------------------------------------------------------------------------- png

def _png(width: int, height: int, pixels: list[bytearray]) -> bytes:
    """Encode 8-bit RGBA rows into a PNG carrying a 144-DPI pHYs chunk."""
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)  # filter 0 per scanline

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"pHYs", struct.pack(">IIB", PX_PER_METRE, PX_PER_METRE, 1))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _blank(w: int, h: int) -> list[bytearray]:
    return [bytearray(w * 4) for _ in range(h)]


def _hex_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _blend(rows: list[bytearray], x: int, y: int, rgb: tuple[int, int, int], a: float) -> None:
    """Source-over composite one pixel with coverage/alpha `a` (0..1)."""
    if a <= 0:
        return
    a = min(1.0, a)
    i = x * 4
    row = rows[y]
    dr, dg, db, da = row[i], row[i + 1], row[i + 2], row[i + 3] / 255.0
    out_a = a + da * (1 - a)
    if out_a <= 0:
        return
    for k, sc in enumerate(rgb):
        dc = (dr, dg, db)[k]
        row[i + k] = int(round((sc * a + dc * da * (1 - a)) / out_a))
    row[i + 3] = int(round(out_a * 255))


# ------------------------------------------------------------------------ meter

def _capsule_cover(px: float, py: float, w: float, h: float) -> bool:
    """Is this point inside a horizontal capsule of size w x h?"""
    r = h / 2.0
    if px < r:
        return (px - r) ** 2 + (py - r) ** 2 <= r * r
    if px > w - r:
        return (px - (w - r)) ** 2 + (py - r) ** 2 <= r * r
    return 0 <= py <= h


def meter(frac: float, color: str, dark: bool, w_pt: int = 74, h_pt: int = 7) -> bytes:
    w, h, rows = meter_rows(frac, color, dark, w_pt, h_pt)
    return _png(w, h, rows)


def meter_rows(frac: float, color: str, dark: bool, w_pt: int = 74, h_pt: int = 7):
    """Rounded capsule meter: faint track, gradient fill, both ends rounded."""
    frac = max(0.0, min(1.0, float(frac)))
    w, h = w_pt * SCALE, h_pt * SCALE
    rows = _blank(w, h)

    base = _hex_rgb(color)
    # A lighter sibling of the accent for the gradient's left end.
    light = tuple(min(255, int(c + (255 - c) * 0.34)) for c in base)
    track_rgb = (255, 255, 255) if dark else (0, 0, 0)
    track_a = 0.20 if dark else 0.13

    # Fill is its own capsule so a small percentage still reads as a rounded pill
    # rather than a sliver clipped flat on its right edge.
    fill_w = frac * w
    if 0 < fill_w < h:
        fill_w = h  # never narrower than a dot

    step = 1.0 / SS
    for py in range(h):
        for px in range(w):
            track_cov = 0.0
            fill_cov = 0.0
            for sy in range(SS):
                fy = py + (sy + 0.5) * step
                for sx in range(SS):
                    fx = px + (sx + 0.5) * step
                    if _capsule_cover(fx, fy, w, h):
                        track_cov += 1
                        if frac > 0 and _capsule_cover(fx, fy, fill_w, h):
                            fill_cov += 1
            n = SS * SS
            if track_cov:
                _blend(rows, px, py, track_rgb, track_a * (track_cov / n))
            if fill_cov:
                t = px / max(1.0, fill_w)          # horizontal gradient position
                t = max(0.0, min(1.0, t))
                rgb = tuple(int(round(light[k] + (base[k] - light[k]) * t)) for k in range(3))
                _blend(rows, px, py, rgb, fill_cov / n)
    return w, h, rows


# --------------------------------------------------------------------- sparkline

def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L))
    dx, dy = ax + t * vx - px, ay + t * vy - py
    return (dx * dx + dy * dy) ** 0.5


def spark(values: list[float], color: str, dark: bool,
          w_pt: int = 120, h_pt: int = 22) -> bytes:
    w, h, rows = spark_rows(values, color, dark, w_pt, h_pt)
    return _png(w, h, rows)


def spark_rows(values: list[float], color: str, dark: bool,
               w_pt: int = 120, h_pt: int = 22):
    """Sparkline: soft area fill, 2pt line, emphasised final point."""
    vals = [float(v) for v in values] or [0.0]
    w, h = w_pt * SCALE, h_pt * SCALE
    rows = _blank(w, h)
    rgb = _hex_rgb(color)

    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pad = 3.0 * SCALE
    dot_r = 2.0 * SCALE
    usable_w = w - dot_r * 2
    n = len(vals)

    pts = []
    for i, v in enumerate(vals):
        x = dot_r + (usable_w * (i / (n - 1))) if n > 1 else w / 2.0
        y = h - pad - ((v - lo) / span) * (h - pad * 2)
        pts.append((x, y))

    half = 1.0 * SCALE  # half of a 2pt stroke
    step = 1.0 / SS
    for py in range(h):
        for px in range(w):
            fx, fy = px + 0.5, py + 0.5

            # area fill under the curve, fading downward
            area = 0.0
            for sy in range(SS):
                sfy = py + (sy + 0.5) * step
                for sx in range(SS):
                    sfx = px + (sx + 0.5) * step
                    # find the curve's y at this x by linear interpolation
                    cy = None
                    for i in range(len(pts) - 1):
                        ax, ay = pts[i]
                        bx, by = pts[i + 1]
                        if ax <= sfx <= bx and bx != ax:
                            cy = ay + (by - ay) * ((sfx - ax) / (bx - ax))
                            break
                    if cy is not None and sfy >= cy:
                        area += 1
            if area:
                depth = 1.0 - (py / max(1.0, h))     # strongest just under the line
                _blend(rows, px, py, rgb, 0.30 * (area / (SS * SS)) * (0.35 + 0.65 * depth))

            # the line itself
            d = min((_seg_dist(fx, fy, *pts[i], *pts[i + 1]) for i in range(len(pts) - 1)),
                    default=1e9)
            cov = max(0.0, min(1.0, half + 0.5 - d))
            if cov:
                _blend(rows, px, py, rgb, cov)

            # emphasised endpoint
            ex, ey = pts[-1]
            de = ((fx - ex) ** 2 + (fy - ey) ** 2) ** 0.5
            cov = max(0.0, min(1.0, dot_r + 0.5 - de))
            if cov:
                _blend(rows, px, py, rgb, cov)

    return w, h, rows


# --------------------------------------------------------------------------- cli

def main() -> int:
    try:
        spec = json.load(sys.stdin)
    except Exception as e:
        sys.stderr.write(f"bad spec: {e}\n")
        return 2
    for item in spec:
        try:
            dark = bool(item.get("dark"))
            if item.get("type") == "spark":
                data = spark(item.get("values") or [0], item.get("color", "#5E5CE6"), dark,
                             int(item.get("w", 120)), int(item.get("h", 22)))
            else:
                data = meter(item.get("frac", 0), item.get("color", "#34C759"), dark,
                             int(item.get("w", 74)), int(item.get("h", 7)))
            print(f"{item.get('key', '')}\t{base64.b64encode(data).decode('ascii')}")
        except Exception as e:                       # never take the widget down
            sys.stderr.write(f"render {item.get('key')} failed: {e}\n")
            print(f"{item.get('key', '')}\t")
    return 0


if __name__ == "__main__":
    sys.exit(main())
