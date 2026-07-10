---
name: evaluate-agent
description: Benchmarks one prescribed agent blueprint against a task corpus — the caller pins the agent, and every corpus task is solved by that same agent, never the factory's own picking. This measures one agent's quality, not the factory's routing. Requires a corpus directory and an agent blueprint path as input. Per attempt it compiles an isolated run (the solver sees only the problem statement; all test tiers stay outside its reach), dispatches the solving subagent, judges success deterministically, appends the ledger, and finally regenerates the HTML report pages. Use when the user wants to evaluate, benchmark, or test a specific agent blueprint against a corpus, or run an agent-vs-corpus sweep.
---

# Evaluate Agent

Use this skill to **benchmark one prescribed agent blueprint** against a corpus. The agent never varies across the sweep — the caller names the exact blueprint file up front, and every corpus task is compiled, solved, verified, and recorded against that same agent. Every sweep grows `.kamino/evals/tasks/task-outcome-ledger.jsonl` with honest evidence about that one agent's quality.

To evaluate the factory's automatic agent picking instead, use `evaluate-factory`.

**Physical test isolation is non-negotiable.** The solving agent's `work/` directory receives ONLY the problem statement (plus problem figures). Every test tier is staged under `verify/`, outside the agent's working area, and runs only at post-flight. `compile_run.py` asserts this layout and aborts the compile on any violation. An agent that can read its tests produces meaningless passes.

Every attempt is stamped `mode: prescribed` with this sweep's `sweep_id` (via `compile_run.py`) so the report pages can compare prescribed-agent sweeps against factory-routed sweeps.

## Inputs

```xml
<corpus_dir>
.kamino/evals/tasks/corpus-leetcode
</corpus_dir>

<agent>
.kamino/agents/library/coding/python-coding-agent-single-shot.md
</agent>
```

Optional:

```xml
<tasks>1-two-sum, 440-kth-smallest-in-lexicographical-order</tasks>     <!-- subset filter; default: all task dirs -->
<limit>3</limit>                                                        <!-- max corpus tasks this sweep -->
<models>haiku</models>                                                  <!-- default: one attempt, no escalation; ladder applies only if >1 model listed -->
<ranking_json>.kamino/evals/tasks/corpus-lc-ranking.json</ranking_json> <!-- default: the corpus's own ranking file -->
<effort>medium</effort>                                                 <!-- default shown -->
```

## Paths

```text
<corpus_dir>/<task-dir>/                          # task.md, meta.json, tests/, optional tests_hidden/ (NEVER read solution_reference.py)
<agent>                                           # the prescribed blueprint file — fixed for the whole sweep
.kamino/evals/scripts/compile_run.py              # isolated compile: work/=statement only, tiers → verify/
.kamino/evals/scripts/record_run.py               # post-flight: tiers, trace, evidence, judgment, ledger — one deterministic step
.kamino/evals/scripts/difficulty_calibration_report.py  # placement subcommand
.kamino/evals/scripts/generate_reports.sh         # regenerates all report pages (final step)
.kamino/dispatch-queue/<run_id>/                  # one isolated run dir per attempt
.kamino/evals/tasks/evaluations/<eval_id>.json    # task evaluation artifact
.kamino/evals/tasks/difficulty/<eval_id>.json     # difficulty placement artifact
.kamino/evals/tasks/candidates/<eval_id>.json     # candidate search artifact (staged for compile_run.py; does not influence agent choice here)
.kamino/evals/tasks/details/<eval_id>[-aN].json   # attempt-aware task details (written by compile_run.py)
.kamino/evals/tasks/outcomes/<eval_id>[-aN]-success.json
.kamino/evals/tasks/task-outcome-ledger.jsonl     # appended once per attempt (by record_run.py)
```

## Rules

1. The corpus is the boss: the task text is the exact content of the task's `task.md`. Never read or reveal `solution_reference.py` — not to yourself, not to the agent. Never copy it anywhere in a run dir.
2. Isolation is compiled, not promised: create run dirs ONLY via `compile_run.py`. Never hand-stage a run dir, never copy tests into `work/`, never point the agent at `verify/`.
3. Difficulty placement for a corpus task comes directly from the corpus ranking (`difficulty_calibration_report.py placement`) because corpus tasks ARE the anchors. Do not pairwise-compare a corpus task against itself. If the ranking file is missing, stop and route to `rank-task-difficulty`.
4. Mode discipline, not routing evidence: the prescribed agent never varies — every task and every attempt in the sweep uses the exact `<agent>` blueprint named at the start. Only the explicit `<models>` ladder may escalate the model; escalation never swaps the blueprint.
5. Verify before starting: confirm `<agent>` exists as a file and passes instantiation checks before compiling any task. A missing or broken blueprint stops the sweep before it begins, not partway through.
6. Dispatch each instantiated agent as a **subagent** whose entire instruction is: read the instantiated agent file and follow it exactly, returning only its OUTPUT_FORMAT JSON. Launch with the frontmatter `model` and `effort` (bound at compile time). Do not route sweep attempts through the `run` skill — `record_run.py` is the sweep's post-flight, trace, judgment, and ledger writer in one deterministic step; using both would double-record.
7. Judgment is deterministic: `record_run.py` runs every staged tier bounded at 300 s and derives success from the exit code. Never use the LLM judge for corpus attempts, never fabricate test results, never soften a failure. A timeout is a resource failure and stays a failure.
8. Record every attempt — failed attempts included. Failures are data, not embarrassments.
9. A task is DONE when an attempt succeeds, ESCALATED when it moves down the ladder, EXHAUSTED when the last ladder model fails. Continue the sweep across remaining tasks regardless of individual outcomes; stop only on infrastructure failure (a script erroring on its own valid inputs).
10. Always finish by regenerating the report pages (Step 4) — a sweep that leaves the dashboards stale is incomplete.
11. Do not invoke AutoResearch during a sweep.

