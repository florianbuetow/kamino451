---
name: evaluate-factory
description: Evaluates the agent factory itself against a task corpus — for every corpus task the factory picks the agent and model (candidate search + weighted-majority routing, cheap-first escalation), solves it in an isolated run dir, verifies against every test tier, and records the outcome. This measures the factory's routing quality, not one agent. Requires a corpus directory as input. Per attempt it compiles an isolated run (the solver sees only the problem statement; all test tiers stay outside its reach), dispatches the solving subagent, judges success deterministically, appends the ledger, and finally regenerates the HTML report pages. Use when the user wants to evaluate the factory / auto agent picking against a corpus, run a factory eval sweep, seed the factory with data, or continue the data flywheel.
---

# Evaluate Factory

Use this skill to **evaluate the factory's automatic agent picking** against a corpus. For each task the factory chooses the agent and model exactly as it would for a real task — candidate search over the ledger plus `route_recommendation.py` (weighted-majority over successful records; cold start → cheapest model) — then solves, verifies, and records. Every sweep grows `.kamino/evals/tasks/task-outcome-ledger.jsonl` with honest evidence about the factory's routing quality.

To benchmark **one specific agent** against a corpus instead, use `evaluate-agent`.

**Physical test isolation is non-negotiable.** The solving agent's `work/` directory receives ONLY the problem statement (plus problem figures). Every test tier is staged under `verify/`, outside the agent's working area, and runs only at post-flight. `compile_run.py` asserts this layout and aborts the compile on any violation. An agent that can read its tests produces meaningless passes.

Every attempt is stamped `mode: auto` with this sweep's `sweep_id` (via `compile_run.py`) so the report pages can compare factory-routed sweeps against prescribed-agent sweeps.

## Inputs

```xml
<corpus_dir>
.kamino/evals/tasks/corpus-leetcode
</corpus_dir>
```

Optional:

```xml
<tasks>1-two-sum, 440-kth-smallest-in-lexicographical-order</tasks>     <!-- subset filter; default: all task dirs -->
<limit>3</limit>                                                        <!-- max corpus tasks this sweep -->
<models>haiku, sonnet</models>                                          <!-- escalation ladder; default shown -->
<ranking_json>.kamino/evals/tasks/corpus-lc-ranking.json</ranking_json> <!-- default: the corpus's own ranking file -->
```

## Paths

```text
<corpus_dir>/<task-dir>/                          # task.md, meta.json, tests/, optional tests_hidden/ (NEVER read solution_reference.py)
.kamino/evals/scripts/compile_run.py              # isolated compile: work/=statement only, tiers → verify/
.kamino/evals/scripts/record_run.py               # post-flight: tiers, trace, evidence, judgment, ledger — one deterministic step
.kamino/evals/scripts/route_recommendation.py     # model routing over the ledger
.kamino/evals/scripts/difficulty_calibration_report.py  # placement subcommand
.kamino/evals/scripts/generate_reports.sh         # regenerates all report pages (final step)
.kamino/dispatch-queue/<run_id>/                  # one isolated run dir per attempt
.kamino/evals/tasks/evaluations/<eval_id>.json    # task evaluation artifact
.kamino/evals/tasks/difficulty/<eval_id>.json     # difficulty placement artifact
.kamino/evals/tasks/candidates/<eval_id>.json     # candidate search artifact
.kamino/evals/tasks/details/<eval_id>[-aN].json   # attempt-aware task details (written by compile_run.py)
.kamino/evals/tasks/outcomes/<eval_id>[-aN]-success.json
.kamino/evals/tasks/task-outcome-ledger.jsonl     # appended once per attempt (by record_run.py)
```

## Rules

1. The corpus is the boss: the task text is the exact content of the task's `task.md`. Never read or reveal `solution_reference.py` — not to yourself, not to the agent. Never copy it anywhere in a run dir.
2. Isolation is compiled, not promised: create run dirs ONLY via `compile_run.py`. Never hand-stage a run dir, never copy tests into `work/`, never point the agent at `verify/`.
3. Difficulty placement for a corpus task comes directly from the corpus ranking (`difficulty_calibration_report.py placement`) because corpus tasks ARE the anchors. Do not pairwise-compare a corpus task against itself. If the ranking file is missing, stop and route to `rank-task-difficulty`.
4. Routing must cite its evidence: candidate matches, the route recommendation, or the cold-start default. Never pick a model or blueprint silently.
5. Dispatch each instantiated agent as a **subagent** whose entire instruction is: read the instantiated agent file and follow it exactly, returning only its OUTPUT_FORMAT JSON. Launch with the frontmatter `model` and `effort` (bound at compile time). Do not route sweep attempts through the `run` skill — `record_run.py` is the sweep's post-flight, trace, judgment, and ledger writer in one deterministic step; using both would double-record.
6. Judgment is deterministic: `record_run.py` runs every staged tier bounded at 300 s and derives success from the exit code. Never use the LLM judge for corpus attempts, never fabricate test results, never soften a failure. A timeout is a resource failure and stays a failure.
7. Record every attempt — failed attempts included. Failures are data, not embarrassments.
8. A task is DONE when an attempt succeeds, ESCALATED when it moves down the ladder, EXHAUSTED when the last ladder model fails. Continue the sweep across remaining tasks regardless of individual outcomes; stop only on infrastructure failure (a script erroring on its own valid inputs).
9. Always finish by regenerating the report pages (Step 4) — a sweep that leaves the dashboards stale is incomplete.
10. Do not invoke AutoResearch during a sweep.

