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
      "all_time": { "total_tokens": <int>, ... }
    }

KNOWN WART (do not "fix" without updating the plugin): the two existing local
sources use different token keys. claude_code_usage.py uses `total_tokens`;
codex_usage.py uses `tokens` (plus `threads`). The plugin reads the correct key
per source. New local sources should use `total_tokens` to match this contract.

## Limit/quota sources (fetch_usage.py, future codex_limits.py)

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
