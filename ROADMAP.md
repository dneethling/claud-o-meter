# Claude/Codex Usage Widget - Multi-Provider Roadmap & Implementation Plan

> **STATUS (2026-07-15):** ALL phases are DONE and shipped. Phases 1-4 below,
> the v3 feature set (prediction, analytics, themes, alerts+snooze+copy, export;
> see `docs/superpowers/specs/2026-07-12-widget-v3-features-design.md`), AND
> **Phase 5 (Codex rate-limits)** are complete. Phase 5 turned out NOT to need a
> DevTools capture: Codex writes its own `rate_limits` (used_percent, window,
> resets_at, plan_type) into the session rollout files under ~/.codex/sessions,
> so `codex_usage.py` reads it locally. The Codex bar now shows a real quota %
> ("cx 20%") and the dropdown a quota gauge. This roadmap is now historical
> reference; there is no outstanding planned work.

> **For agentic workers:** This is a self-contained plan. You have **zero prior context** and that is fine - everything you need is in this file. Execute phases **top to bottom**, one task at a time. Every task ends with a test and a commit. Do **not** skip the "Verify" steps. If a verify step fails, stop and fix before moving on. Steps use checkbox (`- [ ]`) syntax so you can track progress.

**Goal:** Extend a working macOS SwiftBar menu-bar widget (currently shows Claude + Codex usage) with five upgrades: a small provider-contract foundation, usage-trend sparklines, a status/incident badge, a configurable menu-bar glance, and Codex web rate-limits.

**Architecture:** A single bash SwiftBar plugin renders the menu bar and dropdown. It shells out to small single-purpose Python scripts (one per data source) that each print JSON to stdout. Pure display/formatting helpers live in bash. Everything is local-first where possible; the only network calls are to Claude's internal usage API and (new, Phase 5) Codex's.

**Tech Stack:** bash 3.2 (macOS system default - see House Rules), Python 3.14 in a venv at `.venv/`, `jq`, `curl_cffi` + `pycookiecheat` (already installed in the venv), SwiftBar.

---

## Part 1 - Orientation (READ THIS FIRST)

### 1.1 What this project is

A menu-bar tool that shows how much of the user's AI coding quota is used, at a glance. It tracks three things today and will track more after this plan:

- **Claude (web)** - session %, weekly %, per-model %, and paid "usage credits" when the weekly limit is hit. Data comes from `claude.ai`'s internal usage API using the user's browser session cookie.
- **Claude Code (local)** - token volume parsed from `~/.claude/projects/**/*.jsonl` log files. No network, no auth.
- **Codex (local)** - token volume from the `threads` table of `~/.codex/state_5.sqlite`. No network, no auth.

The user is on **flat-rate plans** (Claude Max 5x, ChatGPT Plus). They do **not** pay per token. So token counts are the honest metric; any dollar figure is framed as "≈ API-equivalent value extracted", clearly labelled as an estimate - never as a bill.

### 1.2 File inventory (current state)

```
claude-usage-widget/
├── plugins/claude-usage.5m.sh   # THE SwiftBar plugin (530 lines). Entry point. Renders everything.
├── fetch_usage.py               # Claude web: GET usage API via curl_cffi, validate JSON, print to stdout.
├── refresh_cookie.py            # Pull a valid Claude session cookie from a browser, validate it, write config.
├── claude_code_usage.py         # Claude Code local: parse ~/.claude JSONL, print token summary JSON.
├── codex_usage.py               # Codex local: read ~/.codex sqlite, print token summary JSON.
├── com.darren.claude-usage-refresh.plist  # LaunchAgent: runs refresh_cookie.py every 30 min.
├── README.md                    # User-facing docs.
├── ROADMAP.md                   # THIS FILE.
└── .venv/                        # Python venv (curl_cffi, pycookiecheat installed).
```

Config and runtime files (outside the repo, created at runtime):

```
~/.claude-usage-widget.conf              # USAGE_URL= and COOKIE= lines. Mode 600. Contains a secret cookie.
~/.claude-usage-widget.conf.lock         # Lock file for atomic writes.
~/.claude-usage-cc-summary.json          # Warm cache of Claude Code summary.
~/.claude-usage-cc-cache.json            # Incremental parse cache (per-file token buckets).
~/.claude-usage-codex-summary.json       # Warm cache of Codex summary.
/tmp/claude-usage-raw.json               # Last successful Claude web API response.
/tmp/claude-usage-err.log                # Last stderr from fetch/refresh.
/tmp/claude-usage-alert-state            # Which alerts have fired (de-dup).
```

### 1.3 The plugin's current structure (section markers, by line)

```
line  30  # --- Log rotation
line  42  # --- Thresholds for color coding      (WARN_PCT=60, CRIT_PCT=85)
line  46  # --- Helpers                           (functions listed below)
line 208  # --- Guards                            (venv/config existence checks)
line 225  # --- Fetch (with auto-recovery)        (calls fetch_usage.py, retries via refresh_cookie.py)
line 274  # --- Parse                             (jq extracts session/weekly/credits from the JSON)
line 327  # --- Alerts                            (mkdir-lock mutex, notify() on thresholds)
line 379  # --- Menu bar title                    (headline: "16% · 10%w" or credit mode)
line 421  # --- Dropdown                          (Claude web metric rows)
line 476  # --- Claude Code (local token usage)   (calls claude_code_usage.py)
line 500  # --- Codex (local token usage)         (calls codex_usage.py)
```

Existing bash helper functions (all near the top, in the `# --- Helpers` block):

| Function | Purpose |
|---|---|
| `color_for_pct <pct>` | Returns a hex colour: green `#34C759` (<60), orange `#FF9500` (60-84), red `#FF3B30` (85+). Never returns empty. |
| `progress_bar <pct>` | 14-char Unicode bar `█░`. |
| `round <float>` | Rounds a float to an int string. |
| `humanize_tokens <n>` | `532362776` → `532M`, `4030338896` → `4.0B`. Uses awk. |
| `humanize_usd <n>` | `1551` → `$1.6k`, `52` → `$52`. |
| `format_money <minor> <cur> <exp>` | Minor units → `$6.54` etc. |
| `print_metric <label> <pct> <reset>` | Renders one "label · N% + bar + reset" dropdown block. |
| `fmt_resets_all <iso...>` | Batches N ISO timestamps → "in 1h 14m" lines in ONE Python call with `timeout 2`. |
| `notify <title> <msg> <key>` | Fires a macOS notification once per `key`. Caller holds the alert mutex. |
| `clear_alert <key>` | Removes a fired-alert key (atomic, safe for regex metacharacters). |

