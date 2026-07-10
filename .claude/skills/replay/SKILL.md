---
name: replay
description: Re-run a previously captured task (task detail + dispatch queue) against a specified agent version, model, or effort, judge it the same way, record it as a new attempt, and compare old versus new outcomes. Use when the user wants to retry a captured or failed task with an improved agent, replay a ledger record, A/B a blueprint change, or verify that an agent improvement actually fixes a recorded failure.
---

# Replay

Use this skill to **re-run a captured unit of work**. A captured task is fully reproducible: the task detail JSON preserves the task text and the compile decisions, the dispatch queue preserves the exact instantiated prompt, and the ledger preserves the judged outcome. Replay re-executes that same task — optionally with a changed agent blueprint version, model, or effort — records the new attempt honestly, and reports old-versus-new.

This is how an agent improvement is proven: not "the prompt looks better" but "the captured task that failed now passes, judged the same way."

## Inputs

One capture reference:

```xml
<task_detail_json>
.kamino/evals/tasks/details/<task_id>[-aN].json
</task_detail_json>
```

What to change (at least one, else it is a pure re-run):

```xml
<blueprint>.kamino/agents/ad-hoc/coding/python-coding-agent.md</blueprint>  <!-- e.g. an improved version -->
<model>sonnet</model>
<effort>high</effort>
```

## Rules

1. The task is immutable: replay uses the captured `task_text` (and, for corpus tasks, the same corpus files and `test_command`) exactly as recorded. Never reformulate the task.
2. The comparison must be fair: judge the replay with the same path the original used — deterministic ground-truth tests for corpus tasks, `task-run-success-judge` otherwise.
3. Replay is a new attempt, not a rewrite of history: compute the next free attempt number N for this task (from existing `details/<task_id>*` files), write a new task detail with `--attempt N`, a fresh dispatch run dir, trace, judgment, and ledger record. Existing artifacts are never modified.
4. Re-instantiate from the (possibly changed) blueprint via the normal machinery — copy, fill `{{...}}` variables with the captured input values (from the original instantiated agent / execution graph), bind the requested model/effort, verify no tokens remain.
5. The route decision for the replay records what changed and why (`replay of <record_id>: blueprint vX, model sonnet`).
6. Never invent inputs. If the original dispatch dir or captured inputs cannot be resolved, fail and name what is missing.
7. Do not invoke AutoResearch. Replay is the manual, single-task counterpart of that loop.

## Steps

1. Read the task detail JSON; extract task text, evaluation, difficulty placement, route decision (original blueprint, model, effort), and locate the original dispatch dir via the route decision's `agent_files_used`.
2. Read the original `execution-graph.md` and instantiated agent file to recover the exact input values (`GOAL`, input paths, verification command).
3. Determine the next attempt number N (scan `details/<task_id>*.json`).
4. Assemble the replay exactly like `evaluate-factory` does an attempt: new dispatch run dir + `work/` copies for corpus tasks, instantiate the target blueprint, fill the captured inputs, bind the requested model/effort, write `execution-graph.md`, record the route decision, call `task-detail-record --attempt N`.
5. Execute via `run` (trace + verification command), judge via `run-success-evaluate` (same path as the original), record via `task-outcome-record`.
6. If the replay failed, offer `failure-analyze` on the new attempt.
7. Report the comparison.

## Failure Conditions

Fail clearly if:

1. The task detail JSON is missing or malformed.
2. The original instantiated agent or execution graph cannot be found to recover inputs.
3. The requested blueprint does not exist or fails instantiation checks.
4. Any downstream skill fails on its own contract.

## Output Format

```markdown
# Replay — <task_id> (attempt <N>)

## Change under test
- Blueprint: <original> → <replayed>   Model: <original> → <replayed>   Effort: <original> → <replayed>

## Outcome comparison
| | Original (attempt <M>) | Replay (attempt <N>) |
|---|---|---|
| Model / effort | … | … |
| Execution | completed/failed | … |
| Tests / judgment | PASS/FAIL | … |
| Failure modes | … | … |
| Ledger record | <record_id> | <record_id> |

## Verdict
IMPROVED (fail → pass) | REGRESSED (pass → fail) | UNCHANGED (same result)
```