## Steps

1. **Resolve the sweep.** Read `<corpus_dir>`; enumerate its task directories (each containing `task.md`); apply `<tasks>` filter and `<limit>`. Verify `<agent>` exists as a file before proceeding — a missing or broken blueprint stops the sweep immediately. Resolve the model ladder (default: `haiku` alone) and the ranking file. Mint the sweep id: `<yymmdd-HHMMSS>-prescribed-<corpus label>`. Print the plan: corpus, "prescribed: `<agent>`", ladder, task count.
2. **Per task, in directory order:**
   1. **Evaluate** — call `task-evaluate` with the task's `task.md`; save to `evaluations/<eval_id>.json` (reuse the staged artifact when it already exists — the eval id is the task-text hash, so it is stable). Note the returned `eval_id`; it keys everything downstream.
   2. **Place difficulty** — `uv run .kamino/evals/scripts/difficulty_calibration_report.py placement --ranking "<ranking_json>" --task-id "<corpus task id>" --format json` → `difficulty/<eval_id>.json` (reuse if staged).
   3. **Search candidates** — call `agent-candidate-search` with ledger, evaluation, placement (`--limit "10"`) → `candidates/<eval_id>.json`. Cold start (zero candidates) is fine; `compile_run.py` requires this staged artifact for the task-detail writer, but its result does not influence agent choice here — the agent is `<agent>`, prescribed, full stop.
   4. **Attempt loop** — for attempt N = 1..len(ladder):
      1. **Compile** — `uv run .kamino/evals/scripts/compile_run.py --corpus-dir <corpus_dir> --task-id <task dir> --eval-id <eval_id> --attempt N --model <ladder[N]> --effort <effort> --blueprint <agent> --mode prescribed --sweep-id <sweep id> --format json`. It stages the isolated layout, instantiates and binds the agent copy, writes the execution graph, route decision, and task detail. Note the printed `run_dir` and `agent_file`.
      2. **Dispatch** — capture `started_at` (UTC, ISO-8601); spawn the subagent: "You are executing an instantiated Kamino agent. Read `<agent_file>` and follow its instructions exactly. Your final message must be only the JSON its OUTPUT_FORMAT requires." Use the agent file's frontmatter model/effort.
      3. **Record** — `uv run .kamino/evals/scripts/record_run.py --task-id <eval_id> --run-dir <run_dir> --model <ladder[N]> --effort <effort> --started-at <started_at> --ended-at now --attempt N --format json`. It runs every staged tier (bounded), writes trace + evidence + deterministic judgment, and appends the ledger. Note `success` and the record id.
      4. On success → DONE, leave the attempt loop. On failure with a next ladder model → ESCALATE to attempt N+1. On failure at the last model → EXHAUSTED.
3. **Report the sweep table** (Output Format below).
4. **Regenerate reports** — `bash .kamino/evals/scripts/generate_reports.sh`. This rebuilds the calibration reports, `errors.html`, `difficulty.html`, and `sweeps.html` (the factory-vs-corpus and agent-vs-corpus comparison) from the current ledger. Print where they landed.

## Failure Conditions

The sweep fails (stop everything, report) only if:

1. `<corpus_dir>` is missing, has no task directories, or a referenced task lacks `task.md` or `tests/`.
2. The `<agent>` blueprint file does not exist or fails instantiation checks.
3. The ranking file is missing or malformed (route to `rank-task-difficulty` first).
4. `compile_run.py`, `record_run.py`, or another deterministic script exits non-zero on valid inputs — including an isolation-violation abort.
5. The report regeneration step fails.

An individual attempt failing its tests is NOT a sweep failure — it is the data.

## Output Format

```markdown
# Agent Evaluation — <blueprint name> vs <corpus>

- Corpus: <corpus_dir> (<n> tasks selected)
- Mode: PRESCRIBED (<blueprint path>)
- Ladder: haiku

## Attempts
| Corpus task | Difficulty (BT) | Attempt | Model | Tests | Ledger record | Status |
|---|---:|---:|---|---|---|---|
| 1-two-sum | -0.85 | 1 | haiku | PASS | task-outcome-… | DONE |
| 440-kth-smallest… | 1.68 | 1 | haiku | FAIL | task-outcome-… | EXHAUSTED |

## Summary
- Tasks swept: N   DONE: a   EXHAUSTED: b
- Attempts: total t   per-model pass rate: haiku x/y
- Ledger: `.kamino/evals/tasks/task-outcome-ledger.jsonl` (+t records)
- Reports regenerated: sweeps.html, errors.html, difficulty.html, calibration reports
```

## Success Criteria

The sweep succeeds when every selected corpus task ended DONE or EXHAUSTED under the one prescribed agent, every attempt produced a task detail, trace, deterministic judgment, and ledger record, the report table matches the ledger, and the report pages were regenerated.