### 1.4 How to run, test, and verify ANYTHING

Everything runs from the repo root: `cd ~/Downloads/claude-usage-widget`

```bash
# Render the full widget once (this is exactly what SwiftBar does every 5 min):
bash plugins/claude-usage.5m.sh

# Check bash syntax without running:
bash -n plugins/claude-usage.5m.sh

# Run a Python data source directly:
.venv/bin/python claude_code_usage.py | .venv/bin/python -m json.tool
.venv/bin/python codex_usage.py | .venv/bin/python -m json.tool

# Force the live widget to re-render (after editing the plugin):
open "swiftbar://refreshallplugins"
```

SwiftBar output format (you MUST follow this): each line is `TEXT | key=value key=value`. The **first line** is the menu-bar title. `---` starts the dropdown and separates sections. Useful keys: `size=12`, `color=#RRGGBB`, `font=Menlo`, `sfimage=<SF Symbol name>`, `href=<url>`, `bash=<path> param1=... terminal=false`, `refresh=true`.

### 1.5 House rules (NON-NEGOTIABLE - the user cares about these)

1. **Never break the working widget.** It renders in <1s today. After every change, run `bash plugins/claude-usage.5m.sh` and confirm the Claude/Claude Code/Codex sections still appear. If a new feature errors, it must **degrade gracefully** (show nothing or a muted note), never blank the whole widget.
2. **macOS bash 3.2 only.** The system bash is 3.2 (2007). **Do NOT use:** `mapfile`/`readarray`, `flock` (the binary - use `mkdir` as a mutex), `declare -A` associative arrays, `${var^^}` case modification. Use `while IFS= read -r` loops, `awk` for float maths, and `/usr/bin/timeout` (from coreutils; if absent, the code must still work - guard it).
3. **British spelling in all prose and comments** (colour, behaviour, initialise). Code identifiers that already exist (e.g. `color_for_pct`) stay as-is for consistency.
4. **No em-dashes or en-dashes** in committed files. Use a comma, full stop, or " - " with spaces.
5. **Verify before declaring done.** Run the actual command and read the actual output. Never assert a thing works from reasoning alone.
6. **Never print or commit secrets.** The cookie in `~/.claude-usage-widget.conf` and the token in `~/.codex/auth.json` are secrets. Never echo their values, never write them to a log, never commit them. `.gitignore` already excludes the config.
7. **Commit after every task** with a conventional-commit message and this trailer:
   ```
   Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
   ```
8. **Small files, one job each.** New Python data sources are one file each. Shared bash helpers go in `lib/format.sh` (created in Phase 1).

### 1.6 The provider data-flow model (the pattern every phase follows)

```
  Python data source                         bash plugin
  ┌────────────────────┐   stdout (JSON)    ┌──────────────────────────┐
  │ fetch_usage.py     │ ─────────────────▶ │ jq extracts fields        │
  │ claude_code_usage  │                    │ humanize_* formats them   │
  │ codex_usage.py     │                    │ echoes SwiftBar lines     │
  │ (Phase 5) codex_   │                    │ degrades if JSON missing  │
  │   limits.py        │                    └──────────────────────────┘
  └────────────────────┘
```

Every data source: prints ONE JSON object to stdout, writes nothing else there, puts errors on stderr, exits non-zero on hard failure, and (where useful) mirrors its last-good output to a `~/.claude-usage-*-summary.json` file so the plugin can fall back if a live call times out.

---

## Part 2 - Roadmap (phases, value, effort, order)

| Phase | Feature | Value | Effort | Needs the user? | Recommended order |
|---|---|---|---|---|---|
| 1 | Foundation: `lib/format.sh` + test harness + `PROVIDERS.md` | Enabling | S | No | **1st** |
| 2 | Usage-trend sparklines | Medium | S | No | **2nd** |
| 3 | Status/incident badge | Medium | S | No | **3rd** |
| 4 | Menu-bar multi-provider glance (configurable) | Medium | S | No | **4th** |
| 5 | Codex web rate-limits (5h/weekly ChatGPT ceilings) | **High** | M | **Yes** (one-time endpoint capture) | **5th** (do last: needs a human step) |

**Why this order:** Phases 1-4 are fully deterministic and need nothing from the user, so an agent can do them unattended. Phase 5 is the highest *value* (it turns Codex from "how much have I used" into "am I about to get throttled"), but it needs the user to capture an undocumented endpoint from their browser first, so it goes last. Do not block Phases 1-4 waiting on the user.

**YAGNI guardrails (do NOT build these):** no 57-provider support (the user uses two), no native Swift app (a prior native rewrite failed to render on this machine and was abandoned), no WidgetKit, no auto-update framework, no localisation, no inline pixel charts (sparklines cover the need).

---

## Part 3 - Detailed tasks

### PHASE 1 - Foundation: shared lib, test harness, provider contract

**Why:** The plugin's pure helpers currently live inside the 530-line script, so they cannot be unit-tested. Extracting them into a sourced `lib/format.sh` gives every later phase a place to add tested helpers (the sparkline goes here in Phase 2). This is a safe refactor: the plugin sources the file, behaviour is unchanged, and we prove it with tests + a live render.

**Files:**
- Create: `lib/format.sh`
- Create: `tests/assert.sh`
- Create: `tests/test_format.sh`
- Create: `PROVIDERS.md`
- Modify: `plugins/claude-usage.5m.sh` (replace inline helper defs with `source lib/format.sh`)

- [ ] **Step 1.1: Create the bash test harness**

Create `tests/assert.sh`:

```bash
#!/bin/bash
# Minimal assertion helpers for bash unit tests. Source this, call asserts, call finish.
TESTS_RUN=0
TESTS_FAILED=0

assert_eq() {
  # assert_eq <actual> <expected> <description>
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$1" != "$2" ]; then
    echo "FAIL: $3"
    echo "      expected: '$2'"
    echo "      got:      '$1'"
    TESTS_FAILED=$((TESTS_FAILED + 1))
  fi
}

finish() {
  echo "----"
  echo "$TESTS_RUN assertions, $TESTS_FAILED failed"
  [ "$TESTS_FAILED" -eq 0 ]
}
```

- [ ] **Step 1.2: Write the failing test for `lib/format.sh`**

