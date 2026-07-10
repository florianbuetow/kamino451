# Run Trace Schema

Schema version: `kamino451.run-trace.v1`

The run trace is append-only JSONL, one file per dispatch-queue run, written by
the `run` skill through the deterministic writer
`.kamino/evals/scripts/run_trace_write.py`. Each line is one complete JSON
object describing one **step attempt**. The writer validates every record,
refuses malformed or schema-invalid input, and requires every record in one
trace file to share the same `run_id`.

Canonical storage path:

```text
.kamino/dispatch-queue/<run_id>/trace.jsonl
```

Validation and tests should use temporary or fixture traces, not real
dispatch-queue history.

## Required Fields

| Field | Type | Meaning | Valid values |
|---|---|---|---|
| `schema_version` | string | Trace schema identifier. | `kamino451.run-trace.v1` |
| `run_id` | string | Dispatch-queue run id (`<YYMMDD-HHMMSS>`). | Non-empty; one per trace file |
| `step` | integer | One-based step number from the execution graph. | `>= 1` |
| `attempt` | integer | One-based attempt for this step (operational retries, escalations). | `>= 1` |
| `agent_file` | string | Instantiated agent file that was dispatched. | Non-empty |
| `model` | string | Model the step was launched with (from the instantiated frontmatter). | Non-empty |
| `effort` | string | Effort the step was launched with. | Non-empty |
| `started_at` | string | UTC step start. | ISO-8601 ending in `Z` |
| `ended_at` | string | UTC step end. | ISO-8601 ending in `Z` |
| `duration_seconds` | number | Wall-clock step duration. | `>= 0` |
| `status` | string | Step outcome after post-flight. | `ok`, `skipped`, or `failed` |
| `output_path` | string | The step's declared output file. | Non-empty |
| `verdict` | string or null | Explicit gate verdict when the agent is a gate. | `PASS`, `FAIL`, or `null` |
| `error` | string or null | Error text when the step failed operationally. | Non-empty string or `null` |
| `subagent_summary` | string or null | The dispatched subagent's returned result text. | Non-empty string or `null` |
| `verification` | object | Post-flight evidence for this step. | JSON object (see below) |

## Optional Fields

| Field | Type | Meaning |
|---|---|---|
| `blueprint` | string | Source blueprint path the agent was instantiated from, when known. |

## Verification Object

Free-form JSON object holding the step's deterministic post-flight evidence.
When the execution graph declares a **verification command** for the step, the
object must include:

| Field | Type | Meaning |
|---|---|---|
| `verification_command` | string | The command that was executed. |
| `exit_code` | integer | Its exit code. `0` = pass; non-zero fails the step. |

Other typical keys: `output_non_empty`, `no_template_tokens`.

## Phase Ownership

Only the `run` skill writes trace records, one per step attempt, immediately
after that step's post-flight completes — including `skipped` and `failed`
steps. The compile phase never writes traces. Failure analysis and the
error-analysis UI read traces; nothing else mutates them.
