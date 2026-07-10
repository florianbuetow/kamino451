"""Generic corpus integrity: every corpus present in the factory is sound eval material.

Discovery-based — no corpus is named here. Any `.kamino/evals/tasks/corpus-<name>/`
directory is validated against the invariants every corpus must hold, keyed to each
task's own declared contract (`meta.json` `test_command`) rather than a hardcoded
grader:

- the index reconciles with the directories on disk;
- every sampled task has the standard shape;
- the reference solution passes the task's own test command (the oracle is real);
- the same command fails with no solution present (the tests bite);
- the statement leaks neither the reference nor the hidden tier.

New corpora ingested by /create-eval-corpus are covered automatically; that skill
runs this file as its final gate, so ingestion and CI share one implementation.
On a virgin factory (no corpora) everything here skips.

Large corpora are sampled deterministically: every task that ships a hidden tier,
plus an even stride through the rest, capped at ~24 strided picks per corpus.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TASKS_ROOT = REPO / ".kamino" / "evals" / "tasks"
COMMAND_TIMEOUT_SECONDS = 300  # mirrors record_run.py's bounded verification
MAX_STRIDED_TASKS_PER_CORPUS = 24


def discovered_corpora() -> list[Path]:
    """Every corpus directory currently present in the factory."""
    if not TASKS_ROOT.is_dir():
        return []
    return sorted(path for path in TASKS_ROOT.glob("corpus-*") if path.is_dir())


pytestmark = pytest.mark.skipif(
    not discovered_corpora(),
    reason="no corpus present (virgin factory — ingest one with /create-eval-corpus)",
)


def corpus_names() -> list[str]:
    return [path.name.removeprefix("corpus-") for path in discovered_corpora()]


def index_path(name: str) -> Path:
    return TASKS_ROOT / f"corpus-{name}" / f"corpus-{name}-index.json"


def indexed_task_ids(name: str) -> list[str]:
    path = index_path(name)
    if not path.is_file():
        return []
    index = json.loads(path.read_text(encoding="utf-8"))
    return [str(task["task_id"]) for task in index["tasks"]]


def sampled_cases() -> list[tuple[str, str]]:
    """(corpus name, task id) pairs: all hidden-tier tasks + an even stride of the rest."""
    cases: list[tuple[str, str]] = []
    for name in corpus_names():
        task_ids = indexed_task_ids(name)
        corpus = TASKS_ROOT / f"corpus-{name}"
        hidden = [task_id for task_id in task_ids if (corpus / task_id / "tests_hidden").is_dir()]
        rest = [task_id for task_id in task_ids if task_id not in set(hidden)]
        stride = max(1, -(-len(rest) // MAX_STRIDED_TASKS_PER_CORPUS))  # ceil division
        cases.extend((name, task_id) for task_id in hidden)
        cases.extend((name, task_id) for task_id in rest[::stride])
    return cases


def case_id(case: tuple[str, str]) -> str:
    return f"{case[0]}:{case[1]}"


def declared_test_command(task_dir: Path, workdir: Path) -> str:
    """The task's own contract command, normalized to run from a scratch workdir.

    Corpus test_commands are written repo-relative ("uv run pytest tests -q");
    a scratch copy has no uv project, so pin the repo project explicitly. When
    the command runs pytest, disable random ordering for pytest-in-pytest
    stability (matching the retired per-corpus integrity tests).
    """
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    command = str(meta.get("test_command") or "").strip()
    if not command:
        tiers = ["tests"] + (["tests_hidden"] if (workdir / "tests_hidden").is_dir() else [])
        command = f"uv run pytest {' '.join(tiers)} -q"
    if command.startswith("uv run "):
        command = command.replace("uv run ", f"uv run --project {REPO} ", 1)
    if "pytest" in command:
        command += " -p no:randomly"
    return command


def run_contract_command(command: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", command],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )


def copy_task(name: str, task_id: str, destination: Path) -> Path:
    """Copy a task's statement, metadata, and test tiers (never the reference) into a workdir."""
    source = TASKS_ROOT / f"corpus-{name}" / task_id
    workdir = destination / task_id
    workdir.mkdir(parents=True)
    shutil.copy2(source / "task.md", workdir / "task.md")
    shutil.copy2(source / "meta.json", workdir / "meta.json")
    shutil.copytree(source / "tests", workdir / "tests")
    if (source / "tests_hidden").is_dir():
        shutil.copytree(source / "tests_hidden", workdir / "tests_hidden")
    return workdir