Create `tests/test_format.sh`:

```bash
#!/bin/bash
DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/tests/assert.sh"
# shellcheck source=/dev/null
source "$DIR/lib/format.sh"

assert_eq "$(color_for_pct 30)"  "#34C759" "color green under 60"
assert_eq "$(color_for_pct 70)"  "#FF9500" "color orange 60-84"
assert_eq "$(color_for_pct 90)"  "#FF3B30" "color red 85+"
assert_eq "$(color_for_pct '')"  "#34C759" "color defaults green on empty"

assert_eq "$(humanize_tokens 532362776)"  "532M" "humanize 532M"
assert_eq "$(humanize_tokens 4030338896)" "4.0B" "humanize 4.0B"
assert_eq "$(humanize_tokens 66105)"      "66k"  "humanize 66k"
assert_eq "$(humanize_tokens 512)"        "512"  "humanize raw"

assert_eq "$(humanize_usd 1551)" "\$1.6k" "usd 1.6k"
assert_eq "$(humanize_usd 52)"   "\$52"   "usd 52"

assert_eq "$(round 16.4)" "16" "round down"
assert_eq "$(round 16.6)" "17" "round up"

finish
```

- [ ] **Step 1.3: Run the test to verify it fails**

Run: `bash tests/test_format.sh`
Expected: FAIL - `lib/format.sh` does not exist yet, so `source` errors and the asserts do not run. (You will see a "No such file or directory" for `lib/format.sh`.)

- [ ] **Step 1.4: Create `lib/format.sh` by extracting the existing helpers**

Create `lib/format.sh` with EXACTLY these functions, copied verbatim from `plugins/claude-usage.5m.sh` (lines 47-140, the `color_for_pct`, `progress_bar`, `round`, `humanize_tokens`, `humanize_usd`, `format_money` functions). Read them from the plugin and paste them here. The block to create:

```bash
#!/bin/bash
# Pure display/formatting helpers, sourced by the SwiftBar plugin and unit tests.
# No side effects, no globals beyond the threshold constants below.

WARN_PCT="${WARN_PCT:-60}"
CRIT_PCT="${CRIT_PCT:-85}"

color_for_pct() {
  local pct="${1%%.*}"
  [ -z "$pct" ] && { echo "#34C759"; return; }
  if [ "$pct" -ge "$CRIT_PCT" ]; then
    echo "#FF3B30"
  elif [ "$pct" -ge "$WARN_PCT" ]; then
    echo "#FF9500"
  else
    echo "#34C759"
  fi
}

progress_bar() {
  local pct="${1%%.*}"
  [ -z "$pct" ] && { echo ""; return; }
  local width=14
  local filled=$(( pct * width / 100 ))
  [ $filled -gt $width ] && filled=$width
  [ $filled -lt 0 ] && filled=0
  local empty=$(( width - filled ))
  local bar="" i
  for ((i=0; i<filled; i++)); do bar+="█"; done
  for ((i=0; i<empty; i++)); do bar+="░"; done
  echo "$bar"
}

round() { [ -n "$1" ] && printf "%.0f" "$1" || echo ""; }

humanize_tokens() {
  local n="$1"
  { [ -z "$n" ] || [ "$n" = "null" ]; } && { echo "—"; return; }
  awk -v n="$n" 'BEGIN {
    if (n >= 1e9)      printf "%.1fB", n/1e9;
    else if (n >= 1e6) printf "%.0fM", n/1e6;
    else if (n >= 1e3) printf "%.0fk", n/1e3;
    else               printf "%d", n;
  }'
}

humanize_usd() {
  local n="$1"
  { [ -z "$n" ] || [ "$n" = "null" ]; } && { echo "—"; return; }
  awk -v n="$n" 'BEGIN {
    if (n >= 1000) printf "$%.1fk", n/1000;
    else           printf "$%.0f", n;
  }'
}

format_money() {
  local amt="$1" cur="$2" exp="$3"
  if [ -z "$amt" ] || [ "$amt" = "null" ]; then echo "—"; return; fi
  local divisor=1 i=0
  while [ "$i" -lt "${exp:-0}" ]; do divisor=$(( divisor * 10 )); i=$(( i + 1 )); done
  local val fmt="%.${exp:-0}f"
  val=$(awk -v a="$amt" -v d="$divisor" -v f="$fmt" 'BEGIN { printf f, a/d }')
  case "$cur" in
    USD) echo "\$$val" ;;
    GBP) echo "£$val" ;;
    EUR) echo "€$val" ;;
    ZAR) echo "R$val" ;;
    JPY) echo "¥$val" ;;
    *)   echo "$val $cur" ;;
  esac
}
```

- [ ] **Step 1.5: Run the test to verify it passes**

Run: `bash tests/test_format.sh`
Expected: `14 assertions, 0 failed`

- [ ] **Step 1.6: Make the plugin source the lib instead of defining inline**

In `plugins/claude-usage.5m.sh`, immediately after the `# --- Thresholds for color coding` block (the two lines `WARN_PCT=60` and `CRIT_PCT=85`, around line 43-44), add this source line:

```bash
# Shared pure helpers (see lib/format.sh). WIDGET_DIR is defined above.
# shellcheck source=/dev/null
source "$WIDGET_DIR/lib/format.sh"
```

Then DELETE the now-duplicated function definitions from the plugin: `color_for_pct`, `progress_bar`, `round`, `humanize_tokens`, `humanize_usd`, `format_money` (they now live in `lib/format.sh`). Leave `print_metric`, `fmt_resets_all`, `notify`, `clear_alert` in the plugin - they have side effects or depend on plugin globals, so they stay.

- [ ] **Step 1.7: Verify the live widget still renders identically**

Run: `bash -n plugins/claude-usage.5m.sh && bash plugins/claude-usage.5m.sh`
Expected: syntax OK, and the output still shows the menu-bar title line, the Claude session/weekly rows, the `CLAUDE CODE · local` section, and the `CODEX · local` section. No "command not found" for any helper.

- [ ] **Step 1.8: Write the provider contract doc**

Create `PROVIDERS.md`:

