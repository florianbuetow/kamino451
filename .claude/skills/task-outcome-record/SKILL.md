---
name: task-outcome-record
description: Deterministic post-run task outcome ledger writer. Uses task_outcome_ledger_write.py only and refuses to write without a binary success judgment.
---

# Task Outcome Record

Use this skill only after `run-success-evaluate` returns a valid binary success judgment. It appends one auditable task outcome record to a JSONL ledger.

This skill is deterministic and does not invoke any LLM agent.

## Inputs

```xml
<ledger>
.kamino/evals/tasks/task-outcome-ledger.jsonl
</ledger>

<task_detail_json>
.kamino/evals/tasks/details/<task_id>.json
</task_detail_json>

<run_evidence_json>
path/to/run-evidence.json
</run_evidence_json>

<success_judgment_json>
path/to/success-judgment.json
</success_judgment_json>
```

## Uses

- Script only: `.kamino/evals/scripts/task_outcome_ledger_write.py`

Run the script only through `uv run`:

```bash
uv run .kamino/evals/scripts/task_outcome_ledger_write.py \
  --ledger ".kamino/evals/tasks/task-outcome-ledger.jsonl" \
  --task-detail "<task-detail.json>" \
  --run-evidence "<run-evidence.json>" \
  --success-judgment "<success-judgment.json>" \
  --format json
```

## Output

Strict JSON containing:

- `record_id`
- `ledger_path`
- `success`
- `task_text_hash`
- `task_detail_path`
- `record_sequence`

## Rules

1. Refuse to run without a task detail JSON file.
2. Refuse to run without a success judgment JSON file.
3. Refuse to write unless `success` is present and boolean.
4. Record partial, missing, or unverifiable completion as `success: false`.
5. Append exactly one completed or failed JSONL record per valid invocation.
6. Do not write pending, partial, pre-run, or in-progress rows to the ledger.
7. Do not run agents.
8. Do not invoke any LLM agent.
9. Do not invoke AutoResearch.
10. Run Python only through `uv run`.

## Failure Conditions

Fail clearly and do not modify the ledger if:

1. Any required input file is missing.
2. Any input JSON is malformed.
3. The task detail JSON is malformed or schema-invalid.
4. The success judgment is missing `success`.
5. The success judgment has non-boolean `success`.
6. The route value inside task detail is unsupported.
7. The ledger parent path cannot be written.
8. The script exits non-zero.
