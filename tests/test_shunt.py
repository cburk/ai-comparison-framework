"""Offline checks for the large_read task: corpus, Shunt hook, scorer, cost table."""

import ast
import json
import subprocess
import sys

import pytest

from aicmp.data.codebase_gen import MODULES, QUESTIONS, generate
from aicmp.report import COSTS
from aicmp.shunt import HERE as SHUNT_DIR
from aicmp.tasks.large_read import answer_line, check_answer

HOOK = SHUNT_DIR / "claude" / "hooks" / "shunt_check.py"
BIG = "ledgerline/payments/capture.py"


def run_hook(payload: dict, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), cwd=cwd,
        capture_output=True, text=True,
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
