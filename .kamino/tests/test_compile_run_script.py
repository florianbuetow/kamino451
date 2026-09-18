"""Contract tests for the isolated per-attempt compile step of the eval-sweep engine."""

from __future__ import annotations

import hashlib
import json
import subprocess
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


DEMO_TASK_ID = "9-demo-task"
DEMO_EVAL_ID = "task-demo123"
DEFAULT_BLUEPRINT_REL = ".kamino/agents/library/coding/python-coding-agent-single-shot.md"


def build_corpus_task(corpus_root: Path, *, with_tests: bool = True) -> Path:
    """Build one fixture corpus task directory under <corpus_root>/9-demo-task."""
    task_dir = corpus_root / DEMO_TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text(
        """# 9. Demo Task

## Problem

Given a list of integers `xs`, return the sum of all its elements.

## Examples

Example 1:
    Input: xs = [1, 2, 3]
    Output: 6

## Starter

Write your solution to a file named `solution.py` starting from exactly this
class skeleton:

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
    if with_tests:
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
    # A correct reference must exist on disk but must never be staged into a run dir.
    (task_dir / "solution_reference.py").write_text(
        """class Solution:
    def demo(self, xs) -> int:
        return sum(xs)
""",
        encoding="utf-8",
    )
    (task_dir / "figure.png").write_bytes(b"\x00")
    return task_dir


def build_tasks_root(root: Path, *, eval_id: str = DEMO_EVAL_ID) -> Path:
    """Build a fixture tasks-root with staged evaluation/difficulty/candidate JSONs.

    Reuses the fixture shapes from test_task_detail_script.py / the
    agent-candidate-search fixtures, which task_detail_write.py validates.
    """
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


def build_recommendation(
    path: Path,
    *,
    task_id: str = DEMO_EVAL_ID,
    model: str = "sonnet",
    effort: str = "high",
    blueprints: list[str] | None = None,
) -> dict:
    payload = {
        "schema_version": "kamino451.route-recommendation.v2",
        "task_id": task_id,
        "task_type": "coding",
        "recommended_model": model,
        "recommended_effort": effort,
        "recommended_agent_blueprints": blueprints if blueprints is not None else [DEFAULT_BLUEPRINT_REL],
        "source": "success_rate_policy",
        "candidate_scores": [
            {
                "model": model,
                "effort": effort,
                "agent_blueprints": blueprints if blueprints is not None else [DEFAULT_BLUEPRINT_REL],
                "base_score": "0.91",
                "cost_penalty": "0.04",
                "final_score": "0.87",
                "forecast_scope": "agent_model_effort",
                "cost_estimate": {
                    "status": "available",
                    "amount_usd": "0.0123",
                    "sample_count": 4,
                    "usage_forecast": {"input_tokens": "1200", "output_tokens": "400"},
                    "rate_card": {
                        "model_id": "claude-sonnet-test",
                        "pricing_version": "test-v1",
                        "pricing_hash": "sha256:test",
                    },
                },
            }
        ],
        "cost_policy": {"status": "applied", "weight": 0.2, "scale_usd": 0.1},
        "rationale": "fixture recommendation",
    }
    write_json(path, payload)
    return payload


def run_compile(
    *,
    corpus_dir: Path,
    task_id: str,
    eval_id: str,
    tasks_root: Path,
    dispatch_root: Path,
    attempt: int = 1,
    model: str = "sonnet",
    effort: str = "high",
    mode: str = "prescribed",
    sweep_id: str = "t-sweep",
    recommendation: Path | None = None,
    blueprint: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run compile_run.py through uv run."""
    command = [
        "uv",
        "run",
        ".kamino/evals/scripts/compile_run.py",
        "--corpus-dir",
        str(corpus_dir),
        "--task-id",
        task_id,
        "--eval-id",
        eval_id,
        "--attempt",
        str(attempt),
        "--model",
        model,
        "--effort",
        effort,
        "--mode",
        mode,
        "--sweep-id",
        sweep_id,
        "--tasks-root",
        str(tasks_root),
        "--dispatch-root",
        str(dispatch_root),
        "--format",
        "json",
    ]
    if recommendation is not None:
        command.extend(["--recommendation", str(recommendation)])
    if blueprint is not None:
        command.extend(["--blueprint", str(blueprint)])
    return subprocess.run(
        command,
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_compile_run_stages_isolated_layout_and_stamps_sweep_metadata(tmp_path: Path) -> None:
    """A prescribed-mode compile should stage work/verify in isolation and stamp the sweep."""
    corpus_root = tmp_path / "corpus-demo"
    corpus_root.mkdir()
    build_corpus_task(corpus_root)
    tasks_root = build_tasks_root(tmp_path / "tasks-root")
    dispatch_root = tmp_path / "dispatch-queue"

    process = run_compile(
        corpus_dir=corpus_root,
        task_id=DEMO_TASK_ID,
        eval_id=DEMO_EVAL_ID,
        tasks_root=tasks_root,
        dispatch_root=dispatch_root,
        attempt=2,
        model="sonnet",
        effort="high",
        mode="prescribed",
        sweep_id="t-sweep",
    )

    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["mode"] == "prescribed"
    assert payload["sweep_id"] == "t-sweep"
    run_dir = Path(payload["run_dir"])
    agent_file = Path(payload["agent_file"])
    assert payload["run_id"]
    assert run_dir.is_dir()
    assert agent_file.is_file()

    # work/ must receive exactly the problem statement plus its figure.
    work_files = sorted(p.name for p in (run_dir / "work").iterdir())
    assert work_files == ["figure.png", "task.md"]

    assert (run_dir / "verify" / "tests" / "test_solution.py").is_file()
    assert (run_dir / "verify" / "tests_hidden" / "test_hidden.py").is_file()

    # The reference solution must never be staged anywhere under the run dir.
    assert list(run_dir.rglob("solution_reference.py")) == []

    agent_text = agent_file.read_text(encoding="utf-8")
    assert "{{" not in agent_text
    assert "model: sonnet" in agent_text
    assert "effort: high" in agent_text

    execution_graph = (run_dir / "execution-graph.md").read_text(encoding="utf-8")
    assert str(run_dir / "verify" / "tests") in execution_graph
    assert str(run_dir / "verify" / "tests_hidden") in execution_graph

    route_decision = json.loads((run_dir / "route-decision.json").read_text(encoding="utf-8"))
    assert route_decision["sweep"] == {"mode": "prescribed", "sweep_id": "t-sweep"}
    assert route_decision["attempt"] == 2
    assert "routing_recommendation" not in route_decision
    assert "recommendation_binding_matches" not in route_decision

    assert (tasks_root / "details" / f"{DEMO_EVAL_ID}-a2.json").is_file()


def test_compile_run_rejects_missing_candidate_search_artifact(tmp_path: Path) -> None:
    """A compile with no staged candidates/<eval_id>.json must fail loudly, not silently."""
    corpus_root = tmp_path / "corpus-demo"
    corpus_root.mkdir()
    build_corpus_task(corpus_root)
    tasks_root = build_tasks_root(tmp_path / "tasks-root")
    (tasks_root / "candidates" / f"{DEMO_EVAL_ID}.json").unlink()

    process = run_compile(
        corpus_dir=corpus_root,
        task_id=DEMO_TASK_ID,
        eval_id=DEMO_EVAL_ID,
        tasks_root=tasks_root,
        dispatch_root=tmp_path / "dispatch-queue",
    )

    assert process.returncode != 0
    assert "candidates" in process.stderr


def test_compile_run_rejects_corpus_task_without_tests_directory(tmp_path: Path) -> None:
    """A corpus task missing tests/ must fail the compile instead of staging an unverifiable run."""
    corpus_root = tmp_path / "corpus-demo"
    corpus_root.mkdir()
    build_corpus_task(corpus_root, with_tests=False)
    tasks_root = build_tasks_root(tmp_path / "tasks-root")

    process = run_compile(
        corpus_dir=corpus_root,
        task_id=DEMO_TASK_ID,
        eval_id=DEMO_EVAL_ID,
        tasks_root=tasks_root,
        dispatch_root=tmp_path / "dispatch-queue",
    )

    assert process.returncode != 0
    assert "tests" in process.stderr


def test_compile_run_retains_full_matching_routing_recommendation(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus-demo"
    corpus_root.mkdir()
    build_corpus_task(corpus_root)
    tasks_root = build_tasks_root(tmp_path / "tasks-root")
    recommendation_path = tmp_path / "recommendation.json"
    recommendation = build_recommendation(recommendation_path)

    process = run_compile(
        corpus_dir=corpus_root,
        task_id=DEMO_TASK_ID,
        eval_id=DEMO_EVAL_ID,
        tasks_root=tasks_root,
        dispatch_root=tmp_path / "dispatch-queue",
        model="sonnet",
        effort="high",
        mode="auto",
        recommendation=recommendation_path,
    )
    assert process.returncode == 0, process.stderr

    run_dir = Path(json.loads(process.stdout)["run_dir"])
    route = json.loads((run_dir / "route-decision.json").read_text(encoding="utf-8"))
    assert route["routing_recommendation"] == recommendation
    assert route["recommendation_binding_matches"] is True
    assert route["recommendation_estimate_applicability"] == {
        "applicable": True,
        "reason": "available cost estimate applies to the exact recommended binding",
    }
    assert route["routing_recommendation"]["candidate_scores"][0]["cost_estimate"]["rate_card"] == {
        "model_id": "claude-sonnet-test",
        "pricing_hash": "sha256:test",
        "pricing_version": "test-v1",
    }


def test_compile_run_records_override_without_applying_original_estimate(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus-demo"
    corpus_root.mkdir()
    build_corpus_task(corpus_root)
    tasks_root = build_tasks_root(tmp_path / "tasks-root")
    recommendation_path = tmp_path / "recommendation.json"
    recommendation = build_recommendation(
        recommendation_path,
        model="haiku",
        effort="medium",
        blueprints=[".kamino/agents/library/coding/python-cli-agent.md"],
    )

    process = run_compile(
        corpus_dir=corpus_root,
        task_id=DEMO_TASK_ID,
        eval_id=DEMO_EVAL_ID,
        tasks_root=tasks_root,
        dispatch_root=tmp_path / "dispatch-queue",
        model="sonnet",
        effort="high",
        mode="auto",
        recommendation=recommendation_path,
    )
    assert process.returncode == 0, process.stderr

    run_dir = Path(json.loads(process.stdout)["run_dir"])
    route = json.loads((run_dir / "route-decision.json").read_text(encoding="utf-8"))
    assert route["routing_recommendation"] == recommendation
    assert route["model"] == "sonnet"
    assert route["effort"] == "high"
    assert route["recommendation_binding_matches"] is False
    applicability = route["recommendation_estimate_applicability"]
    assert applicability["applicable"] is False
    assert "model 'sonnet' != recommended 'haiku'" in applicability["reason"]
    assert "effort 'high' != recommended 'medium'" in applicability["reason"]
    assert "blueprint" in applicability["reason"]


def test_compile_run_matching_binding_marks_unavailable_estimate_inapplicable(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus-demo"
    corpus_root.mkdir()
    build_corpus_task(corpus_root)
    tasks_root = build_tasks_root(tmp_path / "tasks-root")
    recommendation_path = tmp_path / "recommendation.json"
    recommendation = build_recommendation(recommendation_path)
    recommendation["cost_policy"]["status"] = "unavailable"
    recommendation["candidate_scores"][0]["cost_estimate"] = {
        "status": "unavailable",
        "amount_usd": None,
        "reason": "not enough historical samples",
    }
    write_json(recommendation_path, recommendation)

    process = run_compile(
        corpus_dir=corpus_root,
        task_id=DEMO_TASK_ID,
        eval_id=DEMO_EVAL_ID,
        tasks_root=tasks_root,
        dispatch_root=tmp_path / "dispatch-queue",
        model="sonnet",
        effort="high",
        mode="auto",
        recommendation=recommendation_path,
    )
    assert process.returncode == 0, process.stderr

    run_dir = Path(json.loads(process.stdout)["run_dir"])
    route = json.loads((run_dir / "route-decision.json").read_text(encoding="utf-8"))
    assert route["recommendation_binding_matches"] is True
    assert route["recommendation_estimate_applicability"] == {
        "applicable": False,
        "reason": "no available cost estimate for the matching binding",
    }


def test_compile_run_labels_fallback_cost_as_blueprint_mixture(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus-demo"
    corpus_root.mkdir()
    build_corpus_task(corpus_root)
    tasks_root = build_tasks_root(tmp_path / "tasks-root")
    recommendation_path = tmp_path / "recommendation.json"
    recommendation = build_recommendation(recommendation_path, blueprints=[])
    recommendation["candidate_scores"][0]["forecast_scope"] = "model_effort_blueprint_mixture"
    write_json(recommendation_path, recommendation)

    process = run_compile(
        corpus_dir=corpus_root,
        task_id=DEMO_TASK_ID,
        eval_id=DEMO_EVAL_ID,
        tasks_root=tasks_root,
        dispatch_root=tmp_path / "dispatch-queue",
        model="sonnet",
        effort="high",
        mode="auto",
        recommendation=recommendation_path,
    )
    assert process.returncode == 0, process.stderr

    run_dir = Path(json.loads(process.stdout)["run_dir"])
    route = json.loads((run_dir / "route-decision.json").read_text(encoding="utf-8"))
    assert route["recommendation_binding_matches"] is True
    applicability = route["recommendation_estimate_applicability"]
    assert applicability["applicable"] is True
    assert "historical blueprint mixture" in applicability["reason"]
    assert "not as an exact blueprint quote" in applicability["reason"]


def test_compile_run_rejects_recommendation_for_other_task_before_artifacts(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus-demo"
    corpus_root.mkdir()
    build_corpus_task(corpus_root)
    tasks_root = build_tasks_root(tmp_path / "tasks-root")
    recommendation_path = tmp_path / "recommendation.json"
    build_recommendation(recommendation_path, task_id="different-task")
    dispatch_root = tmp_path / "dispatch-queue"

    process = run_compile(
        corpus_dir=corpus_root,
        task_id=DEMO_TASK_ID,
        eval_id=DEMO_EVAL_ID,
        tasks_root=tasks_root,
        dispatch_root=dispatch_root,
        recommendation=recommendation_path,
    )
    assert process.returncode != 0
    assert "does not match eval_id" in process.stderr
    assert not dispatch_root.exists()
    assert not (tasks_root / "details").exists()
