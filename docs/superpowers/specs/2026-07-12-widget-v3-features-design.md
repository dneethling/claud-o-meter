# Widget v3 Features - Design Spec

**Date:** 2026-07-12
**Status:** Approved (verbal), spec for the record
**Author:** Darren Neethling + Claude (fable-5)

## Overview

Six feature additions to the working SwiftBar Claude/Codex usage widget, turning it
from a status display into a predictive, analytical, actionable cockpit. All stay in
the existing architecture (Python computes, bash renders) and reuse data already on
disk. Nothing here needs a native app or new heavy infrastructure.

## Guiding constraints (house rules)

- Never break the working widget; every feature degrades gracefully if its data is missing.
- macOS bash 3.2 (no mapfile, no flock binary, no `/usr/bin/timeout` - use the existing
  `run_timeout` perl shim and `while read`).
- British spelling in prose/comments; no em/en dashes in committed files.
- Pure helpers live in `lib/format.sh` and are unit-tested; each Python source prints one
  JSON object per the `PROVIDERS.md` contract.
- Verify before done: tests green + live render still shows all sections.

## Foundation changes (shared by several features)

1. **History retention**: bump `HISTORY_CAP` from 288 (24h) to 2016 (7 days at 5-min).
   The file stays tiny (~40 KB). A 7-day window gives the *weekly* prediction a real slope.
2. **`claude_code_usage.py` summary gains**: per-model breakdown for `today` and `week`
   (the per-(day,model) buckets already exist in its cache), a `prev_week` window
   (days 8-14) for week-over-week, and a `daily` array (last 7 days of total tokens) for
   the daily sparkline. All are exposures of data it already parses.
3. **`codex_usage.py` summary gains**: `prev_week` and `daily` (last 7 days), computed
   from its dated `threads` rows.

---

## Feature 1 - Prediction (burn-rate + ETA)

**What:** Under the Weekly row, a line that answers "will I get throttled before the
limit resets?" - the single most useful thing a limit tracker can tell you.

**Mechanism:** New `predict.py` reads `~/.claude-usage-history` (epoch, session%, weekly%
per line). It fits a linear slope to the recent weekly% samples (awk/numpy-free linear
regression) and projects time to 100%. Reset-aware: if the pct dropped sharply since the
previous sample (a reset), it only fits samples *after* the drop.

**The useful framing:** compare projected-time-to-100% against time-to-reset:
- ETA-to-100% comes BEFORE the weekly reset -> `at this pace ~100% by Thu 2pm` in red/orange
  (you will be throttled first).
- Reset comes first, or slope is flat/negative -> `on track, resets with headroom` muted.

**Output JSON:**

    {
      "available": true,
      "weekly":  { "eta_iso": "<ISO or null>", "verdict": "throttle|headroom|flat" },
      "session": { "eta_iso": "<ISO or null>", "verdict": "throttle|headroom|flat" }
    }

**Display:** one line under Weekly (and optionally Session), coloured by verdict via the
active theme. Degrades to nothing if history has < 3 usable samples.

---

## Feature 2 - Deeper analytics

**2a. Opus vs Sonnet split (Claude Code):** under the CLAUDE CODE section, a line
`Today · Opus 1.2B · Sonnet 700M` from the per-model buckets. Only models with non-zero
tokens are shown; ordered highest first.

**2b. Week-over-week:** on the 7-day total, a delta badge `▲ 12%` (up, orange) or
`▼ 8%` (down, green - less usage) vs `prev_week`, computed as
`(week - prev_week) / prev_week`. Hidden if `prev_week` is zero (no baseline).

**2c. 7-day daily sparkline:** a `sparkline` of the `daily` token array under the CLAUDE
CODE section, so the weekly rhythm is visible at a glance. Same for Codex if space allows
(Codex first, since it is smaller; keep the menu glanceable).

All three reuse the tested `sparkline` helper and `humanize_tokens`.

---

## Feature 3 - Smarter alerts & actions

**3a. Reset-ready ping:** track the last-seen session% and weekly% in the existing alert
state area (a small `~/.claude-usage-lastseen` file: `session weekly`). If the current
value is far below the last-seen (drop greater than a RESET_DROP threshold, e.g. 30
points), fire a one-time notification `Session reset - you are clear to go` (or Weekly).
Uses the existing `notify`/alert-lock machinery so it cannot double-fire.

**3b. Snooze / mute:** a `~/.claude-usage-mute-until` file holding an epoch. `notify()`
returns early when `now < mute-until`. Menu items write it:
`Mute alerts 1h` (now+3600), `Mute till tomorrow` (next 09:00), `Unmute` (remove file).
The dropdown shows `Muted until <time>` when active.

