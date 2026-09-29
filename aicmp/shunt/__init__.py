"""Home-grown replica of Spotify's Shunt plugin, without the Portal dependency.

Shunt has three layers; here is what each became:
- Hooks: `claude/hooks/shunt_check.py`, installed into the sandbox as a project-level
  `.claude/settings.json` PreToolUse hook (see `workspace_files()`).
- Scripts (`bulk-read` -> Portal "bulk-reader" mode on Gemini Flash): `bulk_read()`, a
  host-side Inspect tool exposed to Claude Code over MCP. It calls the `delegate` model
  role directly, so the delegate's tokens are logged by Inspect next to the main model's.
- Skills: folded into the tool description and the hook's block message.

The `code-writer` half of Shunt is not replicated (the post itself found it hard to measure).
"""

from pathlib import Path

from inspect_ai.model import ChatMessageSystem, ChatMessageUser, GenerateConfig, get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import Tool, tool
from inspect_ai.util import sandbox, store

HERE = Path(__file__).parent

# Same temperature/prompt as the post's bulk-reader mode. The post used gemini-2.5-flash,
# which Google no longer serves to new API accounts; 3.8 Flash is its suggested replacement.
DEFAULT_DELEGATE = "google/gemini-3.8-flash"
BULK_READER_PROMPT = (
    "You are a precise code analyst. Read the provided files and answer the question "
    "concisely. Output structured bullets only."
)


def workspace_files() -> dict[str, str]:
    """Sample files that install the Shunt hook into the agent's project directory."""
    return {
        ".claude/settings.json": str(HERE / "claude" / "settings.json"),
        ".claude/hooks/shunt_check.py": str(HERE / "claude" / "hooks" / "shunt_check.py"),
    }


async def _expand(path: str) -> list[str]:
    """A directory expands to the text files under it; a file is returned as-is."""
    result = await sandbox().exec(
        ["find", path, "-type", "f", "-not", "-path", "*/.*", "-not", "-name", "*.pyc"]
    )
    files = sorted(p for p in result.stdout.splitlines() if p) if result.success else []
    return files or [path]


@tool
def bulk_read(delegate_role: str = "delegate") -> Tool:
    async def execute(paths: list[str], question: str) -> str:
        """Answer a question about large files without loading them into your context.

        A cheaper model reads the files in full and returns a concise, structured answer.
        Use this instead of reading large files directly. Ask specific questions and ask it
        to quote exact names, values, and line context you need.

        Args:
            paths: File or directory paths (directories are read recursively).
            question: The specific question to answer from those files.
        """
        parts = []
        for p in paths:
            for f in await _expand(p):
                try:
                    text = await sandbox().read_file(f)
                except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError) as ex:
                    text = f"(could not read: {type(ex).__name__})"
                parts.append(f'<file path="{f}">\n{text}\n</file>')
        model = get_model(role=delegate_role, default=DEFAULT_DELEGATE)
        store().set("bulk_read_calls", store().get("bulk_read_calls", 0) + 1)
        try:
            output = await model.generate(
                [
                    ChatMessageSystem(content=BULK_READER_PROMPT),
                    ChatMessageUser(
                        content="\n".join(parts) + f"\n\n<question>{question}</question>"
                    ),
                ],
                config=GenerateConfig(temperature=0.2),
            )
        except Exception:
            store().set("bulk_read_failures", store().get("bulk_read_failures", 0) + 1)
            raise
        return output.completion

    return execute


@solver
def delegate_preflight(delegate_role: str = "delegate") -> Solver:
    """Fail the sample up front if the delegate model can't be called.

    Otherwise a dead delegate only shows up as tool errors that Claude quietly works
    around, and the run looks like a valid Shunt measurement when it isn't.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        model = get_model(role=delegate_role, default=DEFAULT_DELEGATE)
        try:
            await model.generate("Reply with OK.", config=GenerateConfig(max_tokens=256))
        except Exception as ex:
            raise RuntimeError(f"Shunt delegate {model} is unavailable: {ex}") from ex
        return state

    return solve