```markdown
# Provider data-source contract

Every usage data source is a Python script run from the repo venv that prints
exactly one JSON object to stdout and nothing else. Errors go to stderr. Hard
failure exits non-zero. Where a live call can be slow, mirror the last good
output to a `~/.claude-usage-<name>-summary.json` file so the plugin can fall
back to it after a timeout.

## Local token-usage sources (claude_code_usage.py, codex_usage.py)

    {
      "available": true,
      "generated_at": "<ISO8601>",
      "today":    { "total_tokens": <int>, "est_cost_usd": <float, optional> },
      "week":     { "total_tokens": <int>, ... },
      "month":    { "total_tokens": <int>, ... },
      "all_time": { "total_tokens": <int>, ... }    // optional
    }

KNOWN WART (do not "fix" without updating the plugin): the two existing local
sources use different token keys. claude_code_usage.py uses `total_tokens`;
codex_usage.py uses `tokens` (plus `threads`). The plugin reads the correct key
per source. New local sources should use `total_tokens` to match this contract.

## Limit/quota sources (fetch_usage.py, Phase 5 codex_limits.py)

Percentage-based windows with reset times:

    {
      "available": true,
      "windows": [
        { "label": "Session (5h)", "percent": <int>, "resets_at": "<ISO8601>" },
        { "label": "Weekly",       "percent": <int>, "resets_at": "<ISO8601>" }
      ]
    }

## Rules

- Print JSON only to stdout.
- Never print secrets (cookies, tokens).
- Exit 0 with `{"available": false, "reason": "..."}` when the source is simply
  not configured (so the plugin can skip the section quietly); exit non-zero
  only on unexpected errors.
```

- [ ] **Step 1.9: Commit Phase 1**

```bash
cd ~/Downloads/claude-usage-widget
chmod +x tests/test_format.sh
git add lib/format.sh tests/assert.sh tests/test_format.sh PROVIDERS.md plugins/claude-usage.5m.sh
git commit -m "$(printf 'refactor: extract pure helpers to lib/format.sh + add test harness\n\nFoundation for multi-provider work. No behaviour change; the plugin\nnow sources lib/format.sh. Adds a minimal bash assert harness and\n14 unit tests for the formatters, plus PROVIDERS.md documenting the\nJSON contract every data source follows.\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>')"
```

---

### PHASE 2 - Usage-trend sparklines

**Why:** A percentage tells you where you are; a sparkline tells you how fast you are getting there. Each render appends a sample to a small rolling history file, then draws a `▁▂▃▅▇` sparkline of the session % over the last ~2 hours under the Session row.

**Files:**
- Modify: `lib/format.sh` (add `sparkline`)
- Modify: `tests/test_format.sh` (add sparkline tests)
- Create: `tests/test_history.sh`
- Modify: `plugins/claude-usage.5m.sh` (append sample + render sparkline)

- [ ] **Step 2.1: Add failing sparkline tests**

Append to `tests/test_format.sh` (before the final `finish` line):

```bash
# Sparkline: 8 levels ▁▂▃▄▅▆▇█ mapped linearly from min..max across the inputs.
assert_eq "$(sparkline "0 0 0")"        "▁▁▁"     "sparkline flat = all low"
assert_eq "$(sparkline "0 100")"        "▁█"      "sparkline min and max"
assert_eq "$(sparkline "0 50 100")"     "▁▅█"     "sparkline midpoint"
assert_eq "$(sparkline "")"             ""        "sparkline empty input"
assert_eq "$(sparkline "42")"           "▁"       "sparkline single value = low"
```

- [ ] **Step 2.2: Run to verify failure**

Run: `bash tests/test_format.sh`
Expected: FAIL on the sparkline asserts (`sparkline: command not found` inside the subshell, producing empty output that mismatches).

- [ ] **Step 2.3: Implement `sparkline` in `lib/format.sh`**

Append to `lib/format.sh`:

```bash
# Render a Unicode sparkline from space-separated numbers.
# 8 levels ▁▂▃▄▅▆▇█ mapped linearly across [min,max]. All-equal input -> all ▁.
sparkline() {
  local nums="$1"
  [ -z "$nums" ] && { echo ""; return; }
  awk -v s="$nums" 'BEGIN {
    n = split(s, a, " ");
    if (n == 0) { exit }
    mn = a[1]; mx = a[1];
    for (i = 1; i <= n; i++) { if (a[i] < mn) mn = a[i]; if (a[i] > mx) mx = a[i]; }
    split("▁▂▃▄▅▆▇█", g, "");
    out = "";
    for (i = 1; i <= n; i++) {
      if (mx == mn) { lvl = 1 }
      else { lvl = int(((a[i] - mn) / (mx - mn)) * 7 + 0.5) + 1 }
      out = out g[lvl];
    }
    print out;
  }'
}
```

Note: awk's `split("▁▂▃▄▅▆▇█", g, "")` splits on characters; on macOS awk these are multibyte but `split` with "" handles UTF-8 code points correctly under the default locale for this glyph set. Verify in the next step; if the glyphs come out wrong, set `LC_ALL=en_US.UTF-8` before the awk call.

- [ ] **Step 2.4: Run to verify pass**

Run: `bash tests/test_format.sh`
Expected: `19 assertions, 0 failed`. If the sparkline glyphs are garbled, prepend `LC_ALL=en_US.UTF-8 ` to the `awk` invocation inside `sparkline` and re-run.

- [ ] **Step 2.5: Write a test for history trimming**

Create `tests/test_history.sh`:

```bash
#!/bin/bash
DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "$DIR/tests/assert.sh"

# append_sample <file> <value> <cap> keeps at most <cap> newest lines.
append_sample() {
  local file="$1" value="$2" cap="$3"
  echo "$value" >> "$file"
  local n; n=$(wc -l < "$file" | tr -d ' ')
  if [ "$n" -gt "$cap" ]; then
    tail -n "$cap" "$file" > "$file.tmp" && mv -f "$file.tmp" "$file"
  fi
}

TMP="/tmp/test-history-$$.txt"
rm -f "$TMP"
for v in 1 2 3 4 5; do append_sample "$TMP" "$v" 3; done
assert_eq "$(wc -l < "$TMP" | tr -d ' ')" "3" "history capped at 3 lines"
assert_eq "$(tr '\n' ' ' < "$TMP" | sed 's/ $//')" "3 4 5" "history keeps newest"
rm -f "$TMP"
finish
```

- [ ] **Step 2.6: Run to verify pass**

Run: `bash tests/test_history.sh`
Expected: `2 assertions, 0 failed`

- [ ] **Step 2.7: Wire sampling + sparkline into the plugin**

In `plugins/claude-usage.5m.sh`, near the other path constants at the top (after `CODEX_SUMMARY=...`), add:

