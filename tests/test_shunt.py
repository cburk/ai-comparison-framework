"""Offline checks for the large_read task: corpus, Shunt hook, scorer, cost table."""

import ast
import json
import os
import subprocess
import uuid
import sys

import pytest

from aicmp.data.codebase_gen import MODULES, QUESTIONS, generate
from aicmp.report import COSTS
from aicmp.shunt import HERE as SHUNT_DIR
from aicmp.tasks.large_read import answer_line, check_answer

HOOK = SHUNT_DIR / "claude" / "hooks" / "shunt_check.py"
BIG = "ledgerline/payments/capture.py"


def run_hook(payload: dict, cwd, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), cwd=cwd,
        capture_output=True, text=True, env=None if env is None else {**os.environ, **env},
    )


def test_codebase_shape():
    out = generate()
    for rel in MODULES:
        text = (out / rel).read_text()
        ast.parse(text)
        assert text.count("\n") > 350  # every module triggers the hook
    all_code = "\n".join((out / m).read_text() for m in MODULES)
    for fact in ["ledger.payments.capture.retry.v2", '"capture.max_attempts": 9',
                 'RefundExceedsCharge: (422, "REFUND_OVER_CAPTURE")']:
        assert fact in all_code


@pytest.mark.parametrize(
    "payload, blocked",
    [
        ({"tool_name": "Read", "tool_input": {"file_path": BIG}}, True),
        ({"tool_name": "Read", "tool_input": {"file_path": BIG, "offset": 300, "limit": 80}}, False),
        ({"tool_name": "Read", "tool_input": {"file_path": BIG, "limit": 2000}}, True),
        ({"tool_name": "Read", "tool_input": {"file_path": "ledgerline/__init__.py"}}, False),
        ({"tool_name": "Bash", "tool_input": {"command": f"cat {BIG}"}}, True),
        ({"tool_name": "Bash", "tool_input": {"command": f"head -n 40 {BIG}"}}, False),
        ({"tool_name": "Bash", "tool_input": {"command": f"tail -2000 {BIG}"}}, True),
        ({"tool_name": "Bash", "tool_input": {"command": f"cat {BIG} | grep flag"}}, False),
        ({"tool_name": "Bash", "tool_input": {"command": "grep -rn register_flag ledgerline"}}, False),
        ({"tool_name": "Grep", "tool_input": {"pattern": "x"}}, False),
    ],
)
def test_hook(payload, blocked):
    r = run_hook(payload, generate())
    assert r.returncode == (2 if blocked else 0), r.stderr
    if blocked:
        assert "mcp__shunt__bulk_read" in r.stderr


def test_hook_settings_is_valid_json():
    s = json.loads((SHUNT_DIR / "claude" / "settings.json").read_text())
    assert s["hooks"]["PreToolUse"][0]["matcher"] == "Read|Bash"


def test_answer_scoring():
    t = {q["id"]: q["target"] for q in QUESTIONS}
    assert answer_line("blah\nANSWER: ledger.payments.capture.retry.v2, 9") == (
        "ledger.payments.capture.retry.v2, 9"
    )
    assert check_answer("ledger.payments.capture.retry.v2, 9", t["capture_retry"])[0]
    assert not check_answer("ledger.payments.capture.retry.v2, 6", t["capture_retry"])[0]
    assert not check_answer("ledger.payments.capture.retry.v2, 19", t["capture_retry"])[0]
    assert not check_answer("ledger.payments.capture.retry.v1, 9", t["capture_retry"])[0]
    assert check_answer("422, REFUND_OVER_CAPTURE", t["refund_over_capture"])[0]
    good = "instant_payouts, risk_v3_scoring, async_receipts"
    assert check_answer(good, t["enabled_flags"])[0]
    assert not check_answer(good + ", sms_fallback", t["enabled_flags"])[0]
    assert not check_answer("instant_payouts, risk_v3_scoring", t["enabled_flags"])[0]


def test_cost_table_loads_into_inspect():
    from inspect_ai.model._model_data.model_data import ModelCost
    from inspect_ai.model._model_info import set_model_cost

    for model, cost in COSTS.items():
        set_model_cost(model, ModelCost(**cost))


def test_correct_ignores_format_and_distractors():
    target = next(q["target"] for q in QUESTIONS if q["id"] == "refund_over_capture")
    writeup = (
        "**HTTP status code: `422`**, error code `REFUND_OVER_CAPTURE`. "
        "(Not REFUND_WINDOW_CLOSED, which is the expired-window case.)"
    )
    assert answer_line(writeup) == ""
    assert check_answer(writeup, target, forbid=False)[0]
    assert not check_answer(writeup, target)[0]


def read(path, **kw):
    return {"tool_name": "Read", "session_id": kw.pop("sid", "t"), "tool_input": {"file_path": path, **kw}}


def test_paging_is_blocked_only_when_tracked():
    cwd, run = generate(), uuid.uuid4().hex
    slices = [read(BIG, offset=i * 300, limit=300, sid=f"p-{run}") for i in range(3)]
    # stock behaviour: any slice <= limit passes, so paging a big file is never blocked
    assert all(run_hook(p, cwd).returncode == 0 for p in slices)
    env = {"SHUNT_TRACK_PAGING": "1"}
    codes = [run_hook(p, cwd, env).returncode for p in slices]
    assert codes == [0, 2, 2]  # 300 ok, 600 > 350 blocked
    # a different file is unaffected, and so is a small file
    assert run_hook(read("ledgerline/__init__.py", sid=f"p-{run}"), cwd, env).returncode == 0


def test_session_budget_blocks_further_mid_size_reads():
    cwd, run = generate(), uuid.uuid4().hex
    env = {"SHUNT_SESSION_LINES": "500", "SHUNT_SESSION_MIN_LINES": "100"}
    mid = "ledgerline/payments/capture.py"
    small = "ledgerline/__init__.py"
    r1 = run_hook(read(mid, offset=0, limit=300, sid=f"b-{run}"), cwd, env)   # 300 read
    r2 = run_hook(read(mid, offset=300, limit=300, sid=f"b-{run}"), cwd, env)  # 600 read, budget hit
    r3 = run_hook(read(mid, offset=600, limit=50, sid=f"b-{run}"), cwd, env)   # over budget
    assert (r1.returncode, r2.returncode, r3.returncode) == (0, 0, 2)
    assert "budget 500" in r3.stderr and "mcp__shunt__bulk_read" in r3.stderr
    # tiny files stay readable after the budget is spent
    assert run_hook(read(small, sid=f"b-{run}"), cwd, env).returncode == 0
    # budgets are per session
    assert run_hook(read(mid, offset=0, limit=50, sid=f"other-{run}"), cwd, env).returncode == 0
