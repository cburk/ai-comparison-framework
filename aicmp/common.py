"""Shared helpers for aicmp tasks."""

import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def files_from_dir(root: Path, dest_prefix: str = "") -> dict[str, str]:
    """Map every file under `root` to a sandbox path (`dest_prefix/relative`).

    Values are absolute source paths, which Inspect copies into the sandbox.
    """
    return {
        f"{dest_prefix}{p.relative_to(root).as_posix()}": str(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def sandbox_spec():
    """Docker sandbox by default; set AICMP_SANDBOX=local to run on the host (no isolation)."""
    if os.environ.get("AICMP_SANDBOX", "docker") == "local":
        return "local"
    return ("docker", str(Path(__file__).parent / "docker" / "compose.yaml"))


def python_cmd() -> str:
    """Interpreter with pytest for the active sandbox."""
    return sys.executable if os.environ.get("AICMP_SANDBOX") == "local" else "python"
