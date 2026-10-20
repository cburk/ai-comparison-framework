#!/usr/bin/env python3
"""Two-line Claude Code status line for the Shunt demo.

Line 1: main-model context fill, tokens and session cost (from Claude Code's stdin JSON).
Line 2: what the shunt offloaded to the cheap reader (from the shunt usage log).

Enable for one session without touching settings files:
    claude --settings shunt-claude/demo/settings.demo.json
"""

import json
import os
import sys
from pathlib import Path

LOG = Path(os.environ.get("SHUNT_LOG", "~/.claude/shunt/usage.jsonl")).expanduser()
DIM, GREEN, YELLOW, RED, CYAN, RESET = "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[0m"


def k(n: float) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 1e3 else f"{int(n)}"


def main() -> None:
    try:
        d = json.load(sys.stdin)
    except json.JSONDecodeError:
        d = {}
    cw = d.get("context_window") or {}
    pct = cw.get("used_percentage") or 0
    size = cw.get("context_window_size") or 0
    cur = cw.get("current_usage") or {}
    in_ctx = (cur.get("input_tokens") or 0) + (cur.get("cache_read_input_tokens") or 0) + (
        cur.get("cache_creation_input_tokens") or 0)
    color = GREEN if pct < 40 else YELLOW if pct < 70 else RED
    filled = int(pct / 100 * 20)
    bar = "█" * filled + "░" * (20 - filled)
    cost = (d.get("cost") or {}).get("total_cost_usd") or 0
    model = (d.get("model") or {}).get("display_name", "?")
    print(f"{CYAN}{model}{RESET} {color}{bar} {pct:.0f}%{RESET} "
          f"{k(in_ctx)}/{k(size)} ctx · out {k(cw.get('total_output_tokens') or 0)} · "
          f"{GREEN}${cost:.3f}{RESET} main")

    calls = tok_in = tok_out = 0
    usd = 0.0
    try:
        for line in LOG.read_text().splitlines():
            e = json.loads(line)
            if not e.get("ok"):
                continue
            calls += 1
            tok_in += (e.get("input_tokens", 0) + e.get("cache_read_tokens", 0)
                       + e.get("cache_write_tokens", 0))
            tok_out += e.get("output_tokens", 0)
            usd += e.get("usd") or 0
    except (OSError, json.JSONDecodeError):
        pass
    if calls:
        print(f"{DIM}shunt{RESET} {calls} bulk_read · {YELLOW}{k(tok_in)}{RESET} tokens kept out of "
              f"main context (reader returned {k(tok_out)}) · {GREEN}${usd:.4f}{RESET} reader")
    else:
        print(f"{DIM}shunt  no bulk_read calls yet{RESET}")


if __name__ == "__main__":
    main()