```bash
HISTORY_FILE="$HOME/.claude-usage-history"   # one "epoch sessionPct weeklyPct" line per render
HISTORY_CAP=288                               # 24h at 5-min cadence
```

Then in the `# --- Parse` section, AFTER `S_I` and `W_I` are computed and the `SHAPE_BROKEN` check, add (guard on numeric so a bad tick never pollutes history):

```bash
# Append a history sample and trim (used for the sparkline). Numeric-only.
if [[ "$S_I" =~ ^[0-9]+$ ]] && [[ "$W_I" =~ ^[0-9]+$ ]]; then
  printf '%s %s %s\n' "$(date +%s)" "$S_I" "$W_I" >> "$HISTORY_FILE"
  hlines=$(wc -l < "$HISTORY_FILE" 2>/dev/null | tr -d ' ')
  if [ -n "$hlines" ] && [ "$hlines" -gt "$HISTORY_CAP" ]; then
    tail -n "$HISTORY_CAP" "$HISTORY_FILE" > "$HISTORY_FILE.tmp" && mv -f "$HISTORY_FILE.tmp" "$HISTORY_FILE"
  fi
fi
```

Then in the `# --- Dropdown` section, find the Session row. It currently reads:

```bash
if [ -n "$SESSION" ]; then
  print_metric "Session · 5h" "$SESSION" "$SESSION_RESET_TXT"
  echo "---"
fi
```

Replace it with (adds a trend line built from the last 24 samples ≈ 2h):

```bash
if [ -n "$SESSION" ]; then
  print_metric "Session · 5h" "$SESSION" "$SESSION_RESET_TXT"
  if [ -f "$HISTORY_FILE" ]; then
    SESSION_TREND=$(tail -n 24 "$HISTORY_FILE" 2>/dev/null | awk '{printf "%s ", $2}')
    SPARK=$(sparkline "$SESSION_TREND")
    [ -n "$SPARK" ] && echo "  trend (last ~2h) $SPARK | font=Menlo size=11 color=#8E8E93"
  fi
  echo "---"
fi
```

- [ ] **Step 2.8: Verify live render**

Run: `bash plugins/claude-usage.5m.sh`
Expected: the Session block now has a `  trend (last ~2h) ▁▁▁...` line. It will be short until history accumulates over successive runs. Run the plugin 3-4 times and confirm the sparkline grows and `~/.claude-usage-history` gains lines.

- [ ] **Step 2.9: Commit Phase 2**

```bash
chmod +x tests/test_history.sh
git add lib/format.sh tests/test_format.sh tests/test_history.sh plugins/claude-usage.5m.sh
git commit -m "$(printf 'feat: usage-trend sparkline under the Session row\n\nEach render appends "epoch sessionPct weeklyPct" to ~/.claude-usage-history\n(capped 288 lines = 24h at 5-min). The Session block draws a Unicode\nsparkline of the last ~24 samples. Adds a tested sparkline() helper to\nlib/format.sh (8 levels, linear min-max mapping) and a history-trim test.\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>')"
```

---

### PHASE 3 - Status/incident badge

**Why:** When claude.ai or OpenAI has an incident, usage errors are not the user's fault. A small badge line and a dimmed icon tell them "it's them, not you". Both vendors expose a public Statuspage JSON API (no auth).

**Files:**
- Create: `status_check.py`
- Create: `tests/test_status_check.py`
- Modify: `plugins/claude-usage.5m.sh` (call it, add a badge line + dim icon on incident)

- [ ] **Step 3.1: Set up Python test tooling (once)**

Run: `.venv/bin/pip install pytest`
Expected: pytest installs. Verify: `.venv/bin/pytest --version` prints a version.

- [ ] **Step 3.2: Write the failing test for status parsing**

Create `tests/test_status_check.py`:

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_check  # noqa: E402


def test_operational_when_all_none():
    body = {"status": {"indicator": "none", "description": "All Systems Operational"}}
    assert status_check.classify(body) == "operational"


def test_incident_on_minor():
    body = {"status": {"indicator": "minor", "description": "Partial Outage"}}
    result = status_check.classify(body)
    assert result == "incident"


def test_incident_on_major():
    body = {"status": {"indicator": "major", "description": "Major Outage"}}
    assert status_check.classify(body) == "incident"


def test_unknown_on_garbage():
    assert status_check.classify({}) == "unknown"
    assert status_check.classify({"status": {}}) == "unknown"
