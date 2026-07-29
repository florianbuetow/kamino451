"""Contract tests for the post-flight recording step of the eval-sweep engine."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def sha256_of(text: str) -> str:
    """Return a sha256:<hex> identity hash for fixture task text."""
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def write_json(path: Path, payload: object) -> None:
    """Write stable JSON test data, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def utc_now_iso() -> str:
    """Return the current UTC timestamp in the format record_run.py expects."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def seed_transcript(run_dir: Path, transcripts_root: Path, started_at: str) -> None:
    """Write a minimal subagent transcript matching the capsule's agent file."""
    agent_file = next(run_dir.glob("01-*.md"))
    entries = [
        {"type": "user", "timestamp": started_at,
         "message": {"role": "user",
                     "content": f"You are the agent defined in the file {agent_file}. Read that file NOW."}},
        {"type": "assistant", "timestamp": started_at,
         "message": {"id": "msg_t", "model": "claude-haiku-4-5-20251001",
                     "usage": {"input_tokens": 10, "output_tokens": 50,
                               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                     "content": [{"type": "text", "text": "solution written"}]}},
    ]
    target = transcripts_root / "test-session" / "subagents" / "agent-t1.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


DEMO_TASK_ID = "9-demo-task"
DEMO_EVAL_ID = "task-demo123"
DEMO_ATTEMPT = 2


def build_corpus_task(corpus_root: Path) -> Path:
    """Build one fixture corpus task directory under <corpus_root>/9-demo-task."""
    task_dir = corpus_root / DEMO_TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text(
        """# 9. Demo Task

## Problem

Given a list of integers `xs`, return the sum of all its elements.

## Starter

```python
class Solution:
    def demo(self, xs) -> int:
        \"\"\"Return the sum of xs.\"\"\"
```
""",
        encoding="utf-8",
    )
    write_json(
        task_dir / "meta.json",
        {
            "task_id": DEMO_TASK_ID,
            "title": "9. Demo Task",
            "intended_difficulty": "easy",
            "test_command": "uv run pytest tests -q",
        },
    )
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_solution.py").write_text(
        """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solution import Solution


def test_demo_basic():
    assert Solution().demo([1, 2, 3]) == 6


def test_demo_empty():
    assert Solution().demo([]) == 0
""",
        encoding="utf-8",
    )
    tests_hidden_dir = task_dir / "tests_hidden"
    tests_hidden_dir.mkdir()
    (tests_hidden_dir / "test_hidden.py").write_text(
        """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solution import Solution


def test_demo_larger():
    assert Solution().demo([10, 20, 30, 40]) == 100
""",
        encoding="utf-8",
    )
    (task_dir / "solution_reference.py").write_text(
        """class Solution:
    def demo(self, xs) -> int:
        return sum(xs)
""",
        encoding="utf-8",
    )
    return task_dir


def build_tasks_root(root: Path, *, eval_id: str = DEMO_EVAL_ID) -> Path:
    """Build a fixture tasks-root with staged evaluation/difficulty/candidate JSONs."""
    task_text_hash = sha256_of(eval_id)
    write_json(
        root / "evaluations" / f"{eval_id}.json",
        {
            "schema_version": "kamino451.task-evaluation.v1",
            "task_id": eval_id,
            "task_text_hash": task_text_hash,
            "task_text": "Given a list of integers xs, return the sum of all its elements.",
            "task_type": "coding",
            "clarity_score": 4,
            "ambiguity_score": 2,
            "consistency_score": 5,
            "completeness_score": 4,
            "difficulty_score": 2,
            "recommended_mapping": "standard_model_task_agent",
            "open_issues": [],
        },
    )
    write_json(
        root / "difficulty" / f"{eval_id}.json",
        {
            "schema_version": "kamino451.bradley-terry-pairwise-ranking.v1",
            "estimated_difficulty_score": 0.4,
            "estimated_insertion_rank": 3,
            "nearest_prior_tasks": [{"task_id": "prior-demo-close", "distance": 0.02}],
        },
    )
    write_json(
        root / "candidates" / f"{eval_id}.json",
        {
            "schema_version": "kamino451.agent-candidate-search.v1",
            "task_id": eval_id,
            "task_text_hash": task_text_hash,
            "limit": 10,
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "route_chosen": "clone",
                    "agent_blueprints_used": [".kamino/agents/library/coding/python-cli-agent.md"],
                    "agent_files_used": [".kamino/dispatch-queue/fixture/01-python-cli-agent.md"],
                    "model": "sonnet",
                    "effort": "medium",
                    "historical_success_count": 2,
                    "matched_task_types": ["coding"],
                    "similar_prior_tasks": [
                        {
                            "record_id": "task-outcome-demo-close-1",
                            "task_id": "prior-demo-close",
                            "task_text_excerpt": "Sum a list of integers.",
                            "task_type": "coding",
                            "route_chosen": "clone",
                            "model": "sonnet",
                            "effort": "medium",
                        }
                    ],
                    "match_reasons": ["same task_type"],
                }
            ],
        },
    )
    return root


