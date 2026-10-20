#!/usr/bin/env python3
"""bulk_read: an MCP server that offloads large reads from Claude Code to a cheaper Claude model.

This is the "bulk-reader" half of Spotify's Shunt pattern. The main model (expensive) asks a
question about big files; a cheap model (Haiku 5.5 by default) reads them whole and returns a
short answer, so the files never enter the main model's context. The other half, the hook that
blocks direct large reads and points Claude here, is aicmp/shunt/claude/hooks/shunt_check.py.

Stdio MCP server, stdlib only (no `mcp` package). Each call shells out to headless Claude Code:

    claude -p --model claude-haiku-5-5 --tools "" --system-prompt <bulk-reader prompt> \
           --strict-mcp-config --setting-sources "" --output-format json     (files on stdin)

No tools, MCP servers or settings load in the child, so it can't recurse into this shunt, and
it runs in an empty temp directory. It uses whatever auth the `claude` CLI already has. Cost
and token usage come straight from Claude Code's JSON result and go to a JSONL log.

Env:
  SHUNT_DELEGATE_MODEL  model for the reader (default claude-haiku-5-5)
  SHUNT_EFFORT          reasoning effort for the reader (default low). At the default medium,
                        Haiku 5.5 spent 10-35K output tokens thinking and took 50-140s per call.
  SHUNT_CLAUDE_BIN      claude executable (default: claude)
  SHUNT_MAX_BYTES       refuse inputs larger than this (default 240000, ~95K tokens). Haiku 5.5
                        costs $0.10/$0.50 per MTok only up to 100K prompt tokens; above that the
                        whole prompt is billed at $0.50/$2.50, so one oversize call costs 5x.
  SHUNT_TIMEOUT         seconds per call (default 180)
  SHUNT_LOG             usage log path (default ~/.claude/shunt/usage.jsonl)
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MODEL = os.environ.get("SHUNT_DELEGATE_MODEL", "claude-haiku-5-5")
EFFORT = os.environ.get("SHUNT_EFFORT", "low")
CLAUDE = os.environ.get("SHUNT_CLAUDE_BIN", "claude")
MAX_BYTES = int(os.environ.get("SHUNT_MAX_BYTES", "240000"))
TIMEOUT = int(os.environ.get("SHUNT_TIMEOUT", "180"))
LOG = Path(os.environ.get("SHUNT_LOG", "~/.claude/shunt/usage.jsonl")).expanduser()

# The Shunt post's bulk-reader prompt, plus a line saying the files are inline.
SYSTEM_PROMPT = (
    "You are a precise code analyst. All the files you need are included in the user message. "
    "Read them and answer the question concisely. Output structured bullets only. Quote exact "
    "names, values and file paths with line numbers (each line is prefixed with its number and a "
    "tab) where they matter."
)

TOOL = {
    "name": "bulk_read",
    "description": (
        "Answer a question about large files without loading them into your context. A cheaper "
        "model reads the files in full and returns a concise, structured answer. Use this "
        "instead of reading large files directly. Ask specific questions and ask it to quote "
        "the exact names, values and line context you need."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "File or directory paths (directories are read recursively).",
            },
            "question": {"type": "string", "description": "The specific question to answer."},
        },
        "required": ["paths", "question"],
    },
}


def expand(path: str) -> list[Path]:
    p = Path(path).expanduser()
    if p.is_dir():
        return sorted(
            f
            for f in p.rglob("*")
            if f.is_file()
            and not any(part.startswith(".") for part in f.relative_to(p).parts)
            and f.suffix != ".pyc"
        )
    return [p]


def build_corpus(paths: list[str]) -> str:
    parts = []
    for path in paths:
        for f in expand(path):
            try:
                text = f.read_text()
            except (OSError, UnicodeDecodeError) as ex:
                text = f"(could not read: {type(ex).__name__})"
            # Numbered lines let the reader cite file:line accurately instead of guessing.
            numbered = "\n".join(f"{i}\t{ln}" for i, ln in enumerate(text.splitlines(), 1))
            parts.append(f'<file path="{f}">\n{numbered}\n</file>')
    return "\n".join(parts)


def log_usage(entry: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # logging must never break a read


def bulk_read(paths: list[str], question: str) -> str:
    corpus = build_corpus(paths)
    size = len(corpus.encode())
    if size > MAX_BYTES:
        raise ValueError(
            f"Input is {size:,} bytes (limit {MAX_BYTES:,}, set by SHUNT_MAX_BYTES). "
            f"Narrow the paths or split the question across calls."
        )
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="shunt-") as tmp:
        proc = subprocess.run(
            [
                CLAUDE, "-p", "--model", MODEL, "--tools", "", "--system-prompt", SYSTEM_PROMPT,
                "--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands",
                "--no-session-persistence", "--effort", EFFORT, "--output-format", "json",
            ],
            input=f"{corpus}\n\n<question>{question}</question>\n",
            cwd=tmp, capture_output=True, text=True, timeout=TIMEOUT,
            env={**os.environ, "DISABLE_PROMPT_CACHING": "1"},
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result = {}
    usage = result.get("usage", {})
    failed = proc.returncode != 0 or result.get("is_error") or not result.get("result")
    log_usage(
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model": MODEL,
            "ok": not failed,
            "input_bytes": size,
            "input_tokens": usage.get("input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "usd": result.get("total_cost_usd"),
            "seconds": round(time.time() - started, 1),
        }
    )
    if failed:
        detail = result.get("result") or proc.stderr.strip()[-500:] or proc.stdout[-500:]
        raise RuntimeError(f"claude exited {proc.returncode}: {detail}")
    return result["result"].strip()


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification (e.g. notifications/initialized): no reply
        return None
    if method == "initialize":
        result = {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "shunt", "version": "0.2.0"},
        }
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        args = msg["params"].get("arguments", {})
        try:
            text, is_error = bulk_read(args["paths"], args["question"]), False
        except Exception as ex:  # surfaced to Claude as a tool error, not a protocol error
            text, is_error = f"bulk_read failed: {ex}", True
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": method}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        reply = handle(json.loads(line))
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
