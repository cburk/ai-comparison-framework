"""Offline checks for the Claude-backed bulk_read MCP server (fake `claude`, no API calls)."""

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).parent.parent / "shunt-claude" / "bulk_read_mcp.py"

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, sys
a = sys.argv
prompt = sys.stdin.read()
import os
assert os.environ["DISABLE_PROMPT_CACHING"] == "1"
assert a[a.index("--model") + 1] == "claude-haiku-5-5"
assert a[a.index("--tools") + 1] == "" and "--strict-mcp-config" in a
ok = "FAIL" not in prompt
print(json.dumps({"is_error": not ok, "result": f"seen {'secret.py' in prompt}" if ok else "boom",
                  "total_cost_usd": 0.00063,
                  "usage": {"input_tokens": 600, "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 400, "output_tokens": 100}}))
sys.exit(0 if ok else 3)
"""


@pytest.fixture
def server(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SHUNT_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("SHUNT_LOG", str(tmp_path / "usage.jsonl"))
    spec = importlib.util.spec_from_file_location("bulk_read_mcp", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bulk_read_inlines_files_cleans_refs_and_logs(server, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "secret.py").write_text("X = 1\n")
    (tmp_path / "src" / ".hidden").write_text("nope")
    out = server.bulk_read([str(tmp_path / "src")], "what is X?")
    assert out == "seen True"
    entry = json.loads((tmp_path / "usage.jsonl").read_text())
    assert entry["ok"] and entry["input_tokens"] == 600
    assert entry["usd"] == pytest.approx(0.00063)  # taken from claude's own total_cost_usd
    assert ".hidden" not in server.build_corpus([str(tmp_path / "src")])


def test_oversize_and_failure_are_errors(server, tmp_path, monkeypatch):
    big = tmp_path / "big.txt"
    big.write_text("x" * 100)
    monkeypatch.setattr(server, "MAX_BYTES", 50)
    with pytest.raises(ValueError, match="Narrow"):
        server.bulk_read([str(big)], "q")
    monkeypatch.setattr(server, "MAX_BYTES", 10_000)
    big.write_text("FAIL")
    with pytest.raises(RuntimeError, match="exited 3: boom"):
        server.bulk_read([str(big)], "q")


def test_mcp_protocol(server, tmp_path):
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "shunt"
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools["result"]["tools"][0]["name"] == "bulk_read"
    f = tmp_path / "secret.py"
    f.write_text("1")
    call = {"name": "bulk_read", "arguments": {"paths": [str(f)], "question": "q"}}
    ok = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": call})
    assert not ok["result"]["isError"]
    call["arguments"]["paths"] = [str(tmp_path / "missing.py")]
    err = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": call})
    # an unreadable path is passed to the delegate as a note rather than failing the call
    assert not err["result"]["isError"] and "seen False" in err["result"]["content"][0]["text"]


def test_stdio_roundtrip(tmp_path):
    msgs = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
    r = subprocess.run([sys.executable, str(SERVER)], input=msgs, capture_output=True, text=True)
    assert json.loads(r.stdout)["result"]["tools"][0]["name"] == "bulk_read"
