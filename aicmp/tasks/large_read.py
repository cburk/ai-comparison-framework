"""Large-codebase Q&A: plain Claude Code vs Claude Code + a home-grown Shunt.

Tests the claim in Spotify's "Portal cut my Claude Code token usage by 90%" post, using
the real Claude Code CLI (via inspect_swe) on a synthetic 11k-line codebase:

- variant=direct: stock Claude Code.
- variant=shunt:  same, plus a PreToolUse hook that blocks reads of files over 350 lines
                  and a `bulk_read` tool that hands whole files to a cheap delegate model.

    inspect eval aicmp/tasks/large_read.py -T variant=direct --model anthropic/claude-sonnet-5
    inspect eval aicmp/tasks/large_read.py -T variant=shunt  --model anthropic/claude-sonnet-5 \\
        --model-role delegate=google/gemini-3.8-flash
    python -m aicmp.report logs/            # main-model vs delegate tokens and total $

The post measured only the main model's tokens. The report also prices the delegate's
tokens, since those are the whole point of the trade.
"""

import re
from typing import Literal

from inspect_ai import Task, task
from inspect_ai.agent import BridgedToolsSpec, as_solver
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, accuracy, mean, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import store
from inspect_swe import claude_code

from aicmp.common import files_from_dir, sandbox_spec
from aicmp.data.codebase_gen import PROMPT_TEMPLATE, QUESTIONS, generate
from aicmp.shunt import bulk_read, delegate_preflight, workspace_files


def answer_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip().upper().startswith("ANSWER:")]
    return lines[-1].split(":", 1)[1].strip() if lines else ""


def check_answer(answer: str, target: str, forbid: bool = True) -> tuple[bool, list[str]]:
    """Every term must appear as a whole token; '!'-prefixed terms must not appear.

    With forbid=False the '!' terms are skipped (for free-form text, which may mention the
    distractors while explaining why they don't apply).
    """
    problems = []
    for term in target.split("|"):
        forbidden = term.startswith("!")
        if forbidden and not forbid:
            continue
        term = term.lstrip("!")
        present = re.search(rf"(?<![\w.]){re.escape(term)}(?![\w])", answer, re.I) is not None
        if present == forbidden:
            problems.append(("unexpected " if forbidden else "missing ") + term)
    return not problems, problems


@scorer(metrics={"correct": [accuracy()], "strict": [accuracy()]})
def answer_matches():
    """Two scores per sample.

    - strict: the final ANSWER line has every required term and no forbidden one.
    - correct: the required facts were found. Checked on the ANSWER line if there is one,
      otherwise on the whole final message (Claude sometimes ends with a write-up instead
      of the requested line). Can be fooled by a write-up that mentions the right value
      while concluding a wrong one, so spot-check samples where the two scores differ.
    """

    async def score(state: TaskState, target: Target) -> Score:
        completion = state.output.completion
        answer = answer_line(completion)
        ok, problems = check_answer(answer, target.text)
        found, _ = check_answer(answer or completion, target.text, forbid=False)
        hook_blocks = sum(
            1 for m in state.messages if m.role == "tool" and "Blocked:" in m.text
        )
        return Score(
            value={"correct": 1.0 if found else 0.0, "strict": 1.0 if ok else 0.0},
            answer=answer or "(no ANSWER line)",
            explanation="; ".join(problems) or "ok",
            metadata={
                "bulk_read_calls": store().get("bulk_read_calls", 0),
                "bulk_read_failures": store().get("bulk_read_failures", 0),
                "hook_blocks": hook_blocks,
            },
        )

    return score


@task
def large_read(
    variant: Literal["direct", "shunt"] = "direct",
    delegate_role: str = "delegate",
    message_limit: int = 80,
):
    corpus = generate()
    extra = workspace_files() if variant == "shunt" else {}
    dataset = [
        Sample(
            id=q["id"],
            input=PROMPT_TEMPLATE.format(question=q["input"], answer_format=q["answer_format"]),
            target=q["target"],
            files={**files_from_dir(corpus), **extra},
        )
        for q in QUESTIONS
    ]
    if variant == "shunt":
        solver = [
            delegate_preflight(delegate_role),
            as_solver(
                claude_code(
                    bridged_tools=[
                        BridgedToolsSpec(name="shunt", tools=[bulk_read(delegate_role)])
                    ]
                )
            ),
        ]
    elif variant == "direct":
        solver = claude_code()
    else:
        raise ValueError(f"unknown variant: {variant}")
    return Task(
        dataset=dataset,
        solver=solver,
        scorer=answer_matches(),
        sandbox=sandbox_spec(),
        message_limit=message_limit,
        metadata={"variant": variant},
    )