SKELETON_PREFIXES = ("def ", "class ", "from ", "import ", '"""', "'''", "@", "#")


def leakable_lines(path: Path) -> set[str]:
    """Verbatim lines whose appearance in task.md would leak the grader.

    Interface skeleton (signatures, imports, docstrings) is deliberately shared
    between the statement's starter block and the reference — only algorithm
    body lines count as leaks.
    """
    if not path.is_file():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if len(line) > 20 and not line.startswith(SKELETON_PREFIXES)}


@pytest.mark.parametrize("name", corpus_names())
def test_index_reconciles_with_disk(name: str) -> None:
    """The corpus index and the task directories on disk must match exactly."""
    assert index_path(name).is_file(), f"corpus-{name} is missing corpus-{name}-index.json"
    indexed = set(indexed_task_ids(name))
    corpus = TASKS_ROOT / f"corpus-{name}"
    on_disk = {path.name for path in corpus.iterdir() if path.is_dir()}
    assert indexed == on_disk, f"corpus-{name}: index/disk mismatch: {sorted(indexed ^ on_disk)[:5]}"


@pytest.mark.parametrize("case", sampled_cases(), ids=case_id)
def test_task_has_standard_shape(case: tuple[str, str]) -> None:
    """Every task ships statement, metadata with a test command, reference, and visible tier."""
    name, task_id = case
    task_dir = TASKS_ROOT / f"corpus-{name}" / task_id
    assert (task_dir / "task.md").is_file()
    assert (task_dir / "solution_reference.py").is_file()
    assert (task_dir / "tests").is_dir()
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    assert str(meta.get("test_command") or "").strip(), f"{task_id}: meta.json lacks a test_command"


@pytest.mark.parametrize("case", sampled_cases(), ids=case_id)
def test_reference_passes_and_tests_bite(case: tuple[str, str], tmp_path: Path) -> None:
    """The reference passes the task's own contract command; without it the command fails."""
    name, task_id = case
    task_dir = TASKS_ROOT / f"corpus-{name}" / task_id
    workdir = copy_task(name, task_id, tmp_path)
    command = declared_test_command(task_dir, workdir)

    shutil.copy2(task_dir / "solution_reference.py", workdir / "solution.py")
    with_reference = run_contract_command(command, workdir)
    assert with_reference.returncode == 0, (
        f"{name}:{task_id}: reference fails its own contract:\n{with_reference.stdout}\n{with_reference.stderr}"
    )

    (workdir / "solution.py").unlink()
    without_solution = run_contract_command(command, workdir)
    assert without_solution.returncode != 0, f"{name}:{task_id}: tests pass with no solution present (vacuous suite)"


@pytest.mark.parametrize("case", sampled_cases(), ids=case_id)
def test_statement_leaks_no_grader_content(case: tuple[str, str]) -> None:
    """task.md must not contain reference source or hidden-tier content verbatim."""
    name, task_id = case
    task_dir = TASKS_ROOT / f"corpus-{name}" / task_id
    statement_lines = {line.strip() for line in (task_dir / "task.md").read_text(encoding="utf-8").splitlines()}
    reference_leak = leakable_lines(task_dir / "solution_reference.py") & statement_lines
    assert not reference_leak, f"{name}:{task_id}: reference source appears in task.md: {sorted(reference_leak)[:3]}"
    for hidden in (task_dir / "tests_hidden").glob("*.py") if (task_dir / "tests_hidden").is_dir() else []:
        hidden_leak = leakable_lines(hidden) & statement_lines
        assert not hidden_leak, f"{name}:{task_id}: hidden-tier content appears in task.md: {sorted(hidden_leak)[:3]}"
