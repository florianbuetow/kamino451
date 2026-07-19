---
name: run
description: Executes an assembled pipeline of instantiated Kamino agents in sequence using subagents, verifying each agent's output before proceeding to the next. Reads the run order from a dispatch-queue's execution-graph.md, prints the sequence to the terminal, and runs each step. Use when the user has a dispatch-queue (from taskgraph) or instantiated agent files and wants them executed, or asks to run/execute the assembled agents and confirm each step worked.
---

# Run

Use this skill to **execute** a pipeline of already-instantiated agents — the dispatch-queue produced by `taskgraph` (or a hand-listed set from `clone`). It dispatches each agent to a subagent, in order, and **verifies the output of each step before running the next**. It is the explicit execution step that `factory` (assemble-only) deliberately does not perform.

This skill does not select, fill, or create agents. The agents it runs must already be fully instantiated (no `{{...}}` tokens remaining). If they are not, stop and route back to `clone` / `taskgraph`.

## Inputs

The user must provide one of:

```xml
<dispatch_dir>
<!-- a dispatch-queue run directory produced by taskgraph -->
.kamino/dispatch-queue/<YYMMDD-HHMMSS>/
</dispatch_dir>
```

or, for an ad-hoc set:

```xml
<agents>
<!-- ordered list of instantiated agent files, in run order -->
.kamino/dispatch-queue/<ts>/01-research-agent.md
.kamino/dispatch-queue/<ts>/02-writing-agent.md
</agents>
```

Optional:

```xml
<force>true</force>   <!-- re-run steps even if their output file already exists -->
```

For outcome recording after execution, also provide:

```xml
<task_detail_json>
.kamino/evals/tasks/details/<task_id>.json
</task_detail_json>
```

Prefer `<dispatch_dir>`: it carries `execution-graph.md`, which is the authoritative run order. If neither input is given, ask for one. Do not guess.

## Paths

```text
.kamino/dispatch-queue/<ts>/execution-graph.md  # authoritative run order + dependencies
.kamino/dispatch-queue/<ts>/NN-<agent>.md        # instantiated agents, step-ordered
.kamino/dispatch-queue/<ts>/outputs/             # where each step's {{OUTPUT_FILE}} lands
.kamino/dispatch-queue/<ts>/trace.jsonl          # per-step trace records this skill appends
.kamino/scripts/template-replace-completed.sh    # assert a file has no {{...}} tokens
.kamino/evals/scripts/run_trace_write.py         # deterministic trace record writer
.kamino/evals/tasks/run-trace-schema.md          # trace record schema
.kamino/evals/tasks/outcomes/                    # where run-success-evaluate writes binary judgments
.kamino/evals/tasks/details/<task_id>.json       # durable task context written before execution
.kamino/evals/tasks/task-outcome-ledger.jsonl    # written only by task-outcome-record after judgment
.kamino/tasks/<task_id>/                         # per-task home of the instantiated agent + its run-report.json; for a single-clone ad-hoc set, use it as the dispatch dir (trace.jsonl, run-evidence.json)
```

## Execution model

