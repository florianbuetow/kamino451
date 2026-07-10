#!/usr/bin/env python3
"""Compile one corpus task attempt into an isolated dispatch-queue run directory.

Generic across corpora: any corpus directory whose tasks follow the standard
shape (<task-id>/task.md, meta.json, tests/, optional tests_hidden/,
solution_reference.py) can be compiled. Physical test isolation is the
contract this script enforces:

- work/ receives ONLY task.md plus top-level raster images (figures).
- Every test tier is staged under verify/, outside the agent's working area.
- solution_reference.py is NEVER copied anywhere in the run directory.

The staged layout is asserted after staging; a violation aborts the compile.

Sweep provenance (--mode auto|prescribed, --sweep-id) is stamped into
route-decision.json so downstream reports can separate factory-routed sweeps
from prescribed-agent sweeps.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TASKS = REPO / ".kamino" / "evals" / "tasks"
DEFAULT_BLUEPRINT = REPO / ".kamino" / "agents" / "library" / "coding" / "python-coding-agent-single-shot.md"
IMAGE_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".webp"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", required=True, help="Corpus root directory (e.g. .kamino/evals/tasks/corpus-<name>).")
    parser.add_argument("--task-id", required=True, help="Corpus task directory name inside --corpus-dir.")
    parser.add_argument("--eval-id", required=True, help="Eval task id keying evaluations/, difficulty/, candidates/.")
    parser.add_argument("--attempt", type=int, default=1, help="Attempt number. Defaults to 1.")
    parser.add_argument("--model", default="haiku", help="Model bound into the instantiated agent frontmatter.")
    parser.add_argument("--effort", default="medium", help="Effort bound into the instantiated agent frontmatter.")
    parser.add_argument("--blueprint", default=str(DEFAULT_BLUEPRINT), help="Agent blueprint to instantiate.")
    parser.add_argument("--mode", choices=["auto", "prescribed"], required=True, help="auto: factory routing chose the agent; prescribed: caller pinned it.")
    parser.add_argument("--sweep-id", required=True, help="Identifier grouping all attempts of one sweep.")
    parser.add_argument("--binding-reason", default=None, help="Why this blueprint/model was bound. Defaults per --mode.")
    parser.add_argument("--tasks-root", default=str(TASKS), help="Eval tasks root holding evaluations/, difficulty/, candidates/, details/. Defaults to the repo's.")
    parser.add_argument("--dispatch-root", default=str(REPO / ".kamino" / "dispatch-queue"), help="Directory run dirs are created under. Defaults to the repo's dispatch queue.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, cwd=REPO, check=False)
    if result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr}")
    return result


def fill_token(agent_file: Path, token: str, value: str) -> None:
    process = subprocess.run(
        [str(REPO / ".kamino" / "scripts" / "template-replace.sh"), str(agent_file)],
        input=f"{{{{{token}}}}} {value}",
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise SystemExit(f"template fill failed for {{{{{token}}}}}: {process.stderr}")


def bind_frontmatter(agent_file: Path, model: str, effort: str) -> None:
    """Bind model/effort in the instantiated copy's frontmatter (never the blueprint)."""
    text = agent_file.read_text(encoding="utf-8")
    text = re.sub(r"^model: .*$", f"model: {model}", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^effort: .*$", f"effort: {effort}", text, count=1, flags=re.MULTILINE)
    agent_file.write_text(text, encoding="utf-8")


def assert_isolation(run_dir: Path) -> None:
    """Hard guarantees: no reference solution anywhere, no tests inside work/."""
    leaked_references = list(run_dir.rglob("solution_reference.py"))
    if leaked_references:
        raise SystemExit(f"isolation violation: solution_reference.py staged at {leaked_references[0]}")
    work = run_dir / "work"
    leaked_tests = [path for path in work.rglob("*") if path.is_dir() and path.name in ("tests", "tests_hidden")]
    leaked_tests += [path for path in work.rglob("test_*.py")]
    if leaked_tests:
        raise SystemExit(f"isolation violation: test material inside work/: {leaked_tests[0]}")


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    corpus_dir = Path(args.corpus_dir).resolve()
    corpus_task_dir = corpus_dir / args.task_id
    blueprint = Path(args.blueprint).resolve()
    tasks_root = Path(args.tasks_root).resolve()
    if not (corpus_task_dir / "task.md").is_file():
        raise SystemExit(f"missing task.md for {args.task_id} in {corpus_dir}")
    if not (corpus_task_dir / "tests").is_dir():
        raise SystemExit(f"missing tests/ for {args.task_id} in {corpus_dir}")
    if not blueprint.is_file():
        raise SystemExit(f"missing blueprint {blueprint}")
    for artifact in ("evaluations", "difficulty", "candidates"):
        if not (tasks_root / artifact / f"{args.eval_id}.json").is_file():
            raise SystemExit(f"missing staged {artifact}/{args.eval_id}.json — run the difficulty pipeline first")

    corpus_label = corpus_dir.name.removeprefix("corpus-")
    timestamp = datetime.now(timezone.utc).strftime("%y%m%d-%H%M%S")
    run_id = f"{timestamp}-{corpus_label}-{args.task_id}"
    run_dir = Path(args.dispatch_root).resolve() / run_id
    (run_dir / "outputs").mkdir(parents=True)
    (run_dir / "work").mkdir()
    (run_dir / "verify").mkdir()

    # Stage the solver's view: statement + figures only.
    shutil.copy2(corpus_task_dir / "task.md", run_dir / "work" / "task.md")
    for path in corpus_task_dir.iterdir():
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            shutil.copy2(path, run_dir / "work" / path.name)

    # Stage every test tier outside the solver's reach.
    shutil.copytree(corpus_task_dir / "tests", run_dir / "verify" / "tests")
    if (corpus_task_dir / "tests_hidden").is_dir():
        shutil.copytree(corpus_task_dir / "tests_hidden", run_dir / "verify" / "tests_hidden")

    assert_isolation(run_dir)

    agent_file = run_dir / f"01-{blueprint.stem}.md"
    shutil.copy2(blueprint, agent_file)
    goal = "Solve the coding problem specified in the provided PROBLEM file exactly as stated, writing a complete solution module in a single attempt."
    fill_token(agent_file, "GOAL", goal)
    fill_token(agent_file, "PROBLEM", str(run_dir / "work" / "task.md"))
    fill_token(agent_file, "OUTPUT_FILE", str(run_dir / "work" / "solution.py"))
    bind_frontmatter(agent_file, args.model, args.effort)
    run([str(REPO / ".kamino" / "scripts" / "template-replace-completed.sh"), str(agent_file)])

    test_dirs = [run_dir / "verify" / "tests"]
    if (run_dir / "verify" / "tests_hidden").is_dir():
        test_dirs.append(run_dir / "verify" / "tests_hidden")
    verify_command = f"uv run --project {REPO} pytest {' '.join(str(path) for path in test_dirs)} -q"

    try:
        blueprint_rel = str(blueprint.relative_to(REPO))
    except ValueError:
        blueprint_rel = str(blueprint)

    (run_dir / "execution-graph.md").write_text(
        f"""# Execution Graph — {run_id}

## Run order
1. {agent_file.name}

## Chain
{agent_file.stem}

## Steps
| Step | Agent file | Blueprint | Model / Effort | Inputs (value / upstream path) | Output file | Verification | Depends on |
|---:|---|---|---|---|---|---|---|
| 01 | {agent_file.name} | {blueprint_rel} | {args.model} / {args.effort} (bound: {args.mode} sweep) | PROBLEM = work/task.md | work/solution.py | `{verify_command}` | — |

## Notes
- Corpus task: {args.task_id} ({corpus_dir.name}, attempt {args.attempt}, sweep {args.sweep_id}, mode {args.mode})
- The agent never sees the test tiers; they are staged under verify/ and run only at post-flight.
""",
        encoding="utf-8",
    )

    binding_reason = args.binding_reason or (
        "factory routing selected this agent/model" if args.mode == "auto" else "caller prescribed this agent for the whole corpus sweep"
    )
    route_decision = {
        "route_chosen": "clone",
        "agent_files_used": [str(agent_file)],
        "agent_blueprints_used": [blueprint_rel],
        "model": args.model,
        "effort": args.effort,
        "binding_reason": binding_reason,
        "corpus_dir": str(corpus_dir),
        "corpus_task_id": args.task_id,
        "attempt": args.attempt,
        "sweep": {"mode": args.mode, "sweep_id": args.sweep_id},
    }
    (run_dir / "route-decision.json").write_text(json.dumps(route_decision, indent=2, sort_keys=True), encoding="utf-8")

    run(
        [
            "uv", "run", str(REPO / ".kamino" / "evals" / "scripts" / "task_detail_write.py"),
            "--output-dir", str(tasks_root / "details"),
            "--task-eval", str(tasks_root / "evaluations" / f"{args.eval_id}.json"),
            "--difficulty", str(tasks_root / "difficulty" / f"{args.eval_id}.json"),
            "--candidate-search", str(tasks_root / "candidates" / f"{args.eval_id}.json"),
            "--route", str(run_dir / "route-decision.json"),
            "--attempt", str(args.attempt),
            "--format", "json",
        ]
    )

    print(json.dumps({"run_dir": str(run_dir), "agent_file": str(agent_file), "run_id": run_id, "mode": args.mode, "sweep_id": args.sweep_id}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
