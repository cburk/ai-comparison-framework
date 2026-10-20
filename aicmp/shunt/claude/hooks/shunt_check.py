#!/usr/bin/env python3
"""PreToolUse hook: block direct reads of large files and point at bulk_read instead.

Replicates the "check-file-size" and "check-bash-read" hooks from the Shunt plugin:
- Read of a file over SHUNT_MIN_LINES lines is blocked, unless it passes an explicit
  `limit` <= SHUNT_MIN_LINES (a targeted slice).
- Bash `cat`/`less`/`more` of a large file is blocked, as is `head`/`tail` without an
  explicit -n <= SHUNT_MIN_LINES. Piped commands pass through as targeted reads, and so
  do grep/rg/sed/awk.

Two opt-in stateful checks (off by default; they close gaps in the stock per-file rule, which
a model can sidestep by paging through a big file in slices or by reading many mid-size files):
- SHUNT_TRACK_PAGING=1: slices of one large file count together, so paging through it in
  `limit` <= SHUNT_MIN_LINES chunks is blocked once the total passes SHUNT_MIN_LINES.
- SHUNT_SESSION_LINES=N: once N lines have been read directly with Read this session, further
  reads of files over SHUNT_SESSION_MIN_LINES (default 100) are blocked.
State is kept per session_id in the temp directory.

Claude Code contract: tool call JSON on stdin; exit 2 blocks the call and feeds stderr
back to the model; exit 0 allows it.
"""

import json
import os
import shlex
import sys
import tempfile

MIN_LINES = int(os.environ.get("SHUNT_MIN_LINES", "350"))
BULK_TOOL = "mcp__shunt__bulk_read"
TRACK_PAGING = os.environ.get("SHUNT_TRACK_PAGING") == "1"
SESSION_LINES = int(os.environ.get("SHUNT_SESSION_LINES", "0"))
SESSION_MIN_LINES = int(os.environ.get("SHUNT_SESSION_MIN_LINES", "100"))
READ_CMDS = {"cat", "less", "more", "head", "tail"}


def line_count(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def block(path: str, lines: int, why: str | None = None) -> None:
    detail = f" ({why})" if why else ""
    print(
        f"Blocked: {path} has {lines} lines (limit {MIN_LINES}){detail}. Don't read large files "
        f"into your context. Call the {BULK_TOOL} tool with the file or directory path(s) "
        f"and a specific question; a cheaper model reads them and returns a concise answer. "
        f"For a small targeted slice, use Read with offset and limit <= {MIN_LINES}, or Grep.",
        file=sys.stderr,
    )
    sys.exit(2)


def load_state(session: str) -> tuple[str, dict]:
    path = os.path.join(tempfile.gettempdir(), f"shunt-state-{session}.json")
    try:
        with open(path) as f:
            return path, json.load(f)
    except (OSError, json.JSONDecodeError):
        return path, {"total": 0, "files": {}}


def check_read(tool_input: dict, session: str = "") -> None:
    limit = tool_input.get("limit")
    path = tool_input.get("file_path", "")
    n = line_count(path)
    stateful = bool(session) and (TRACK_PAGING or SESSION_LINES)
    sliced = limit is not None and int(limit) <= MIN_LINES
    if not stateful:
        if not sliced and n > MIN_LINES:
            block(path, n)
        return
    offset = int(tool_input.get("offset") or 0)
    read = min(int(limit), max(n - offset, 0)) if limit is not None else max(n - offset, 0)
    state_path, state = load_state(session)
    seen = state["files"].get(path, 0) + read
    if n > MIN_LINES and (not sliced or (TRACK_PAGING and seen > MIN_LINES)):
        block(path, n, f"{seen} lines of it requested so far this session" if sliced else None)
    if SESSION_LINES and state["total"] >= SESSION_LINES and n > SESSION_MIN_LINES:
        block(path, n, f"{state['total']} lines already read directly this session "
                       f"(budget {SESSION_LINES})")
    state["files"][path] = seen
    state["total"] += read
    with open(state_path, "w") as f:
        json.dump(state, f)


def check_bash(command: str) -> None:
    if "|" in command:
        return
    try:
        words = shlex.split(command)
    except ValueError:
        return
    if not words or os.path.basename(words[0]) not in READ_CMDS:
        return
    cmd, args = os.path.basename(words[0]), words[1:]
    if cmd in ("head", "tail"):
        n = _n_arg(args)
        if n is not None and n <= MIN_LINES:
            return
    for arg in args:
        if not arg.startswith("-") and os.path.isfile(arg):
            n = line_count(arg)
            if n > MIN_LINES:
                block(arg, n)


def _n_arg(args: list[str]) -> int | None:
    for i, a in enumerate(args):
        val = None
        if a in ("-n", "--lines") and i + 1 < len(args):
            val = args[i + 1]
        elif a.startswith("-n") and len(a) > 2:
            val = a[2:]
        elif a.startswith("--lines="):
            val = a.split("=", 1)[1]
        elif a[1:].isdigit() and a.startswith("-"):
            val = a[1:]
        if val is not None:
            try:
                return abs(int(val.lstrip("+")))
            except ValueError:
                return None
    return None


def main() -> None:
    data = json.load(sys.stdin)
    tool, tool_input = data.get("tool_name"), data.get("tool_input") or {}
    if tool == "Read":
        check_read(tool_input, data.get("session_id", ""))
    elif tool == "Bash":
        check_bash(tool_input.get("command", ""))


if __name__ == "__main__":
    main()