**3c. Copy summary:** a menu item runs a small bash that pipes a plaintext snapshot
(session/weekly/credits + CC/Codex today) to `pbcopy`, so you can paste your status into
a message or note in one click.

---

## Feature 4 - Codex throttle gauge (needs a one-time capture)

The live 5h/weekly ChatGPT ceilings for Codex. This is Phase 5 of the existing `ROADMAP.md`
and is unchanged here: it needs Darren to capture the ChatGPT usage endpoint once via
DevTools (procedure in the roadmap). `codex_limits.py` then renders percentage bars in the
Codex section. Everything else in v3 ships without it; this slots in whenever the capture
happens.

---

## Feature 5 - Data export

**What:** Menu items `Export usage CSV` and `Export usage JSON` that write a dated file to
`~/Downloads` and reveal it in Finder.

**Mechanism:** `export_usage.py` merges the three local sources into a tidy table:
per-day rows with columns `date, cc_tokens, cc_opus, cc_sonnet, codex_tokens,
session_pct_last, weekly_pct_last`. CSV for spreadsheets, JSON for tooling. The history
file supplies the daily last-known percentages; the CC/Codex caches supply daily tokens.

**Display:** an `Export ▸` submenu (SwiftBar nested items) with the two actions; each
writes `~/Downloads/claude-usage-YYYY-MM-DD.{csv,json}` and opens Finder at it.

---

## Feature 6 - Palette themes

**What:** a `THEME` config knob (`semantic` default, `minimal`, `colorblind`) that changes
how `color_for_pct` maps a percentage to a colour. This is the only theming surface that
matters for a text dropdown and it fixes a real accessibility gap.

**Palettes (in `lib/format.sh`, `color_for_pct` becomes theme-aware via a `THEME` var):**
- `semantic` (default): green `#34C759` / orange `#FF9500` / red `#FF3B30` (current).
- `minimal`: monochrome - `color_for_pct` returns an empty string, and bars/text render
  with NO `color=` key so SwiftBar uses the adaptive label colour.
- `colorblind`: Okabe-Ito safe palette - blue `#0072B2` (low) / orange `#E69F00` (warn) /
  vermillion `#D55E00` (crit). Distinguishable for red-green colour blindness.

**Black-on-black guard (critical):** an earlier bug was `color=` with an empty value
rendering black (invisible). So the minimal theme must NOT emit `color=`. Add a tiny helper
`colorkey <hex>` to `lib/format.sh` that echoes `color=$hex` when `$hex` is non-empty and
nothing when empty. Every bar/line render site uses `$(colorkey "$clr")` instead of a bare
`color=$clr`. This makes minimal (empty) safe AND keeps the never-black guarantee for the
semantic/colourblind themes. In semantic/colourblind, `color_for_pct` still never returns
empty for a valid percentage (default low colour on empty INPUT is preserved).

`THEME` is read from the config file (like `MENUBAR_MODE`) and exported before sourcing so
`lib/format.sh` sees it. Unit tests cover all three palettes and `colorkey` both ways.

## New/changed files

```
lib/format.sh          # color_for_pct theme-aware; (sparkline already present)
claude_code_usage.py   # per-model + prev_week + daily in summary
codex_usage.py         # prev_week + daily in summary
predict.py             # NEW - burn-rate ETA
export_usage.py        # NEW - CSV/JSON export
plugins/claude-usage.5m.sh  # render predictions, analytics, alerts, snooze, export, theme knob
tests/test_format.sh   # theme palette assertions
tests/test_predict.py  # NEW - linear-fit + verdict logic
README.md              # THEME + snooze + export docs
```

## Sequencing (recommended build order)

1. Foundation (history bump + summary exposures) - unblocks 1 and 2.
2. Feature 1 Prediction.
3. Feature 2 Analytics.
4. Feature 6 Themes (touches `color_for_pct`, best done before more colour call-sites pile up).
5. Feature 3 Alerts & actions.
6. Feature 5 Data export.
7. Feature 4 Codex throttle - whenever the capture lands.

## Explicitly out of scope (bloat guard)

Full custom-colour theming engine, more providers (Gemini/Cursor), a config GUI, cloud sync.

## Testing

- Pure helpers (`color_for_pct` per theme, `sparkline`) unit-tested in `tests/test_format.sh`.
- Prediction linear-fit + verdict logic unit-tested in `tests/test_predict.py` with fixed
  sample series (no live data).
- Each feature verified by a live `bash plugins/claude-usage.5m.sh` render showing the new
  line, plus the regression guard that all existing sections still appear.
