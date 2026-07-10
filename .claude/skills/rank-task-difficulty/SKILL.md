---
name: rank-task-difficulty
description: Place a new task against historical task anchors or rank a task set using Bradley-Terry pairwise comparisons before factory routing.
---

# Rank Task Difficulty

Use this skill after `task-evaluate` and before `task-outcome-lookup`. It produces pairwise difficulty placement evidence for factory routing.

## Inputs

For ranking mode:

```xml
<tasks_file>
.kamino/evals/tasks/sample-difficulty-tasks.json
</tasks_file>

<comparisons_file>
.kamino/evals/tasks/sample-difficulty-comparisons.json
</comparisons_file>
```

For similar-difficulty placement mode:

```xml
<task_evaluation_json>
path/to/task-evaluation.json
</task_evaluation_json>

<ranking_json>
path/to/ranking-output.json
</ranking_json>

<target_task_json>
path/to/target-task.json
</target_task_json>

<comparisons_file>
path/to/target-comparisons.json
</comparisons_file>
```

## Uses

- Agent: `bradley-terry-pairwise-ranking`
- Agent for missing comparisons only: `pairwise-difficulty-judge`
- Script: `.kamino/evals/scripts/bradley_terry_pairwise_ranking.py`

Run the script only through `uv run`:

```bash
uv run .kamino/evals/scripts/bradley_terry_pairwise_ranking.py rank --tasks "<tasks-file>" --comparisons "<comparisons-file>" --format json
uv run .kamino/evals/scripts/bradley_terry_pairwise_ranking.py similar --ranking "<ranking-json>" --target-task "<target-task-json>" --comparisons "<comparisons-file>" --neighbors "3" --format json
```

## Output

Ranking mode returns:

- hardest-first ranking
- estimated difficulty scores
- comparison coverage
- low-confidence comparisons

Similar-difficulty mode returns:

- `estimated_insertion_rank`
- `estimated_difficulty_score`
- nearest prior tasks
- pairwise comparison path
- low-confidence comparisons

Preferred placement artifact path:

```text
.kamino/evals/tasks/difficulty/<task_id>.json
```

## Steps

1. Require explicit input paths. Do not infer missing files.
2. Run rank mode when a task set is provided.
3. Run similar mode when a new task evaluation must be placed.
4. If the deterministic script returns `status: needs_comparison`, invoke `pairwise-difficulty-judge` for the requested pair only.
5. Append the judge comparison to the provided comparisons file.
6. Rerun the same similar-mode command.
7. Stop when `status: complete`.
8. Return parseable JSON and preserve any low-confidence comparisons.

## Rules

1. Do not invent pairwise comparisons.
2. Use `pairwise-difficulty-judge` only for missing comparisons requested by the deterministic script.
3. Do not write the task outcome ledger.
4. Do not invoke AutoResearch.
5. Run Python only through `uv run`.

## Failure Conditions

Fail clearly if:

1. A required task, ranking, target, or comparison file is missing.
2. The deterministic script exits non-zero.
3. Missing comparison data is needed but no judge output can be obtained.
4. Judge output is malformed or lacks `harder_task`, `confidence`, `reasoning`, or `key_factors`.
