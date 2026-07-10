---
name: run-success-evaluate
description: Evaluate whether a completed Kamino run fully satisfied the original task — deterministically from ground-truth test results when the run evidence carries them, otherwise using the task-run-success-judge agent.
---

# Run Success Evaluate

Use this skill after `run` completes and before `task-outcome-record`. It judges task completion success separately from pipeline execution success.

It has two paths:

1. **Deterministic path (preferred when ground truth exists).** When the run evidence carries an authoritative ground-truth result — `verification_evidence.tests_passed` set by a declared verification command (e.g. a corpus task's unit tests) — derive the judgment with the deterministic script. No LLM is involved.
2. **LLM judge path.** For tasks without ground truth, judge with the `task-run-success-judge` agent as before.

## Inputs

```xml
<original_task>
Original user task.
</original_task>

<task_evaluation_json>
path/to/task-evaluation.json
</task_evaluation_json>

<run_evidence_json>
path/to/run-evidence.json
</run_evidence_json>

<output_files>
path/to/output-1.md
path/to/output-2.md
</output_files>
```

Optional:

```xml
<execution_graph>
.kamino/dispatch-queue/<run-id>/execution-graph.md
</execution_graph>
```

## Uses

- Agent: `task-run-success-judge` (LLM judge path only)
- Script: `.kamino/evals/scripts/success_judgment_from_tests.py` (deterministic path only)

Run the deterministic script only through `uv run`:

```bash
uv run .kamino/evals/scripts/success_judgment_from_tests.py \
  --run-evidence "<run-evidence.json>" \
  --output ".kamino/evals/tasks/outcomes/<task_id>-success.json" \
  --format json
```

## Output

Strict JSON only:

```json
{
  "success": false,
  "reason": "The output satisfies the summary requirement but omits required verification evidence.",
  "satisfied_requirements": ["summary"],
  "missing_requirements": ["verification evidence"],
  "partial_requirements": [],
  "unverifiable_requirements": [],
  "confidence": "high"
}
```

Preferred artifact path:

```text
.kamino/evals/tasks/outcomes/<task_id>-success.json
```

## Steps

1. Require the original task, task evaluation, run evidence, and output file paths.
2. Verify every output file exists and is non-empty before judging.
3. Read the run evidence and execution graph if provided.
4. Choose the path: if the run evidence's `verification_evidence` contains a boolean `tests_passed` produced by a declared verification command, use the deterministic script; otherwise invoke `task-run-success-judge` with the original task, task evaluation, run evidence, execution graph, output file paths, and output contents.
5. Require strict JSON from either path.
6. Validate that `success` is a boolean.
7. Return or write the strict JSON judgment artifact.

## Rules

1. Partial task completion is failure.
2. Missing evidence is failure when evidence was required.
3. Ground truth beats opinion: when a boolean `tests_passed` from a verification command is present, the deterministic script's judgment is authoritative — do not override it with an LLM judgment.
4. Never fabricate `tests_passed`; it may only originate from a verification command actually run by `run`.
5. Do not write the task outcome ledger.
6. Do not invoke AutoResearch.
7. Do not ask follow-up questions after the run; judge from evidence.

## Failure Conditions

Fail clearly if:

1. The original task is missing.
2. Run evidence is missing or malformed.
3. Any required output file is missing or empty.
4. The judge output is not strict JSON.
5. The judge output omits a binary `success` field.
