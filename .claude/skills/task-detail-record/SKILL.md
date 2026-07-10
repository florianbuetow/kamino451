---
name: task-detail-record
description: Deterministic pre-run task detail writer. Persists task context as .kamino/evals/tasks/details/<task_id>.json without writing the outcome ledger.
---

# Task Detail Record

Use this skill after factory has produced task evaluation, difficulty placement, candidate search, and route decision artifacts, and before any assembled agents are executed.

This skill writes durable task context. It does not record success or failure.

## Inputs

```xml
<output_dir>
.kamino/evals/tasks/details
</output_dir>

<task_evaluation_json>
path/to/task-evaluation.json
</task_evaluation_json>

<difficulty_json>
path/to/difficulty-placement.json
</difficulty_json>

<candidate_search_json>
path/to/candidate-search.json
</candidate_search_json>

<route_json>
path/to/factory-route.json
</route_json>
```

Optional (defaults to `1`):

```xml
<attempt>2</attempt>
```

Attempt `1` writes `<task_id>.json`. A retry or escalation attempt `N > 1` (e.g. re-running with a stronger model) writes `<task_id>-a<N>.json`, with the attempt's own route decision (model, effort) embedded. Each attempt file is immutable once written.

## Uses

- Script only: `.kamino/evals/scripts/task_detail_write.py`

Run the script only through `uv run`:

```bash
uv run .kamino/evals/scripts/task_detail_write.py \
  --output-dir ".kamino/evals/tasks/details" \
  --task-eval "<task-eval.json>" \
  --difficulty "<difficulty.json>" \
  --candidate-search "<candidate-search.json>" \
  --route "<route.json>" \
  --attempt "1" \
  --format json
```

## Output

Strict JSON containing:

- `schema_version`
- `task_detail_schema_version`
- `task_id`
- `task_text_hash`
- `task_detail_path`
- `attempt`

## Rules

1. Write exactly one task detail file per invocation: `.kamino/evals/tasks/details/<task_id>.json` for attempt 1, `.kamino/evals/tasks/details/<task_id>-a<N>.json` for attempt N > 1.
2. Refuse to overwrite an existing task detail file.
3. Do not run assembled agents.
4. Do not write the task outcome ledger.
5. Do not invoke any LLM agent.
6. Do not invoke AutoResearch.
7. Run Python only through `uv run`.

## Failure Conditions

Fail clearly if:

1. Any required input file is missing.
2. Any input JSON is malformed or schema-invalid.
3. The task id or task hash is inconsistent across artifacts.
4. The task detail file already exists.
5. The script exits non-zero.
