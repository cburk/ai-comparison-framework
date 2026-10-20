#!/usr/bin/env bash
# Interactive demo launcher: real Claude Code in a repo, with a live two-line token/cost status line.
#
#   shunt-claude/demo/live.sh ../tracking shunt     # hook + bulk_read (Haiku) + offload guidance
#   shunt-claude/demo/live.sh ../tracking strict    # hook only, no guidance; counts paging + a 1200-line read budget
#   shunt-claude/demo/live.sh ../tracking direct    # stock Claude Code, same status line
#
# Nothing is written into the repo: config lives in a temp dir passed with --settings/--mcp-config.
# The shunt usage log is per launch, so the second status line starts at zero.
set -euo pipefail
repo="$(cd "${1:?usage: live.sh <repo> shunt|direct}" && pwd)"
mode="${2:-shunt}"
demo="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$demo/../.." && pwd)"
tmp="$(mktemp -d /tmp/shunt-live.XXXXXX)"
export SHUNT_LOG="$tmp/usage.jsonl"; : > "$SHUNT_LOG"
[ "$mode" = strict ] && export SHUNT_TRACK_PAGING=1 SHUNT_SESSION_LINES="${SHUNT_SESSION_LINES:-1200}"
export SHUNT_DELEGATE_MODEL="${SHUNT_DELEGATE_MODEL:-claude-haiku-5-5}"

python3 - "$tmp" "$demo" "$root" "$mode" <<'PY'
import json, sys
tmp, demo, root, mode = sys.argv[1:]
settings = {"statusLine": {"type": "command", "command": f"python3 {demo}/statusline.py", "refreshInterval": 2}}
mcp = {"mcpServers": {}}
if mode in ("shunt", "strict"):
    settings["hooks"] = {"PreToolUse": [{"matcher": "Read|Bash", "hooks": [
        {"type": "command", "command": f'python3 "{root}/aicmp/shunt/claude/hooks/shunt_check.py"'}]}]}
    settings["permissions"] = {"allow": ["mcp__shunt__bulk_read"]}
    mcp["mcpServers"]["shunt"] = {"type": "stdio", "command": "python3", "args": [f"{root}/shunt-claude/bulk_read_mcp.py"]}
json.dump(settings, open(f"{tmp}/settings.json", "w"))
json.dump(mcp, open(f"{tmp}/mcp.json", "w"))
PY

args=(--settings "$tmp/settings.json" --strict-mcp-config --mcp-config "$tmp/mcp.json")
[ "$mode" = shunt ] && args+=(--append-system-prompt "$(cat "$demo/guidance.txt")")
echo "mode=$mode repo=$repo log=$SHUNT_LOG  (try /context and /usage too)"
cd "$repo" && exec claude "${args[@]}"