```

- [ ] **Step 3.3: Run to verify failure**

Run: `.venv/bin/pytest tests/test_status_check.py -q`
Expected: FAIL - `ModuleNotFoundError: No module named 'status_check'`.

- [ ] **Step 3.4: Implement `status_check.py`**

Create `status_check.py`:

```python
#!/usr/bin/env python3
"""Check Anthropic and OpenAI public status pages; print a compact JSON verdict.

Both vendors run Atlassian Statuspage, which exposes /api/v2/status.json with a
{"status": {"indicator": "none|minor|major|critical", "description": "..."}}.
We map that to operational/incident/unknown. Cached to a summary file with a
short TTL so we do not hammer the endpoints on every 5-min tick.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

SUMMARY_PATH = Path.home() / ".claude-usage-status-summary.json"
CACHE_TTL_SECONDS = 300
TIMEOUT_SECONDS = 6
ENDPOINTS = {
    "anthropic": "https://status.anthropic.com/api/v2/status.json",
    "openai": "https://status.openai.com/api/v2/status.json",
}


def classify(body: dict) -> str:
    """Map a Statuspage status.json body to operational/incident/unknown."""
    try:
        indicator = body["status"]["indicator"]
    except (KeyError, TypeError):
        return "unknown"
    if indicator == "none":
        return "operational"
    if indicator in ("minor", "major", "critical"):
        return "incident"
    return "unknown"


def fetch(url: str) -> str:
    from curl_cffi import requests
    resp = requests.get(url, impersonate="chrome", timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        return "unknown"
    try:
        return classify(json.loads(resp.text))
    except Exception:
        return "unknown"


def build() -> dict:
    result = {"generated_at": time.time()}
    for name, url in ENDPOINTS.items():
        try:
            result[name] = fetch(url)
        except Exception:
            result[name] = "unknown"
    return result


def cached_or_fresh() -> dict:
    if SUMMARY_PATH.exists():
        try:
            cached = json.loads(SUMMARY_PATH.read_text())
            if time.time() - cached.get("generated_at", 0) < CACHE_TTL_SECONDS:
                return cached
        except Exception:
            pass
    result = build()
    try:
        tmp = SUMMARY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(result))
        os.replace(tmp, SUMMARY_PATH)
    except Exception:
        pass
    return result


if __name__ == "__main__":
    sys.stdout.write(json.dumps(cached_or_fresh()))
```

- [ ] **Step 3.5: Run to verify pass**

Run: `.venv/bin/pytest tests/test_status_check.py -q`
Expected: `4 passed`.

- [ ] **Step 3.6: Smoke-test the live call**

Run: `.venv/bin/python status_check.py | .venv/bin/python -m json.tool`
Expected: JSON with `anthropic` and `openai` each one of `operational`/`incident`/`unknown`. (If the network is blocked, both read `unknown` and that is acceptable degradation.)

- [ ] **Step 3.7: Wire the badge into the plugin**

Add path constants near the top of `plugins/claude-usage.5m.sh` (after `HISTORY_CAP=...`):

```bash
STATUS_CHECK="$WIDGET_DIR/status_check.py"
```

In the `# --- Menu bar title` section there is a `if [ $ON_CREDITS -eq 1 ]; then ... else ... fi` block followed by a SINGLE shared `echo "$TITLE | $ICON color=$TITLE_COLOR size=12"`. Insert this incident check on the line AFTER the closing `fi` and BEFORE that shared echo, so the override applies in both credit and normal modes:

```bash
# Provider status: dim + badge the icon if a vendor reports an incident.
STATUS_JSON=$(/usr/bin/timeout 7 "$PYTHON" "$STATUS_CHECK" 2>/dev/null)
INCIDENT=""
if [ -n "$STATUS_JSON" ]; then
  A_ST=$(echo "$STATUS_JSON" | jq -r '.anthropic // "unknown"')
  O_ST=$(echo "$STATUS_JSON" | jq -r '.openai // "unknown"')
  [ "$A_ST" = "incident" ] && INCIDENT="Claude"
  [ "$O_ST" = "incident" ] && INCIDENT="${INCIDENT:+$INCIDENT + }OpenAI"
fi
if [ -n "$INCIDENT" ]; then
  ICON="sfimage=exclamationmark.triangle.fill"
  TITLE_COLOR="#FF9500"
fi
```

Then in the `# --- Dropdown` section, right after the `Claude Usage Dashboard` header line and its following `---`, add a badge row when there is an incident:

```bash
if [ -n "$INCIDENT" ]; then
  echo "⚠ ${INCIDENT} reporting an incident | color=#FF9500 href=https://status.anthropic.com"
  echo "---"
fi
```

- [ ] **Step 3.8: Verify live render (both states)**

Run: `bash plugins/claude-usage.5m.sh`
Expected: normal render (no badge) when all is operational. To test the incident path, temporarily add `A_ST="incident"` right after the `A_ST=...` line, re-run, confirm the title icon becomes the warning triangle and a `⚠ Claude reporting an incident` row appears, then REMOVE the temporary line.

- [ ] **Step 3.9: Commit Phase 3**

```bash
git add status_check.py tests/test_status_check.py plugins/claude-usage.5m.sh
git commit -m "$(printf 'feat: status/incident badge from vendor status pages\n\nstatus_check.py polls status.anthropic.com and status.openai.com\n(/api/v2/status.json, no auth), classifies operational/incident/unknown,\nand caches for 5 min. On an incident the menu-bar icon dims to a warning\ntriangle and the dropdown shows a badge row linking to the status page.\nDegrades to no-badge when the network is unreachable. 4 unit tests.\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>')"
```

---

### PHASE 4 - Menu-bar multi-provider glance (configurable)

**Why:** Today the menu-bar title shows only Claude web limits. Some users want Codex or Claude Code volume visible at a glance too. Add a single config knob with three modes, defaulting to today's behaviour so nothing changes unless the user opts in.

**Files:**
- Modify: `plugins/claude-usage.5m.sh` (read a `MENUBAR_MODE`, build title accordingly)
- Modify: `README.md` (document the knob)

- [ ] **Step 4.1: Add the config knob with a safe default**

In `plugins/claude-usage.5m.sh`, in the `# --- Thresholds` area near the top, add:

```bash
# Menu-bar title mode. Override by adding a MENUBAR_MODE=... line to
# ~/.claude-usage-widget.conf. Values:
#   claude   (default) -> "16% · 10%w"
#   codex             -> "16% · 10%w · cx 120M"   (adds Codex 30-day tokens)
#   both              -> "16% · 10%w · cc 20.5B · cx 120M"
MENUBAR_MODE=$(grep -E '^MENUBAR_MODE=' "$CONFIG" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' ')
[ -z "$MENUBAR_MODE" ] && MENUBAR_MODE="claude"
```

Note: `$CONFIG` is defined near the top of the plugin already (`CONFIG="$HOME/.claude-usage-widget.conf"`). Confirm this line runs after `CONFIG` is set; if not, move it below that assignment.

- [ ] **Step 4.2: Extend the title builder**

In the `# --- Menu bar title` section, find the normal (non-credit) branch where `TITLE` is built:

```bash
  TITLE_COLOR=$(color_for_pct "$S_I")
  TITLE="${S_I:-?}%"
  if [[ "$W_I" =~ ^[0-9]+$ ]]; then
    TITLE="$TITLE · ${W_I}%w"
  fi
```

Replace with (appends provider volume per mode; the CC_* and CX_* vars are computed later in the script, so compute compact versions here from the summary files to avoid ordering issues):

```bash
  TITLE_COLOR=$(color_for_pct "$S_I")
  TITLE="${S_I:-?}%"
  if [[ "$W_I" =~ ^[0-9]+$ ]]; then
    TITLE="$TITLE · ${W_I}%w"
  fi
  case "$MENUBAR_MODE" in
    codex|both)
      if [ -f "$CODEX_SUMMARY" ]; then
        CX_M=$(jq -r '.month.tokens // empty' "$CODEX_SUMMARY" 2>/dev/null)
        [ -n "$CX_M" ] && TITLE="$TITLE · cx $(humanize_tokens "$CX_M")"
      fi
      ;;
  esac
  case "$MENUBAR_MODE" in
    both)
      if [ -f "$CC_SUMMARY" ]; then
        CC_M=$(jq -r '.month.total_tokens // empty' "$CC_SUMMARY" 2>/dev/null)
        [ -n "$CC_M" ] && TITLE="${TITLE/· cx/· cc $(humanize_tokens "$CC_M") · cx}"
      fi
      ;;
  esac
```

- [ ] **Step 4.3: Verify all three modes**

Run each and confirm the title line:

```bash
# default (no knob) -> "16% · 10%w"
bash plugins/claude-usage.5m.sh | head -1

# codex mode:
printf 'MENUBAR_MODE=codex\n' >> ~/.claude-usage-widget.conf
bash plugins/claude-usage.5m.sh | head -1   # expect "... · cx 120M"

# both mode:
sed -i '' 's/^MENUBAR_MODE=.*/MENUBAR_MODE=both/' ~/.claude-usage-widget.conf
bash plugins/claude-usage.5m.sh | head -1   # expect "... · cc 20.5B · cx 120M"

# restore default:
sed -i '' '/^MENUBAR_MODE=/d' ~/.claude-usage-widget.conf
```

Expected: title changes per mode; removing the line restores `16% · 10%w`.

- [ ] **Step 4.4: Document the knob in README**

In `README.md`, under a new `### Menu-bar display modes` heading in the config section, add:

```markdown
### Menu-bar display modes

By default the menu bar shows Claude limits only: `16% · 10%w`. To also surface
local token volume, add one line to `~/.claude-usage-widget.conf`:

    MENUBAR_MODE=codex   # adds Codex 30-day tokens:  16% · 10%w · cx 120M
    MENUBAR_MODE=both    # adds Claude Code too:       16% · 10%w · cc 20.5B · cx 120M

Remove the line (or set `claude`) for the default.
```

- [ ] **Step 4.5: Commit Phase 4**

```bash
git add plugins/claude-usage.5m.sh README.md
git commit -m "$(printf 'feat: configurable menu-bar multi-provider glance\n\nNew MENUBAR_MODE knob in the config file (default "claude", unchanged\nbehaviour). "codex" appends Codex 30-day tokens to the title; "both"\nadds Claude Code too. Reads compact figures from the warm summary files\nso there is no ordering dependency on the dropdown computation.\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>')"
```

---

### PHASE 5 - Codex web rate-limits (REQUIRES a one-time capture from the user)

**Why:** The highest-value feature. Codex/ChatGPT enforces 5-hour and weekly message ceilings. Surfacing them turns the Codex section from a retrospective token count into a live "am I about to be throttled" gauge, matching what the Claude section already does.

**The blocker:** Unlike Claude, we do not yet know the exact Codex usage endpoint. It must be captured once from the user's browser. **Do not guess the URL** (the user's house rule #5). This phase has a human step; everything after it is deterministic.

**Files:**
- Create: `docs/codex-endpoint-capture.md` (instructions for the user)
- Create: `codex_limits.py`
- Create: `tests/test_codex_limits.py`
- Modify: `plugins/claude-usage.5m.sh` (render Codex limits when available)

- [ ] **Step 5.1: Write the capture instructions for the user**

Create `docs/codex-endpoint-capture.md`:

```markdown
# Capturing the Codex usage endpoint (one-time, ~2 minutes)

We need the exact URL Codex/ChatGPT calls to report your usage limits.

1. Open https://chatgpt.com in Chrome (or your usual browser), logged in.
2. Open DevTools: Cmd+Opt+I, click the **Network** tab.
3. In the filter box type: usage   (or: limit, rate)
4. Navigate to your ChatGPT settings / usage area, or reload, so requests flow.
5. Look for a JSON request whose response contains your plan usage / limits.
   Likely hosts: chatgpt.com/backend-api/... or api.openai.com/...
6. Right-click that request -> Copy -> Copy as cURL.
7. Paste the whole cURL command back to the assistant.

The assistant will extract the URL, the required headers, and how to
authenticate (the reused session, or the token already in ~/.codex/auth.json),
and fill them into codex_limits.py. Nothing is guessed until you provide this.
```

- [ ] **Step 5.2: STOP and get the capture**

Present `docs/codex-endpoint-capture.md` to the user and wait for them to paste the cURL. Do not proceed until you have a real endpoint URL and its auth method. If the user cannot capture it, mark Phase 5 blocked and stop; Phases 1-4 stand on their own.

- [ ] **Step 5.3: Write the parser test against a captured-shape fixture**

Once you have the real response shape from the capture, create `tests/test_codex_limits.py` using the ACTUAL field names from the captured JSON. Template (replace the fixture with the real shape and adjust `parse` field access accordingly):

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_limits  # noqa: E402

# REPLACE this fixture with the real captured response body.
FIXTURE = {
    "primary_used_percent": 42,
    "primary_reset_after_seconds": 7200,
    "secondary_used_percent": 88,
    "secondary_reset_after_seconds": 320400,
}


def test_parse_windows():
    out = codex_limits.parse(FIXTURE)
    assert out["available"] is True
    labels = [w["label"] for w in out["windows"]]
    assert "5h" in labels[0]
    assert out["windows"][0]["percent"] == 42
    assert out["windows"][1]["percent"] == 88
```

- [ ] **Step 5.4: Implement `codex_limits.py`**

Create `codex_limits.py`. The skeleton below handles auth via the token already in `~/.codex/auth.json` (an OAuth access token) OR a captured cookie, whichever the capture showed. **Fill `USAGE_URL`, `HEADERS`, and the `parse()` field mapping from the captured cURL.** Keep the `parse()` pure so the test drives it.

```python
#!/usr/bin/env python3
"""Fetch Codex/ChatGPT usage limits and print them in the limit-source contract shape.

