#!/usr/bin/env python3
"""A/B a real repo: stock Claude Code vs Claude Code + Shunt, same prompt, same main model.

    python3 shunt-claude/demo/run_ab.py --repo ../tracking --question save_flow
    python3 shunt-claude/demo/run_ab.py --repo ../tracking --question gateway_docs --runs 2

Nothing is written into the repo. The shunt is injected with --settings (hook + allow rule)
and --mcp-config, and both arms run read-only (Edit/Write disallowed, Bash limited to
read commands) with --strict-mcp-config so the only MCP difference is the shunt.

Reports, per run: main-model cost and tokens, peak context, turns, wall time, bulk_read calls
and hook blocks, delegate cost (from the shunt's usage log), total $, and a check that the
answer names the files/functions on the real call path.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
HOOK = REPO_ROOT / "aicmp" / "shunt" / "claude" / "hooks" / "shunt_check.py"
SERVER = HERE.parent / "bulk_read_mcp.py"

READ_ONLY = (
    "Do not modify, create or delete any files. Do not run the application or tests. "
    "Use only source code under cmd/, internal/ and lambdas/. Do not read .wiki, docs, "
    ".planning, README or *_test.go files."
)
QUESTIONS = {
    "save_flow": {
        "prompt": (
            "Trace the flow of a MacroPoint tracking-map save end to end in this Go service: "
            "start at the entry point where a tracking request for a MacroPoint carrier is "
            "received, follow it through each function and package, and end where the row is "
            "persisted. For each hop give file:line and what it does, and call out validation "
            "and failure handling (dedup, error classification, retries, what happens when the "
            "vendor call fails). " + READ_ONLY + " Finish with a numbered list of hops."
        ),
        # Real path, checked against the code: SQS wiring -> handler -> service -> repository.
        "must_mention": [
            "cmd/macropoint/main.go", "RequestedHandler", "ProcessRequest",
            "persistCreatedOrder", "UpsertTrackingMap", "CreateOrder", "handleVendorFailure",
        ],
    },
    "gateway_docs": {
        "prompt": (
            "Write a developer-facing reference doc (markdown, in your answer; do not create "
            "files) for the MacroPoint gateway in internal/macropoint-gateway: its purpose, what "
            "each file in service/, repository/ and macropointapi/ is responsible for, how the "
            "layers call each other, the error-handling conventions, and what a new engineer "
            "must know before changing it. " + READ_ONLY
        ),
        "must_mention": [
            "requested.go", "update.go", "cancel.go", "autocomplete.go", "failure.go",
            "tracking_map_repository.go", "client.go", "UpsertTrackingMap",
        ],
    },
}

# The "skills" layer of Shunt: tells the model when to offload. Injected only in the guided arm.
GUIDANCE = (
    "Large-read policy: for questions that span several files or need whole-file understanding "
    "(tracing a flow, writing docs, auditing a package), first call mcp__shunt__bulk_read with the "
    "relevant directory or file paths and a specific question. A cheaper model reads them in full "
    "and returns a short answer with file:line references. Then use Read or Grep only to verify "
    "the specific lines you will cite. Do not page through large files with Read."
)

READ_TOOLS = (
    "Read,Grep,Glob,Bash(grep:*),Bash(rg:*),Bash(sed:*),Bash(ls:*),Bash(wc:*),Bash(find:*),"
    "Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(awk:*)"
)


def build_configs(out: Path) -> tuple[Path, Path, Path]:
    shunt_settings = out / "shunt-settings.json"
    shunt_settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "Read|Bash", "hooks": [
            {"type": "command", "command": f'python3 "{HOOK}"'}]}]},
    }))
    shunt_mcp = out / "shunt-mcp.json"
    shunt_mcp.write_text(json.dumps({"mcpServers": {"shunt": {
        "type": "stdio", "command": "python3", "args": [str(SERVER)]}}}))
    empty_mcp = out / "empty-mcp.json"
    empty_mcp.write_text(json.dumps({"mcpServers": {}}))
    return shunt_settings, shunt_mcp, empty_mcp


def run_one(arm: str, n: int, q: dict, args, cfg, out: Path) -> dict:
    settings, shunt_mcp, empty_mcp = cfg
    log = out / f"{arm}-{n}.shunt-usage.jsonl"
    log.unlink(missing_ok=True)
    allowed = READ_TOOLS + (",mcp__shunt__bulk_read" if arm != "direct" else "")
    cmd = [
        "claude", "-p", q["prompt"], "--model", args.model, "--output-format", "stream-json",
        "--verbose", "--max-budget-usd", str(args.max_budget), "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", str(empty_mcp if arm == "direct" else shunt_mcp),
        "--allowedTools", allowed, "--disallowedTools", "Edit,Write,NotebookEdit",
    ]
    if arm in ("shunt", "guided", "strict"):
        cmd += ["--settings", str(settings)]
    if arm == "guided":
        cmd += ["--append-system-prompt", GUIDANCE]
    env = {**os.environ, "SHUNT_LOG": str(log), "SHUNT_DELEGATE_MODEL": args.delegate}
    if arm == "strict":  # hook-only (no guidance), but paging and cumulative reads are counted
        env.update(SHUNT_TRACK_PAGING="1", SHUNT_SESSION_LINES=str(args.session_lines))
    started = time.time()
    proc = subprocess.run(cmd, cwd=args.repo, env=env, capture_output=True, text=True)
    (out / f"{arm}-{n}.stream.jsonl").write_text(proc.stdout)

    tools: dict[str, int] = {}
    blocks = peak = 0
    result: dict = {}
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            msg = ev["message"]
            u = msg.get("usage", {})
            peak = max(peak, u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0))
            for c in msg["content"]:
                if c["type"] == "tool_use":
                    tools[c["name"]] = tools.get(c["name"], 0) + 1
        elif ev.get("type") == "user":
            for c in ev["message"]["content"]:
                if isinstance(c, dict) and c.get("type") == "tool_result" and "Blocked:" in str(
                        c.get("content")):
                    blocks += 1
        elif ev.get("type") == "result":
            result = ev
    answer = result.get("result", "") or ""
    (out / f"{arm}-{n}.answer.md").write_text(answer)
    u = result.get("usage", {})
    delegate = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    main_usd = result.get("total_cost_usd") or 0.0
    delegate_usd = sum(d.get("usd") or 0 for d in delegate)
    return {
        "arm": arm, "run": n, "ok": not result.get("is_error", True),
        "main_usd": main_usd, "delegate_usd": delegate_usd, "total_usd": main_usd + delegate_usd,
        "turns": result.get("num_turns"), "seconds": round(time.time() - started),
        "input_tokens": u.get("input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0), "peak_context": peak,
        "bulk_read_calls": tools.get("mcp__shunt__bulk_read", 0), "hook_blocks": blocks,
        "delegate_tokens": sum(d.get("input_tokens", 0) for d in delegate),
        "tools": tools, "denials": len(result.get("permission_denials", [])),
        "missing": [t for t in q["must_mention"] if t not in answer],
        "refs": check_refs(answer, args.repo),
    }


def check_refs(answer: str, repo: str) -> tuple[int, int]:
    """(valid, total) file:line references. A bare filename resolves to any same-named file in
    the repo; a reference is valid if some candidate has that line."""
    by_name: dict[str, list[Path]] = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".cache", "gen")]
        for name in files:
            by_name.setdefault(name, []).append(Path(root) / name)
    valid = total = 0
    for path, line in set(re.findall(r"([\w./-]+\.(?:go|proto|ts|yaml|yml)):(\d+)", answer)):
        total += 1
        direct = Path(repo) / path.lstrip("/").removeprefix(repo.lstrip("/")).lstrip("/")
        candidates = [direct] if direct.is_file() else by_name.get(Path(path).name, [])
        if any(1 <= int(line) <= len(c.read_text(errors="ignore").splitlines()) for c in candidates):
            valid += 1
    return valid, total


def table(rows: list[dict]) -> str:
    cols = [
        ("arm", "arm", "{}"), ("run", "#", "{}"), ("total_usd", "total $", "{:.3f}"),
        ("main_usd", "main $", "{:.3f}"), ("delegate_usd", "deleg $", "{:.4f}"),
        ("peak_context", "peak ctx", "{:,}"), ("cache_read", "cache rd", "{:,}"),
        ("output_tokens", "out tok", "{:,}"), ("turns", "turns", "{}"),
        ("seconds", "sec", "{}"), ("bulk_read_calls", "bulk", "{}"),
        ("hook_blocks", "blocks", "{}"), ("denials", "denied", "{}"),
    ]
    head = " | ".join(h for _, h, _ in cols) + " | refs ok | missing terms"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(" | ".join(f.format(r[k]) for k, _, f in cols)
                     + f" | {r['refs'][0]}/{r['refs'][1]}" + " | " + (", ".join(r["missing"]) or "none"))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--question", choices=QUESTIONS, default="save_flow")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--arms", default="direct,shunt,guided")
    ap.add_argument("--session-lines", type=int, default=1200,
                    help="strict arm: direct-read budget in lines before reads are blocked")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--delegate", default="claude-haiku-5-5")
    ap.add_argument("--max-budget", type=float, default=4.0, help="USD cap per run")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    args.repo = str(Path(args.repo).resolve())
    out = Path(args.out or HERE / "results" / f"{args.question}-{time.strftime('%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    cfg = build_configs(out)
    jobs = [(arm, n) for n in range(1, args.runs + 1) for arm in args.arms.split(",")]
    print(f"{args.question}: {len(jobs)} runs on {args.model}, delegate {args.delegate}",
          file=sys.stderr)
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        rows = list(pool.map(
            lambda j: run_one(j[0], j[1], QUESTIONS[args.question], args, cfg, out), jobs))
    (out / "results.json").write_text(json.dumps(rows, indent=2))
    print(table(rows))
    print(f"\nanswers + transcripts: {out}")


if __name__ == "__main__":
    main()