## Steps

1. **Resolve the sweep.** Read `<corpus_dir>`; enumerate its task directories (each containing `task.md`); apply `<tasks>` filter and `<limit>`. Resolve the model ladder and the ranking file. Mint the sweep id: `<yymmdd-HHMMSS>-auto-<corpus label>`. Print the plan: corpus, "factory-routed", ladder, task count.
2. **Per task, in directory order:**
   1. **Evaluate** — call `task-evaluate` with the task's `task.md`; save to `evaluations/<eval_id>.json` (reuse the staged artifact when it already exists — the eval id is the task-text hash, so it is stable). Note the returned `eval_id`; it keys everything downstream.
   2. **Place difficulty** — `uv run .kamino/evals/scripts/difficulty_calibration_report.py placement --ranking "<ranking_json>" --task-id "<corpus task id>" --format json` → `difficulty/<eval_id>.json` (reuse if staged).
   3. **Search candidates** — call `agent-candidate-search` with ledger, evaluation, placement (`--limit "10"`) → `candidates/<eval_id>.json`. Cold start (zero candidates) is fine.
   4. **Route** — `uv run .kamino/evals/scripts/route_recommendation.py --ledger <ledger> --task-eval <evaluation> --difficulty <placement> --format json`; start the ladder at the recommended model when it is in the ladder, citing the evidence; pick the blueprint from fitting candidates, else the default single-shot coding blueprint (`.kamino/agents/library/coding/python-coding-agent-single-shot.md`).
   5. **Attempt loop** — for attempt N = 1..len(ladder):
      1. **Compile** — `uv run .kamino/evals/scripts/compile_run.py --corpus-dir <corpus_dir> --task-id <task dir> --eval-id <eval_id> --attempt N --model <ladder[N]> --effort <effort> --blueprint <blueprint> --mode auto --sweep-id <sweep id> --format json`. It stages the isolated layout, instantiates and binds the agent copy, writes the execution graph, route decision, and task detail. Note the printed `run_dir` and `agent_file`.
      2. **Dispatch** — capture `started_at` (UTC, ISO-8601); spawn the subagent: "You are executing an instantiated Kamino agent. Read `<agent_file>` and follow its instructions exactly. Your final message must be only the JSON its OUTPUT_FORMAT requires." Use the agent file's frontmatter model/effort.
      3. **Record** — `uv run .kamino/evals/scripts/record_run.py --task-id <eval_id> --run-dir <run_dir> --model <ladder[N]> --effort <effort> --started-at <started_at> --ended-at now --attempt N --format json`. It runs every staged tier (bounded), writes trace + evidence + deterministic judgment, and appends the ledger. Note `success` and the record id.
      4. On success → DONE, leave the attempt loop. On failure with a next ladder model → ESCALATE to attempt N+1. On failure at the last model → EXHAUSTED.
3. **Report the sweep table** (Output Format below).
4. **Regenerate reports** — `bash .kamino/evals/scripts/generate_reports.sh`. This rebuilds the calibration reports, `errors.html`, `difficulty.html`, and `sweeps.html` (the factory-vs-corpus and agent-vs-corpus comparison) from the current ledger. Print where they landed.

## Failure Conditions

The sweep fails (stop everything, report) only if:

1. `<corpus_dir>` is missing, has no task directories, or a referenced task lacks `task.md` or `tests/`.
2. The ranking file is missing or malformed (route to `rank-task-difficulty` first).
3. `compile_run.py`, `record_run.py`, or another deterministic script exits non-zero on valid inputs — including an isolation-violation abort.
4. The report regeneration step fails.

An individual attempt failing its tests is NOT a sweep failure — it is the data.

## Output Format

```markdown
# Factory Evaluation — <sweep id>

- Corpus: <corpus_dir> (<n> tasks selected)
- Mode: AUTO (factory-routed)
- Ladder: haiku → sonnet

## Attempts
| Corpus task | Difficulty (BT) | Attempt | Model | Tests | Ledger record | Status |
|---|---:|---:|---|---|---|---|
| 1-two-sum | -0.85 | 1 | haiku | PASS | task-outcome-… | DONE |
| 440-kth-smallest… | 1.68 | 1 | haiku | FAIL | task-outcome-… | ESCALATED |
| 440-kth-smallest… | 1.68 | 2 | sonnet | PASS | task-outcome-… | DONE |

## Summary
- Tasks swept: N   DONE: a   EXHAUSTED: b
- Attempts: total t   per-model pass rates: haiku x/y, sonnet u/v
- Ledger: `.kamino/evals/tasks/task-outcome-ledger.jsonl` (+t records)
- Reports regenerated: sweeps.html, errors.html, difficulty.html, calibration reports
```

## Success Criteria

The sweep succeeds when every selected corpus task ended DONE or EXHAUSTED, every attempt produced a task detail, trace, deterministic judgment, and ledger record, the report table matches the ledger, and the report pages were regenerated.
