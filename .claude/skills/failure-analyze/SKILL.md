---
name: failure-analyze
description: Classify a failed Kamino run attempt into catalog failure modes using the run-failure-classifier agent and persist the analysis for error-analysis tooling. Use when the user wants to analyze why a run or attempt failed, classify failure modes, do error analysis on ledger failures, or find agent failure patterns.
---

# Failure Analyze

Use this skill after a failed attempt has been recorded in the ledger. It assembles the attempt's capsule, has `run-failure-classifier` explain the failure in catalog terms, and persists the analysis where the error-analysis UI can find it.

This is the "attribute failure" step of the learning loop: Run → Trace → Evaluate → **Attribute** → Improve. The Improve stage is the `improve-agent` skill — hand it this skill's analysis files when the user wants the failing agent fixed.

## Inputs

```xml
<task_detail_json>
.kamino/evals/tasks/details/<task_id>[-aN].json
</task_detail_json>

<dispatch_dir>
.kamino/dispatch-queue/<run-id>/
</dispatch_dir>

<record_id>task-outcome-...</record_id>
```

Optional:

```xml
<success_judgment_json>
.kamino/evals/tasks/outcomes/<task_id>[-aN]-success.json
</success_judgment_json>
```

## Uses

- Agent: `run-failure-classifier`
- Catalog: `.kamino/evals/tasks/failure-mode-catalog.md`

## Output

One analysis file per failed attempt:

```text
.kamino/evals/tasks/failures/<task_id>[-aN].json
```

Strict JSON containing:

- `schema_version` (`kamino451.failure-analysis.v1`)
- `task_id`, `attempt`, `record_id`, `task_detail_path`, `dispatch_dir`
- `classification` — the classifier's strict JSON (primary_failure_mode, failure_modes with layer/component_to_improve/evidence, recommended_fix, confidence)

## Rules

1. Analyze failed attempts only. If the attempt's judgment says `success: true`, stop and say there is nothing to analyze.
2. Assemble the full capsule before classifying: task detail, `trace.jsonl`, `execution-graph.md`, output files, test output from run evidence, and the success judgment. A capsule missing its trace is still analyzable — say so and expect lower confidence.
3. Require strict JSON from the classifier and validate every `slug` against the catalog file. A slug not in the catalog is a classifier error: re-invoke once with the violation named; if it repeats, fail.
4. Refuse to overwrite an existing analysis file for the same attempt.
5. Do not write the ledger, the task detail, or the trace. The analysis file is this skill's only write.
6. Do not invoke AutoResearch.

## Steps

1. Read the task detail JSON; extract `task_id` and `attempt` (default 1 when absent).
2. Locate the capsule pieces in `<dispatch_dir>`: `trace.jsonl`, `execution-graph.md`, `run-evidence.json`, `outputs/` and `work/` artifacts.
3. Verify the attempt actually failed (judgment `success: false`). Otherwise stop.
4. Invoke `run-failure-classifier` with the catalog path and every capsule path.
5. Validate the classifier output: strict JSON, allowed confidence, every slug present in the catalog.
6. Write `.kamino/evals/tasks/failures/<task_id>[-aN].json` with the fields listed above (create the directory if needed; refuse overwrite).
7. Report the primary failure mode, the component to improve, and the analysis path.

## Failure Conditions

Fail clearly if:

1. The task detail, dispatch dir, or record id is missing.
2. The attempt was not a failure.
3. The classifier returns non-JSON, an unknown slug twice, or no `primary_failure_mode`.
4. The analysis file already exists.

## Output Format

```markdown
# Failure Analysis — <task_id> (attempt <N>)

- Primary failure mode: `<slug>` (<layer>)
- Component to improve: <component>
- Recommended fix: <one line>
- Confidence: <low|medium|high>
- Analysis file: `.kamino/evals/tasks/failures/<task_id>[-aN].json`

## All classified modes
| Slug | Layer | Evidence |
|---|---|---|
```