- The run order comes from `execution-graph.md` (the "Run order" section). Do not re-infer order by guessing; trust the graph. Only fall back to filename sort if no graph is present.
- Each instantiated agent file is a complete prompt. Run it by dispatching it to a **subagent** (the Task / general-purpose agent), passing the agent file's full body as the subagent prompt.
- Use the agent's declared `model` **and `effort`** (from its frontmatter) for that subagent. The instantiated file's frontmatter carries the values bound at compile time (the blueprint's defaults unless the factory bound otherwise) — launch with exactly those, and deviate only with an explicit, stated reason.
- Agents communicate by file: each writes to the path in its `<OUTPUT_FILE>`, and a later step reads an earlier step's output **by that path**. So order matters and each output must exist before its consumer runs.

## Rules

1. Run steps strictly in the order given by `execution-graph.md`. A step starts only after every prior step it depends on has **passed** verification.
2. Fail fast. On the first step that fails verification, stop. Do not run any downstream step (its inputs would be missing or invalid). Report exactly which step failed and why.
3. Never run a non-instantiated agent. Before dispatch, confirm the agent file has no `{{...}}` tokens (use `template-replace-completed.sh` on the agent file). If any remain, fail — the agent was not fully instantiated.
4. Pre-flight each step: every input file the agent references must exist and be non-empty. If a required input file is missing, fail and name it.
5. Dispatch each agent as a subagent using its frontmatter `model` and `effort`. Respect both — never substitute a different model or effort silently; deviate only with an explicit, stated reason. If your subagent mechanism cannot set effort, say so rather than ignoring it.
6. Post-flight each step (all must hold, else the step FAILS):
   - the declared output file exists and is non-empty;
   - the output file contains no `{{...}}` tokens (verify with `template-replace-completed.sh`);
   - the subagent reported success — no error, no exception, no refusal;
   - if the agent emits an explicit pass/fail verdict (e.g. a fact-check that returns **FAIL**), a **FAIL** verdict counts as a failed step. Read the produced output to determine this;
   - if the execution graph declares a **verification command** for the step, run that exact command after the checks above — and run it even when an earlier check already failed the step, so the trace and run evidence always carry a real exit code (e.g. `tests_passed`) instead of a fabricated one. Exit code `0` passes; any non-zero exit code fails the step. Record the command and its exit code in the step's trace `verification` object, and record `tests_passed` (exit code `0`) in the run evidence's `verification_evidence` when the command is the task's ground truth. Never soften or reinterpret a non-zero exit.
7. Skip-if-exists: if a step's output file already exists and passes post-flight checks (including its verification command, if declared), skip running it and mark it `SKIPPED`. Override with `<force>true</force>`. Track skipped steps separately.
7a. Trace every step attempt. Immediately after each step's post-flight — whether the step is `OK`, `SKIPPED`, or `FAILED` — append one trace record to `<dispatch_dir>/trace.jsonl` via the deterministic writer (schema `kamino451.run-trace.v1`; see `.kamino/evals/tasks/run-trace-schema.md`): write the record JSON to a temp file, then run

```bash
uv run .kamino/evals/scripts/run_trace_write.py --trace "<dispatch_dir>/trace.jsonl" --record "<record.json>" --format json
```

   The record carries: `run_id`, `step`, `attempt` (increment on operational retries or escalation re-runs of the same step), `agent_file`, `blueprint` (when known), the launched `model` and `effort`, `started_at`/`ended_at`/`duration_seconds`, `status` (`ok`/`skipped`/`failed`), `output_path`, `verdict` (`PASS`/`FAIL`/null), `error` (null unless failed), `subagent_summary` (the subagent's returned result text, null for skipped steps), and the `verification` object. A trace-writer failure fails the run — do not continue with unrecorded steps.
8. Never invent values. This skill only runs what is already assembled; it does not fill missing inputs.
9. Do not modify the agent files. Treat them as read-only prompts.
10. **Print the run order to the terminal before executing** (see Output Format), so the sequence is visible up front.
11. Save or emit run evidence after execution. Execution success is not task success until `run-success-evaluate` returns a binary judgment.
12. Call `task-outcome-record` only after task detail JSON, run evidence, and a valid binary success judgment exist.
13. Do not invoke AutoResearch during normal run execution or outcome recording.

## Steps

1. Resolve the ordered list of agent files: if given a `<dispatch_dir>`, read its `execution-graph.md` "Run order" section for the authoritative order; otherwise use the `<agents>` list as given. The step files are the `NN-<agent>.md` files in the directory.
2. For each agent file, read its frontmatter (`agent_name`, `model`, `required_inputs`) and body, and record its declared output file path.
3. Validate the whole set first: every agent file must be fully instantiated (no `{{...}}`). If any is not, stop before running anything.
4. **Print the run order** to the terminal (numbered sequence with each agent and its output file).
5. For each step, in order:
   1. **Pre-flight** — confirm all input files exist and are non-empty.
   2. **Skip check** — if the output already exists and passes post-flight checks and `<force>` is not set, mark `SKIPPED`, emit the step's trace record (Rule 7a, status `skipped`), and continue.
   3. **Dispatch** — record the start time, then run the agent body as a subagent using its `model`. Capture the subagent's returned result.
   4. **Post-flight** — apply every check in Rule 6, including the step's verification command when the graph declares one.
   5. **Trace** — append the step's trace record per Rule 7a.
   6. On pass → mark `OK` and continue. On fail → mark `FAILED`, stop the pipeline (after its trace record is written).
6. Save or emit run evidence containing:
   - execution status;
   - ordered step statuses;
   - output paths;
   - template-variable verification results;
   - non-empty output checks;
   - verification command exit codes, when declared;
   - skipped steps;
   - the trace path (`<dispatch_dir>/trace.jsonl`).
7. If the pipeline executed or skipped all steps successfully, call `run-success-evaluate` with the original task, task evaluation, run evidence, execution graph, and output files from the task detail JSON.
8. If `run-success-evaluate` returns strict JSON with boolean `success`, call `task-outcome-record` with the task detail JSON, run evidence, and success judgment.
9. Return the run report, task success judgment, and outcome record path.

If a step fails, stop immediately. Do not call `task-outcome-record` unless task detail JSON, run evidence, and a valid binary success judgment exist.

## Failure Conditions

The run fails if:

1. Neither `<dispatch_dir>` nor `<agents>` is provided.
2. Any agent file still contains `{{...}}` tokens (not fully instantiated).
3. A required input file for a step is missing or empty.
4. A subagent errors, refuses, or does not produce its declared output file.
5. A produced output file is empty or still contains `{{...}}` tokens.
6. An agent's own verdict is **FAIL** (e.g. fact-check).

When the run fails, stop immediately, report the failing step and the specific check that failed, and quote the evidence (subagent error text or the failing output excerpt).

## Output Format

First, print the planned sequence to the terminal:

```markdown
# Pipeline Run — <dispatch_dir>

## Run order (executing in THIS sequence)
1. 01-<agent>.md  →  outputs/01-<agent>.md
2. 02-<agent>.md  →  outputs/02-<agent>.md
3. 03-<agent>.md  →  outputs/03-<agent>.md
```

Then, after execution, report:

```markdown
## Steps
| # | Agent | Model | Inputs verified | Output file | Result | Evidence |
|--:|---|---|---|---|---|---|
| 1 |  |  | yes | … | OK / SKIPPED / FAILED | … |

## Summary
- Ran: N    Skipped: M    Failed: K
- Final status: SUCCESS (all steps OK/SKIPPED) | FAILED at step <n>
- Task success: true / false / not judged
- Outcome record: <record id and ledger path, or not recorded with reason>
- Final output(s): <path(s) of the last successful step>

## On failure
- Failed step: <n> — <agent>
- Failed check: <which Rule 6 check>
- Evidence:
  > <quoted subagent error or failing output excerpt>
```

## Success Criteria

The skill succeeds only when:

1. The run order was printed to the terminal before execution.
2. Every step ran (or was justifiably skipped) in order.
3. Each executed step's output file exists, is non-empty, and contains no `{{...}}` tokens.
4. No step reported an error or a **FAIL** verdict.
5. Run evidence was saved or emitted.
6. The final step's output is reported as the pipeline result.
7. Task success is reported separately from execution success.
8. Outcome recording occurs only after task detail JSON, run evidence, and a binary `run-success-evaluate` judgment exist.
