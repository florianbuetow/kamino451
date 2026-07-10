---
name: agent-candidate-search
description: Deterministic read-only search over completed task outcome ledger records to produce score-free Agent Factory candidates.
---

# Agent Candidate Search

Use this skill after `task-evaluate` and `rank-task-difficulty`, before factory chooses agents. It searches successful historical task outcomes and returns a shortlist of agents the factory should consider.

This skill is deterministic and does not invoke any LLM agent.

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

<limit>10</limit>
```

Optional central config override (defaults to `.kamino/factory-config.json`):

```xml
<config>.kamino/factory-config.json</config>
```

## Uses

- Script only: `.kamino/evals/scripts/agent_candidate_search.py`

Run the script only through `uv run`:

```bash
uv run .kamino/evals/scripts/agent_candidate_search.py \
  --ledger ".kamino/evals/tasks/task-outcome-ledger.jsonl" \
  --task-eval "<task-eval.json>" \
  --difficulty "<difficulty.json>" \
  --limit "10" \
  --format json
```

## Output

Strict JSON containing:

- `schema_version`
- `task_id`
- `task_text_hash`
- `limit`
- `candidate_count`
- `candidates`
- `routing_config` — the `success_rate_threshold` / `min_attempts_for_rate` values applied, read from the central factory config `.kamino/factory-config.json`

Each candidate carries success-rate statistics computed over **all** ledger attempts for its route+agent+model+effort combination, failures included:

- `historical_attempt_count`, `historical_success_count`, `historical_success_rate`
- `same_task_type_attempt_count`, `same_task_type_success_count`, `same_task_type_success_rate` (scoped to the current task's `task_type`)
- `meets_success_rate_threshold` — `true` when the same-task-type rate is strictly above the configured threshold over at least the configured minimum attempts

Candidates that meet the threshold rank first. Candidate output is a shortlist to consider, not a winner list.

Cold start: when the ledger file does not exist yet, or exists but is empty (the virgin-factory state), the search succeeds with `candidate_count` `0` and an empty `candidates` list. A ledger with malformed or schema-invalid records still fails.

## Rules

1. Do not invoke any LLM agent.
2. Do not write the ledger.
3. Do not mutate files.
4. Do not invoke AutoResearch.
5. Do not expose numeric similarity scores to factory.
6. Run Python only through `uv run`.

## Failure Conditions

Fail clearly if:

1. Any required input path is missing or malformed.
2. The ledger contains malformed or schema-invalid records (a missing or empty ledger is a cold start, not a failure).
3. The task evaluation JSON is missing or schema-invalid.
4. The difficulty placement JSON is missing or schema-invalid.
5. `limit` is non-positive or non-integer.
6. The script exits non-zero.