Endpoint, headers, and field mapping come from a one-time browser capture
(see docs/codex-endpoint-capture.md). parse() is pure and unit-tested; the
network wrapper mirrors the last good result to a summary file.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

AUTH_PATH = Path.home() / ".codex" / "auth.json"
SUMMARY_PATH = Path.home() / ".claude-usage-codex-limits-summary.json"
TIMEOUT_SECONDS = 12

# FILL FROM CAPTURE:
USAGE_URL = "REPLACE_WITH_CAPTURED_URL"
# Static headers from the capture (do NOT include the auth token here;
# it is injected below from auth.json so no secret is committed).
HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
}


def bearer_token() -> str | None:
    try:
        data = json.loads(AUTH_PATH.read_text())
        return data.get("tokens", {}).get("access_token")
    except Exception:
        return None


def parse(body: dict) -> dict:
    """Map the captured response body to the limit-source contract.

    REPLACE the field names below with the real ones from the capture.
    """
    def secs_to_iso(seconds):
        if seconds is None:
            return None
        import datetime
        return (datetime.datetime.now().astimezone()
                + datetime.timedelta(seconds=int(seconds))).isoformat()

    windows = []
    if "primary_used_percent" in body:
        windows.append({
            "label": "Codex (5h)",
            "percent": int(body["primary_used_percent"]),
            "resets_at": secs_to_iso(body.get("primary_reset_after_seconds")),
        })
    if "secondary_used_percent" in body:
        windows.append({
            "label": "Codex (weekly)",
            "percent": int(body["secondary_used_percent"]),
            "resets_at": secs_to_iso(body.get("secondary_reset_after_seconds")),
        })
    return {"available": bool(windows), "windows": windows}


