"""Cheapest possible end-to-end check that credentials, Docker, and the agent wiring work.

    inspect eval aicmp/tasks/smoke.py -T harness=react       --model anthropic/claude-haiku-4-5
    inspect eval aicmp/tasks/smoke.py -T harness=claude_code --model anthropic/claude-haiku-4-5

`react` exercises the API key + Docker sandbox. `claude_code` additionally installs the real
Claude Code binary in the sandbox and routes it through Inspect's bridge.
"""

from typing import Literal

from inspect_ai import Task, task
from inspect_ai.agent import react
from inspect_ai.dataset import Sample
from inspect_ai.scorer import includes
from inspect_ai.tool import bash
from inspect_swe import claude_code

from aicmp.common import sandbox_spec

CODEWORD = "periwinkle-ostrich-4471"


@task
def smoke(harness: Literal["react", "claude_code"] = "react"):
    solver = react(tools=[bash(timeout=30)]) if harness == "react" else claude_code()
    return Task(
        dataset=[
            Sample(
                input="Read the file codeword.txt in the current directory and reply with its "
                "exact contents and nothing else.",
                target=CODEWORD,
                files={"codeword.txt": CODEWORD + "\n"},
            )
        ],
        solver=solver,
        scorer=includes(),
        sandbox=sandbox_spec(),
        message_limit=12,
    )
