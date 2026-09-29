"""Plan/execute/review (3 fresh sessions linked by .md files) vs one agent doing it all.

    inspect eval aicmp/tasks/plan_execute_review.py -T variant=single      --model <m> --epochs 5
    inspect eval aicmp/tasks/plan_execute_review.py -T variant=three_phase --model <m> --epochs 5

Correctness is scored by hidden pytest tests derived from the acceptance criteria in TASK.md.
Uses the Docker sandbox in aicmp/docker (set AICMP_SANDBOX=local to run on the host instead).
"""

import re
from typing import Literal

from inspect_ai import Task, task
from inspect_ai.agent import Agent, agent, react, run
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, accuracy, mean, scorer
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.tool import bash
from inspect_ai.util import sandbox

from aicmp.common import DATA_DIR, files_from_dir, python_cmd, sandbox_spec

ROOT = DATA_DIR / "plan_execute_review"

SINGLE_PROMPT = (
    "Read TASK.md in the current directory and implement bucket.py so it satisfies the "
    "specification and every acceptance criterion. Write and run your own tests to verify "
    "your work. Use bash for all file access."
)

PHASES = [
    (
        "planner",
        "You are the PLANNER. Read TASK.md and the stub bucket.py. Do NOT write the "
        "implementation. Write PLAN.md containing: the design, the exact algorithm/state, "
        "every edge case implied by each acceptance criterion, and a test plan. A separate "
        "engineer with no other context will implement from PLAN.md and TASK.md.",
    ),
    (
        "executor",
        "You are the EXECUTOR. Read TASK.md and PLAN.md (written by a planner), then implement "
        "bucket.py accordingly. Write tests in test_bucket_mine.py and run them until they pass. "
        "Write NOTES.md with a short summary of what you built and anything you are unsure about.",
    ),
    (
        "reviewer",
        "You are the REVIEWER. Read TASK.md, PLAN.md, NOTES.md and bucket.py. Check the "
        "implementation against EVERY acceptance criterion, actively looking for bugs and "
        "unhandled edge cases (run code to confirm). Write REVIEW.md with your findings, and "
        "fix any defects directly in bucket.py. Finish with all tests passing.",
    ),
]


@agent
def worker(role: str, prompt: str) -> Agent:
    return react(name=role, prompt=prompt, tools=[bash(timeout=120)])


@solver
def three_phase():
    """Three independent sessions; context flows only through files in the sandbox."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        last = None
        for role, prompt in PHASES:
            last = await run(worker(role, prompt), input="Begin.", name=role)
        if last is not None:
            state.messages = last.messages
            state.output = last.output
        return state

    return solve


@scorer(metrics=[accuracy(), mean()])
def hidden_tests():
    """Fraction of hidden tests passing; metadata records whether all passed."""

    async def score(state: TaskState, target: Target) -> Score:
        sb = sandbox()
        await sb.write_file(
            "hidden_tests/test_bucket.py", (ROOT / "hidden_tests" / "test_bucket.py").read_text()
        )
        res = await sb.exec(
            [python_cmd(), "-m", "pytest", "hidden_tests", "-q", "--tb=no", "-p", "no:cacheprovider"],
            env={"PYTHONPATH": "."},
            timeout=120,
        )
        return parse_pytest(res.stdout + res.stderr)

    return score


def parse_pytest(output: str) -> Score:
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", output)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", output)) else 0
    errors = int(m.group(1)) if (m := re.search(r"(\d+) errors?", output)) else 0
    total = passed + failed + errors
    return Score(
        value=passed / total if total else 0.0,
        answer=f"{passed}/{total} passed",
        explanation=output[-1500:],
        metadata={"passed": passed, "total": total, "all_passed": total > 0 and passed == total},
    )


@task
def plan_execute_review(
    variant: Literal["single", "three_phase"] = "single", message_limit: int = 80
):
    sample = Sample(
        input=SINGLE_PROMPT if variant == "single" else "Begin.",
        target="all hidden tests pass",
        files=files_from_dir(ROOT / "workspace"),
    )
    solver_ = (
        react(prompt=SINGLE_PROMPT, tools=[bash(timeout=120)])
        if variant == "single"
        else three_phase()
    )
    return Task(
        dataset=[sample],
        solver=solver_,
        scorer=hidden_tests(),
        sandbox=sandbox_spec(),
        message_limit=message_limit,
        metadata={"variant": variant},
    )
