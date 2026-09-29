"""Needle search: single agent vs orchestrator delegating to a search subagent.

    inspect eval aicmp/tasks/needle_search.py -T variant=single   --model <m> --epochs 5
    inspect eval aicmp/tasks/needle_search.py -T variant=subagent --model <m> --epochs 5
"""

from typing import Literal

from inspect_ai import Task, task
from inspect_ai.agent import Agent, agent, as_tool, react
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, accuracy, mean, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import bash

from aicmp.common import files_from_dir, sandbox_spec
from aicmp.data.needle_gen import ANSWER, QUESTION, generate

TOOLS = lambda: [bash(timeout=60)]  # noqa: E731


@agent
def searcher() -> Agent:
    return react(
        name="searcher",
        description=(
            "Searches the ./corpus files for information. Give it a specific, self-contained "
            "research question; it reads the files in its own context and reports back the "
            "relevant findings with file names."
        ),
        prompt="You are a search specialist. Use bash (grep, sed, head) to explore ./corpus "
        "and report precise findings, quoting the relevant sentences. Files are large; "
        "do not print whole files.",
        tools=TOOLS(),
    )


def _solver(variant: str):
    if variant == "single":
        return react(
            prompt="Answer the question using the files in ./corpus. Files are large; "
            "do not print whole files.",
            tools=TOOLS(),
        )
    if variant == "subagent":
        return react(
            prompt="Answer the question by delegating file searching to the searcher tool. "
            "You do not have direct file access. Verify details that could be ambiguous "
            "(dates, spellings) by asking the searcher follow-up questions.",
            tools=[as_tool(searcher())],
        )
    raise ValueError(f"unknown variant: {variant}")


@scorer(metrics=[accuracy(), mean()])
def contains_all():
    """1.0 if every '|'-separated required string appears in the answer."""

    async def score(state: TaskState, target: Target) -> Score:
        text = state.output.completion.lower()
        required = [t.lower() for t in target.text.split("|")]
        ok = all(r in text for r in required)
        return Score(value=1.0 if ok else 0.0, answer=state.output.completion)

    return score


@task
def needle_search(variant: Literal["single", "subagent"] = "single", message_limit: int = 40):
    corpus = generate()
    sample = Sample(
        input=QUESTION,
        target=ANSWER,
        files=files_from_dir(corpus, "corpus/"),
    )
    return Task(
        dataset=[sample],
        solver=_solver(variant),
        scorer=contains_all(),
        sandbox=sandbox_spec(),
        message_limit=message_limit,
        metadata={"variant": variant},
    )