def fetch() -> dict:
    if USAGE_URL.startswith("REPLACE"):
        return {"available": False, "reason": "endpoint not captured yet"}
    from curl_cffi import requests
    headers = dict(HEADERS)
    tok = bearer_token()
    if tok:
        headers["authorization"] = f"Bearer {tok}"
    try:
        resp = requests.get(USAGE_URL, headers=headers, impersonate="chrome", timeout=TIMEOUT_SECONDS)
    except Exception as e:
        return {"available": False, "reason": f"http error: {e}"}
    if resp.status_code != 200:
        return {"available": False, "reason": f"http {resp.status_code}"}
    try:
        return parse(json.loads(resp.text))
    except Exception as e:
        return {"available": False, "reason": f"parse error: {e}"}


if __name__ == "__main__":
    result = fetch()
    if result.get("available"):
        try:
            tmp = SUMMARY_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(result))
            os.replace(tmp, SUMMARY_PATH)
        except Exception:
            pass
    sys.stdout.write(json.dumps(result))
```

- [ ] **Step 5.5: Run the parser test**

Run: `.venv/bin/pytest tests/test_codex_limits.py -q`
Expected: `1 passed` (against the fixture matching the captured shape).

- [ ] **Step 5.6: Smoke-test the live call**

Run: `.venv/bin/python codex_limits.py | .venv/bin/python -m json.tool`
Expected: `{"available": true, "windows": [...]}` with real percentages. If it returns `{"available": false, ...}`, the URL/headers/auth need adjusting against the capture; iterate until live data returns.

- [ ] **Step 5.7: Render Codex limits in the plugin**

Add a path constant near the other Codex constants: `CODEX_LIMITS="$WIDGET_DIR/codex_limits.py"` and `CODEX_LIMITS_SUMMARY="$HOME/.claude-usage-codex-limits-summary.json"`.

In the `# --- Codex (local token usage)` section, at the TOP of the `if [ -n "$CODEX_JSON" ] ...` block (right after the `CODEX ·` header line), add a limits sub-block:

```bash
  CXL_JSON=$(/usr/bin/timeout 12 "$PYTHON" "$CODEX_LIMITS" 2>/dev/null)
  if [ -z "$CXL_JSON" ] && [ -f "$CODEX_LIMITS_SUMMARY" ]; then
    CXL_JSON=$(cat "$CODEX_LIMITS_SUMMARY")
  fi
  if [ -n "$CXL_JSON" ] && echo "$CXL_JSON" | jq -e '.available == true' >/dev/null 2>&1; then
    echo "$CXL_JSON" | jq -r '.windows[] | "\(.label)\t\(.percent)"' | while IFS=$'\t' read -r lbl pct; do
      clr=$(color_for_pct "$pct")
      echo "$lbl · ${pct}% | size=12 color=$clr"
      echo "$(progress_bar "$pct") | font=Menlo size=12 color=$clr"
    done
  fi
```

- [ ] **Step 5.8: Verify live render**

Run: `bash plugins/claude-usage.5m.sh`
Expected: the Codex section now shows `Codex (5h) · N%` and `Codex (weekly) · N%` rows with bars, above the local token line. Confirm the rest of the widget is unchanged.

- [ ] **Step 5.9: Commit Phase 5**

```bash
git add docs/codex-endpoint-capture.md codex_limits.py tests/test_codex_limits.py plugins/claude-usage.5m.sh
git commit -m "$(printf 'feat: Codex web rate-limits (5h + weekly ceilings)\n\ncodex_limits.py fetches ChatGPT/Codex usage limits (endpoint captured\nonce from the browser, see docs/codex-endpoint-capture.md) using the\nOAuth token already in ~/.codex/auth.json, and renders them in the Codex\nsection as percentage bars. Pure parse() is unit-tested; degrades to the\nlocal-token view when the endpoint is unreachable.\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>')"
```

---

## Part 4 - Definition of done / final verification

Run all of these from the repo root and confirm each:

```bash
# 1. All bash tests pass
bash tests/test_format.sh          # expect: 0 failed
bash tests/test_history.sh         # expect: 0 failed

# 2. All Python tests pass
.venv/bin/pytest tests/ -q         # expect: all passed

# 3. Plugin has no syntax errors and renders in under ~1.5s
bash -n plugins/claude-usage.5m.sh && time bash plugins/claude-usage.5m.sh

# 4. The three original sections still render (regression guard)
bash plugins/claude-usage.5m.sh | grep -E "Session · 5h|CLAUDE CODE|CODEX"

# 5. Nothing writes a secret to a log or a tracked file
git grep -nE "sk-ant|access_token" -- . ':!*.md'   # expect: no matches in code/config

# 6. Live widget updates
open "swiftbar://refreshallplugins"
```

Definition of done: Phases 1-4 complete and committed; Phase 5 complete if the user provided the capture, otherwise committed up to `docs/codex-endpoint-capture.md` and clearly flagged as blocked-on-user.

---

## Appendix - Quick reference for the executing agent

- **Repo root:** `~/Downloads/claude-usage-widget`
- **Python:** `.venv/bin/python` (never system python; the venv has curl_cffi + pycookiecheat)
- **Run the widget:** `bash plugins/claude-usage.5m.sh`
- **Refresh live:** `open "swiftbar://refreshallplugins"`
- **Bash version trap:** system bash is 3.2; no mapfile/flock/declare -A.
- **Commit trailer:** `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- **Golden rule:** every task ends green (tests pass + widget still renders) before the next begins.
