"""Offline checks: fixtures are valid and scorers behave, without calling any model."""

import subprocess
import sys
from pathlib import Path

import pytest

from aicmp.data.needle_gen import ANSWER, TARGET, generate
from aicmp.tasks.plan_execute_review import ROOT, parse_pytest

REF = Path(__file__).parent / "reference"


def run_hidden(workdir: Path) -> str:
    (workdir / "hidden_tests").mkdir(exist_ok=True)
    (workdir / "hidden_tests" / "test_bucket.py").write_text(
        (ROOT / "hidden_tests" / "test_bucket.py").read_text()
    )
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "hidden_tests", "-q", "--tb=no", "-p", "no:cacheprovider"],
        cwd=workdir, env={"PYTHONPATH": ".", "PATH": ""}, capture_output=True, text=True,
    )
    return r.stdout


def test_reference_solution_passes_all_hidden_tests(tmp_path):
    (tmp_path / "bucket.py").write_text((REF / "bucket.py").read_text())
    s = parse_pytest(run_hidden(tmp_path))
    assert s.metadata["all_passed"], s.explanation


def test_stub_fails_hidden_tests(tmp_path):
    (tmp_path / "bucket.py").write_text((ROOT / "workspace" / "bucket.py").read_text())
    s = parse_pytest(run_hidden(tmp_path))
    assert s.value == 0.0 and s.metadata["total"] > 0


def test_needle_corpus_shape():
    out = generate()
    files = sorted(out.glob("file_*.txt"))
    assert len(files) == 12 and all(f.stat().st_size > 100_000 for f in files)
    hits = [f.name for f in files if TARGET in f.read_text()]
    assert hits == ["file_08.txt"]
    assert all(a in TARGET for a in ANSWER.split("|"))


def test_code_review_bugs_are_real():
    d = Path(__file__).parents[1] / "aicmp/data/code_review/repo"
    ns: dict = {}
    exec((d / "pricing.py").read_text(), ns)
    assert ns["apply_discount"](100, 20) != 80  # planted bug
    exec((d / "listing.py").read_text(), ns)
    assert ns["paginate"](list(range(10)), 1, 3) != [0, 1, 2]  # planted bug
    exec((d / "stats.py").read_text(), ns)
    with pytest.raises(ZeroDivisionError):
        ns["average"]([])


def test_code_review_scorer():
    import asyncio
    from types import SimpleNamespace

    from aicmp.tasks.code_review import bug_recall

    good = (
        "- `apply_discount` multiplies price by percent instead of dividing by 100.\n"
        "- `paginate` is off-by-one: pages are 1-indexed but start = page*size skips the first page.\n"
        "- `average` raises ZeroDivisionError on an empty list."
    )

    async def go(text):
        st = SimpleNamespace(output=SimpleNamespace(completion=text))
        return (await bug_recall()(st, None)).value

    assert asyncio.run(go(good)) == 1.0
    assert asyncio.run(go("Looks fine to me.")) == 0.0
