#!/usr/bin/env python3
"""Install the Shunt (hook + Claude-backed bulk_read) into a Claude Code setup.

    python3 shunt-claude/install.py                    # this project (.claude/settings.json, .mcp.json)
    python3 shunt-claude/install.py --scope user       # every project (~/.claude/settings.json)
    python3 shunt-claude/install.py --uninstall [--scope user]

Both pieces point at the files in this repo by absolute path, so nothing is copied and the repo
must stay where it is. Existing settings are merged, never overwritten.
"""

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "aicmp" / "shunt" / "claude" / "hooks" / "shunt_check.py"
SERVER = Path(__file__).resolve().parent / "bulk_read_mcp.py"
HOOK_CMD = f'python3 "{HOOK}"'
TOOL = "mcp__shunt__bulk_read"


def is_ours(entry: dict) -> bool:
    return any("shunt_check.py" in h.get("command", "") for h in entry.get("hooks", []))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["project", "user"], default="project")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    settings_path = (
        Path.home() / ".claude" / "settings.json"
        if args.scope == "user"
        else Path.cwd() / ".claude" / "settings.json"
    )
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}

    pre = [e for e in settings.get("hooks", {}).get("PreToolUse", []) if not is_ours(e)]
    allow = [a for a in settings.get("permissions", {}).get("allow", []) if a != TOOL]
    if not args.uninstall:
        pre.append(
            {"matcher": "Read|Bash", "hooks": [{"type": "command", "command": HOOK_CMD}]}
        )
        allow.append(TOOL)
    for keys, value in ((("hooks", "PreToolUse"), pre), (("permissions", "allow"), allow)):
        section = settings.setdefault(keys[0], {})
        if value:
            section[keys[1]] = value
        else:
            section.pop(keys[1], None)
        if not section:
            settings.pop(keys[0])
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"{'removed from' if args.uninstall else 'updated'} {settings_path}")

    subprocess.run(["claude", "mcp", "remove", "shunt", "--scope", args.scope], capture_output=True)
    if not args.uninstall:
        subprocess.run(
            ["claude", "mcp", "add", "--scope", args.scope, "shunt", "--", "python3", str(SERVER)],
            check=True,
        )
    print("Restart Claude Code, then run /mcp to confirm 'shunt' is connected.")


if __name__ == "__main__":
    main()
