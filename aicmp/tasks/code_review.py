"""Code review task: find planted logic bugs in a small repo.

Compare models with e.g.:
    inspect eval aicmp/tasks/code_review.py --model anthropic/<model>,google/<model> --epochs 5
"""

import re

from inspect_ai import Task, task
from inspect_ai.agent import react
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, accuracy, mean, scorer
from inspect_ai.solver import TaskState
from inspect_ai.tool import bash

from aicmp.common import DATA_DIR, files_from_dir, python_cmd, sandbox_spec

# Each planted bug: id -> (function that must be named, pattern describing the flaw).
BUGS = {
    "discount_missing_div_100": (
        "apply_discount",
        r"(divid|/\s*100|percent(age)?\s+(is\s+)?(not|never)|fraction|scale|multipl\w+\s+by\s+(the\s+)?percent|treat\w*\s+(the\s+)?percent)",
    ),
    "paginate_off_by_one": (
        "paginate",
        r"(0-index|zero-index|1-index|one-index|off.by.one|\(page\s*-\s*1\)|skips?\s+(the\s+)?first|first\s+page)",
    ),
    "average_empty_list": (
        "average",
        r"(empty|zero\s*division|division\s+by\s+zero|len\(values\)\s*(is|==)\s*0)",
    ),
}

PROMPT = (
    "The repository in ./repo is about to be released. Review its Python code for "
    "logic bugs (behavior that contradicts the docstrings or would fail at runtime). "
    "Explore the files with the bash tool. In your final answer list every bug you are "
    "confident about, naming the function and explaining the flaw. Do not report style issues."
)


@scorer(metrics=[accuracy(), mean()])
def bug_recall():
    """Fraction of planted bugs identified (function named + flaw described)."""

    async def score(state: TaskState, target: Target) -> Score:
        text = state.output.completion
        found = {}
        for bug_id, (func, pattern) in BUGS.items():
            # Look at each paragraph/bullet mentioning the function for the flaw description.
            chunks = [c for c in re.split(r"\n\s*\n|\n(?=\s*[-*\d])", text) if func in c]
            found[bug_id] = any(re.search(pattern, c, re.I) for c in chunks)
        return Score(
            value=sum(found.values()) / len(BUGS),
            answer=", ".join(k for k, v in found.items() if v) or "none",
            metadata={"found": found},
        )

    return score


@task
def code_review(message_limit: int = 30):
    sample = Sample(
        input=PROMPT,
        target=", ".join(BUGS),
        files=files_from_dir(DATA_DIR / "code_review" / "repo", "repo/"),
    )
    return Task(
        dataset=[sample],
        solver=react(tools=[bash(timeout=60)]),
        scorer=bug_recall(),
        sandbox=sandbox_spec(),
        message_limit=message_limit,
    )