def run_compile(*, corpus_dir: Path, tasks_root: Path, dispatch_root: Path) -> subprocess.CompletedProcess[str]:
    """Run compile_run.py through uv run with this file's fixed demo identifiers."""
    return subprocess.run(
        [
            "uv",
            "run",
            ".kamino/evals/scripts/compile_run.py",
            "--corpus-dir",
            str(corpus_dir),
            "--task-id",
            DEMO_TASK_ID,
            "--eval-id",
            DEMO_EVAL_ID,
            "--attempt",
            str(DEMO_ATTEMPT),
            "--model",
            "sonnet",
            "--effort",
            "high",
            "--mode",
            "prescribed",
            "--sweep-id",
            "t-sweep",
            "--tasks-root",
            str(tasks_root),
            "--dispatch-root",
            str(dispatch_root),
            "--format",
            "json",
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def compile_fresh_run(base: Path) -> tuple[Path, Path]:
    """Compile one fresh isolated run dir under base/ and return (run_dir, tasks_root)."""
    corpus_root = base / "corpus-demo"
    corpus_root.mkdir(parents=True)
    build_corpus_task(corpus_root)
    tasks_root = build_tasks_root(base / "tasks-root")
    dispatch_root = base / "dispatch-queue"

    process = run_compile(corpus_dir=corpus_root, tasks_root=tasks_root, dispatch_root=dispatch_root)
    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    return Path(payload["run_dir"]), tasks_root


def run_record(
    *,
    run_dir: Path,
    ledger: Path,
    tasks_root: Path,
    started_at: str,
    transcripts_root: Path | None = None,
    model: str = "sonnet",
) -> subprocess.CompletedProcess[str]:
    """Run record_run.py through uv run with this file's fixed demo identifiers."""
    command = [
        "uv",
        "run",
        ".kamino/evals/scripts/record_run.py",
        "--task-id",
        DEMO_EVAL_ID,
        "--run-dir",
        str(run_dir),
        "--model",
        model,
        "--effort",
        "high",
        "--started-at",
        started_at,
        "--ended-at",
        "now",
        "--attempt",
        str(DEMO_ATTEMPT),
        "--ledger",
        str(ledger),
        "--tasks-root",
        str(tasks_root),
    ]
    if transcripts_root is not None:
        command.extend(["--transcripts-root", str(transcripts_root)])
    command.extend(["--format", "json"])
    return subprocess.run(
        command,
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_record_run_marks_a_correct_solution_successful(tmp_path: Path) -> None:
    """A solution that passes every staged tier must be recorded as a success."""
    base = tmp_path / "a"
    run_dir, tasks_root = compile_fresh_run(base)
    (run_dir / "work" / "solution.py").write_text(
        "class Solution:\n    def demo(self, xs) -> int:\n        return sum(xs)\n",
        encoding="utf-8",
    )
    ledger = base / "ledger.jsonl"
    started_at = utc_now_iso()
    transcripts_root = base / "transcripts"
    seed_transcript(run_dir, transcripts_root, started_at)

    process = run_record(
        run_dir=run_dir,
        ledger=ledger,
        tasks_root=tasks_root,
        started_at=started_at,
        transcripts_root=transcripts_root,
        model="haiku",
    )

    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["success"] is True
    assert payload["tests_passed"] is True
    assert payload["status"] == "ok"
    assert payload["token_costs"] == str(run_dir / "token_costs.json")
    assert (run_dir / "token_costs.json").is_file()

    ledger_lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1

    assert (tasks_root / "outcomes" / f"{DEMO_EVAL_ID}-a2-success.json").is_file()
    assert (run_dir / "verify" / "solution.py").is_file()
    assert (run_dir / "trace.jsonl").is_file()


def test_record_run_marks_a_wrong_solution_failed(tmp_path: Path) -> None:
    """A solution that fails the staged tests must be recorded as a failure."""
    base = tmp_path / "b"
    run_dir, tasks_root = compile_fresh_run(base)
    (run_dir / "work" / "solution.py").write_text(
        "class Solution:\n    def demo(self, xs) -> int:\n        return -1\n",
        encoding="utf-8",
    )
    ledger = base / "ledger.jsonl"

    process = run_record(run_dir=run_dir, ledger=ledger, tasks_root=tasks_root, started_at=utc_now_iso())

    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["success"] is False
    assert payload["tests_passed"] is False
    assert payload["status"] == "failed"


def test_record_run_marks_a_missing_solution_failed(tmp_path: Path) -> None:
    """An attempt with no solution file at all must still record cleanly as a failure."""
    base = tmp_path / "c"
    run_dir, tasks_root = compile_fresh_run(base)
    ledger = base / "ledger.jsonl"

    process = run_record(run_dir=run_dir, ledger=ledger, tasks_root=tasks_root, started_at=utc_now_iso())

    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["status"] == "failed"
    assert payload["tests_passed"] is False


def test_record_run_keeps_recording_when_token_accounting_fails(tmp_path: Path) -> None:
    """A missing transcript must not block outcome recording."""
    base = tmp_path / "d"
    run_dir, tasks_root = compile_fresh_run(base)
    (run_dir / "work" / "solution.py").write_text(
        "class Solution:\n    def demo(self, xs) -> int:\n        return sum(xs)\n",
        encoding="utf-8",
    )
    ledger = base / "ledger.jsonl"
    transcripts_root = base / "empty-transcripts"
    transcripts_root.mkdir()

    process = run_record(
        run_dir=run_dir,
        ledger=ledger,
        tasks_root=tasks_root,
        started_at=utc_now_iso(),
        transcripts_root=transcripts_root,
    )

    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["token_costs"] is None
    assert not (run_dir / "token_costs.json").is_file()
