# Task Outcome Ledger Schema

Schema version: `kamino451.task-outcome-ledger.v1`

The task outcome ledger is append-only JSONL. Each line is one complete JSON
object. The deterministic reader rejects empty ledgers, malformed JSONL, and
records missing required fields. The deterministic writer validates every input
artifact and appends one record per valid invocation.

Canonical storage path:

```text
.kamino/evals/tasks/task-outcome-ledger.jsonl
```

Validation and tests should use temporary or fixture ledgers, not the canonical
history file.

## Required Fields

| Field | Type | Meaning | Valid values |
|---|---|---|---|
| `schema_version` | string | Ledger schema identifier. | `kamino451.task-outcome-ledger.v1` |
| `record_id` | string | Stable record id assigned by the writer. | Non-empty, `task-outcome-...` by convention |
| `record_sequence` | integer | One-based append sequence in the target ledger. | `>= 1` |
| `timestamp` | string | UTC write time. | ISO-8601 ending in `Z` |
| `task_detail_path` | string | Path to the durable pre-run task detail JSON. | Non-empty for new records |
| `task_id` | string | Task id from task evaluation. | Non-empty |
| `task_text_hash` | string | Stable task text hash. | `sha256:<64 lowercase hex chars>` |
| `task_text` | string | Original task text. | Non-empty |
| `task_type` | string | Task type from task evaluation. | Non-empty |
| `clarity_score` | integer | Task evaluation clarity score. | `1..5` |
| `ambiguity_score` | integer | Task evaluation ambiguity score. | `1..5` |
| `consistency_score` | integer | Task evaluation consistency score. | `1..5` |
| `completeness_score` | integer | Task evaluation completeness score. | `1..5` |
| `semantic_difficulty_score` | number | Task evaluation difficulty score. | `1..5` today; numeric for future compatibility |
| `pairwise_difficulty_score` | number | Difficulty placement score from pairwise ranking. | Any JSON number |
| `nearest_prior_tasks` | array | Prior task anchors used for lookup. | Objects with `task_id` and non-negative `distance` |
| `route_chosen` | string | Factory route selected after evaluation/ranking/lookup. | `clone`, `taskgraph`, or `createblueprint` |
| `agent_files_used` | array | Instantiated agent files used by the run. | Non-empty strings |
| `agent_blueprints_used` | array | Source blueprint files used to instantiate agents. | Non-empty strings |
| `model` | string | Model used for the route/run. | Non-empty |
| `effort` | string | Effort used for the route/run. | Non-empty |
| `execution_status` | string | Pipeline execution status before task success judging. | `completed` or `failed` |
| `success` | boolean | Binary task success after run-success judgment. | `true` or `false` |
| `failure_mode` | string | Deterministic failure class. | `none`, `partial_completion`, `missing_required_output`, `unverifiable_completion`, or `judged_failure` |
| `success_judgment_path` | string | Path to the strict JSON run-success judgment. | Non-empty |
| `output_paths` | array | Run output files considered by the judge. | Non-empty strings |
| `verification_evidence` | object | Deterministic run evidence from `run`. | JSON object |
| `success_judgment` | object | Strict JSON binary success judgment. | See below |

## Success Judgment Object

The writer requires a task detail JSON file and a binary success judgment before
it can append a ledger record.

| Field | Type | Meaning | Valid values |
|---|---|---|---|
| `success` | boolean | Whether all explicit task requirements were fully satisfied. | `true` or `false` |
| `reason` | string | Concise evidence-based reason. | Non-empty |
| `satisfied_requirements` | array | Requirements fully satisfied. | Strings |
| `missing_requirements` | array | Requirements absent from the output. | Strings |
| `partial_requirements` | array | Requirements only partly satisfied. | Strings |
| `unverifiable_requirements` | array | Requirements the evidence cannot prove. | Strings |
| `confidence` | string | Judge confidence. | `low`, `medium`, or `high` |

Partial, missing, or unverifiable completion is always recorded as
`success: false`, even if a malformed judge response claims `success: true`.

## Phase Ownership

Compile phase:

1. `task-evaluate`
2. `rank-task-difficulty`
3. `agent-candidate-search`
4. Route to `clone`, `taskgraph`, or `createblueprint`
5. `task-detail-record`

The compile phase may write `.kamino/evals/tasks/details/<task_id>.json`, but
it never writes this ledger.

Run phase:

1. `run`
2. `run-success-evaluate`
3. `task-outcome-record`

`task-outcome-record` is the only normal task-completion skill that writes the
ledger, and only after a valid binary success judgment exists.

The ledger must contain only completed or failed task outcomes. It must never
contain pending, in-progress, pre-run, or partial context rows.

## Duplicate Writes

The writer appends a second auditable record when invoked repeatedly with the
same artifacts. `record_sequence` and `record_id` distinguish repeated writes.
