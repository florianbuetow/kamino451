---
name: task-outcome-lookup
description: Deterministic read-only lookup of historical task outcomes for Agent Factory routing. Uses task_outcome_ledger_read.py only and never invokes an LLM.
---

# Task Outcome Lookup

Use this skill after `task-evaluate` and `rank-task-difficulty`. It reads historical task outcomes and returns routing evidence for `factory`.

This skill is read-only.

## Inputs

```xml
<ledger>
.kamino/evals/tasks/task-outcome-ledger.jsonl
</ledger>

<task_evaluation_json>
path/to/task-evaluation.json
</task_evaluation_json>

<difficulty_json>
path/to/difficulty-placement.json
</difficulty_json>
```

Optional filters:

```xml
<task_type>writing</task_type>
<difficulty_band>0.5</difficulty_band>
<agent>.kamino/agents/library/writing/article-review-agent.md</agent>
<model>sonnet</model>
<effort>medium</effort>
```

## Uses

- Script only: `.kamino/evals/scripts/task_outcome_ledger_read.py`

Run the script only through `uv run`:

```bash
uv run .kamino/evals/scripts/task_outcome_ledger_read.py \
  --ledger ".kamino/evals/tasks/task-outcome-ledger.jsonl" \
  --task-eval "<task-eval.json>" \
  --difficulty "<difficulty.json>" \
  --format json
```

## Output

Strict JSON containing:

- similar historical tasks
- successful agent/model/effort combinations
- failed agent/model/effort combinations
- risk notes derived only from ledger records
- filters applied

## Rules

1. Do not invoke any LLM agent.
2. Do not write files.
3. Do not mutate the ledger.
4. Do not infer historical outcomes that are not in the ledger.
5. Do not treat partial historical completion as success.
6. Do not invoke AutoResearch.
7. Run Python only through `uv run`.

## Failure Conditions

Fail clearly if:

1. The ledger contains malformed or schema-invalid records (a missing or empty ledger is a cold start: the lookup succeeds with no historical outcomes).
2. The task evaluation JSON is missing or schema-invalid.
3. The difficulty placement JSON is missing or schema-invalid.
4. A filter is malformed.
5. The script exits non-zero.
