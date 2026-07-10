---
name: task-evaluate
description: Public wrapper for profiling an incoming task before Agent Factory routing. Uses task-evaluator, task-llm-judge, and the deterministic evaluate_task.py script to produce reusable task evaluation JSON.
---

# Task Evaluate

Use this skill before factory route selection. It profiles the user task and returns a reusable task evaluation artifact for `rank-task-difficulty`, `task-outcome-lookup`, and later outcome recording.

This skill evaluates only. It does not route, instantiate, or run agents.

## Inputs

The user must provide one of:

```xml
<task>
Task text to evaluate.
</task>
```

or:

```xml
<task_file>
path/to/task.txt
</task_file>
```

Optional:

```xml
<context_files>
path/to/context-a.md
path/to/context-b.md
</context_files>
```

## Uses

- Agent: `task-evaluator`
- Agent used by `task-evaluator`: `task-llm-judge`
- Script: `.kamino/evals/scripts/evaluate_task.py`

Run the deterministic script only through `uv run`:

```bash
uv run .kamino/evals/scripts/evaluate_task.py --task "<task text>" --format json
uv run .kamino/evals/scripts/evaluate_task.py --file "<task file>" --format json
```

## Output Artifact

Write or return strict JSON with these required fields:

- `schema_version`
- `task_id`
- `task_text_hash`
- `task_text`
- `task_type`
- `clarity_score`
- `ambiguity_score`
- `consistency_score`
- `completeness_score`
- `difficulty_score`
- `recommended_mapping`
- `open_issues`
- deterministic `metrics`
- semantic LLM judge report from `task-llm-judge`

Preferred artifact path:

```text
.kamino/evals/tasks/evaluations/<task_id>.json
```

## Steps

1. Require exactly one task source: inline task text or task file path.
2. If a task file is provided, verify it exists and is non-empty.
3. Run `.kamino/evals/scripts/evaluate_task.py` through `uv run`.
4. Invoke `task-evaluator` with the original task text, deterministic JSON, and any context file paths.
5. Preserve the relationship where `task-evaluator` calls `task-llm-judge` for semantic scoring.
6. Combine deterministic and semantic results into one parseable JSON artifact.
7. Return the artifact path or the JSON payload.

## Failure Conditions

Fail clearly if:

1. No task is provided.
2. Both inline task and task file are provided.
3. The task file is missing or empty.
4. The deterministic script exits non-zero.
5. The LLM judge output is not parseable JSON.
6. Any required output field is missing.

## Rules

1. Do not route to `clone`, `taskgraph`, or `createblueprint`.
2. Do not run assembled agents.
3. Do not write the task outcome ledger.
4. Do not invoke AutoResearch.
5. Run Python only through `uv run`.
