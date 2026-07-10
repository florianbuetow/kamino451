# Kamino451 — Agent Factory

A self-contained demo of an **agent factory**: a system that turns task descriptions
into dispatched agent runs, records every outcome, and uses the accumulated data to
route future tasks to the right agent and model.

- **`.kamino/`** is the factory: agent blueprints, the deterministic engine scripts,
  and the (initially empty) data locations that fill up as the factory runs — eval
  corpora, run capsules, the outcome ledger, reports.
- **`.claude/`** is the control plane: the factory's skills (slash commands) and the
  judge/classifier agent definitions that Claude Code executes.
- Solving agents are **generic subagents instantiated from markdown blueprint files**
  in `.kamino/agents/` — the factory writes a bound copy of a blueprint into a run
  directory and a subagent executes exactly that file.

The learning loop this repo demonstrates:

```text
generate traces -> error analysis -> failure modes -> task difficulty ranking -> agent+model matching
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — runs all Python scripts and pytest (`uv run …`)
- [just](https://github.com/casey/just) — command runner
- [Claude Code](https://claude.com/claude-code) — executes the factory skills and
  dispatches solving agents

## Quick start

```bash
just help      # list commands
just run       # factory workflow smoke validation (contract + integrity tests)
just check     # validate blueprint template contracts
just test      # run the full pytest suite
just ci        # check + test
```

## How the factory works

Every task goes through a **compile → run** split:

1. **Compile** (never writes the ledger): `task-evaluate` scores the task →
   `rank-task-difficulty` places it against the Bradley-Terry corpus anchors →
   `agent-candidate-search` finds prior successes on similar tasks →
   route decision binds a blueprint + model/effort into an instantiated agent file
   under `.kamino/dispatch-queue/<run_id>/` → `task-detail-record` captures the
   full decision context.
2. **Run** (sole ledger writer): the instantiated agent executes → post-flight runs
   the task's **verification command** (pytest suites are ground truth) → a trace and
   run evidence are written → deterministic success judgment → `task-outcome-record`
   appends to the ledger.

Key invariants:

- Blueprint frontmatter `model`/`effort` are defaults; instantiation may bind
  different values (recorded with the reason). `run` trusts the instantiated file.
- The outcome ledger (`.kamino/evals/tasks/task-outcome-ledger.jsonl`) is
  **append-only**, written exclusively via `task_outcome_ledger_write.py`.
- Where ground truth exists (corpus tasks), success is the test command's exit code
  (`success_judgment_from_tests.py`); the LLM judge is only for tasks without tests.
- Model policy is **cheap-first**: sweeps start on the cheapest model and escalate
  only on failure; stronger models are reserved for authoring, judging, and
  classification.

## Skills (slash commands in Claude Code)

| Group | Skill | Purpose |
|---|---|---|
| Lifecycle | `factory` | Assembly entry point: evaluate → rank → route → instantiate → task-detail. **Assembles only** — execution stays with `run` / the evaluate skills |
| | `createblueprint` | Author a new agent blueprint (`<<authoring>>` markers) |
| | `clone` / `taskgraph` | Instantiate blueprints (`{{invocation}}` markers) for one task / a task graph |
| | `run` | Execute an instantiated run dir with post-flight verification + trace |
| | `replay` | Re-run a captured task with a different agent/model and compare outcomes |
| | `evaluate-factory` | Sweep a corpus with the factory picking agent+model per task (isolated runs, cheap-first escalation) |
| | `evaluate-agent` | Benchmark one prescribed agent blueprint against a corpus (isolated runs) |
| | `create-eval-corpus` | Ingest a problem source into a standard-shape eval corpus via a generated, git-tracked builder |
| Evaluation | `task-evaluate` | Deterministic + LLM-judge task scoring |
| | `rank-task-difficulty` | Bradley-Terry placement against corpus anchors |
| | `agent-candidate-search` | Ledger search for similar solved tasks (cold-start tolerant) |
| | `run-success-evaluate` | Deterministic (tests) or LLM success judgment |
| | `failure-analyze` | Classify a failed capsule into catalog failure modes |
| | `improve-agent` | Improve a blueprint's prompt via keep-or-revert optimization in an isolated workspace (the loop's "Improve" stage) |
| | `task-detail-record` / `task-outcome-record` / `task-outcome-lookup` | Ledger I/O |
| Utility | `check` | Validate blueprint template contracts |
| | `inventory` | List curated library-tier agents with their purpose |

Judge/classifier agents used by these skills live in `.claude/agents/`:
`task-llm-judge`, `pairwise-difficulty-judge`, `task-run-success-judge`,
`run-failure-classifier`, `bradley-terry-pairwise-ranking`, and `task-evaluator`
(the `task-evaluate` LLM+deterministic merge driver). `code-reviewer` is a
general-purpose review agent, not wired into the routing loop. Four more —
`autoresearch-agent-improver`, `autoresearch-eval-author`,
`autoresearch-llm-evaluator`, `autoresearch-program-author` — drive the
AutoResearch prompt-optimization loop described below.

## Eval corpora

The factory starts with **no corpora**. Corpora are created with the
`create-eval-corpus` skill, which authors a source-specific builder into
`.kamino/evals/ingest/<corpus-name>/` (tracked in git) and emits the corpus to
`.kamino/evals/tasks/corpus-<name>/`.

Every task directory has the same shape:

```text
<task-id>/
  task.md                    # problem statement + signature (all the solver sees)
  meta.json                  # task_id, title, intended_difficulty, test_command
  solution_reference.py      # oracle-verified reference (never shown to solvers)
  tests/test_solution.py     # visible tier (ground truth)
  tests_hidden/test_hidden.py# OPTIONAL hidden tier: constraint-scale stress,
                             # adversarial edges, complexity smoke
```

Hidden tiers exist because memorized solutions can pass visible tests while
carrying latent defects (recursion at scale, O(n²) where O(n log n) is required).
The hidden tier turns those defects into recorded failures.

**Corpus integrity is enforced by one discovery-based test** — for every corpus
present in the factory: the index reconciles with disk, every reference passes the
task's own `test_command`, every suite fails without a solution, and the statement
leaks no grader content. No per-corpus test files exist; new corpora are covered
automatically (and it skips when no corpus is present):

```bash
uv run pytest .kamino/tests/test_corpus_integrity_any.py -q
```

## Running evals

Two questions, one recipe each — both **per corpus**, both in Claude Code, both
ending with the dashboards regenerated automatically:

### "How good is the factory?" — evaluate factory vs corpus

```text
/evaluate-factory
<corpus_dir>.kamino/evals/tasks/corpus-<name></corpus_dir>
<limit>10</limit>                <!-- optional: cap tasks this sweep -->
<tasks><task-dir>, <task-dir></tasks>  <!-- optional: subset filter -->
<models>haiku, sonnet</models>   <!-- optional: escalation ladder (default shown) -->
```

The factory picks agent + model **per task** (candidate search +
weighted-majority routing over the ledger, cheap-first escalation). This
measures routing quality. Requires a corpus under `.kamino/evals/tasks/` with
its difficulty pipeline staged (step 1 below) — ingest one with
`/create-eval-corpus`.

### "How good is this agent?" — evaluate agent vs corpus

```text
/evaluate-agent
<corpus_dir>.kamino/evals/tasks/corpus-<name></corpus_dir>
<agent>.kamino/agents/library/coding/python-coding-agent-single-shot.md</agent>
<models>haiku</models>           <!-- optional: single attempt by default; list >1 for a ladder -->
```

The prescribed blueprint solves **every** task — no routing, agent never
varies. This measures one agent. Use it to benchmark a blueprint (or a model
binding) before promoting it to the library tier. The blueprint must be
corpus-compatible: required inputs exactly `GOAL`/`PROBLEM`/`OUTPUT_FILE`, like
the single-shot coding agent — blueprints that demand test access (e.g. a
`TEST_FILE` input) are incompatible with isolated sweeps by design.

### New corpus first?

`/create-eval-corpus` interviews for the source folder, answer source, and
answer contract; authors a builder into `.kamino/evals/ingest/<corpus-name>/`;
runs it; and gate-checks the result. Then stage the difficulty pipeline once
(step 1 below) and run either sweep against it.

### Where the results land

- **Ledger** — one record per attempt, stamped `mode: auto|prescribed` +
  `sweep-id` (in the run dir's `route-decision.json`).
- **`sweeps.html`** — per-sweep cards (pass rates, per-task table) and the
  **factory-auto vs prescribed-agent comparison per corpus**: run both sweep
  kinds on the same corpus and this page answers "does the factory's picking
  beat the pinned agent?".
- `errors.html` / `difficulty.html` / calibration reports — regenerated at
  sweep end; `bash .kamino/evals/scripts/generate_reports.sh` rebuilds everything
  from the ledger anytime.

### Everything at once

```bash
just ci        # contracts + the full pytest suite (corpus integrity tests
               # run when corpora are present; on a fresh factory they skip)
```

The sections below are the per-attempt mechanics both sweep skills drive
underneath — reach for them directly only to run or debug a single attempt.

### 1. Stage the difficulty pipeline (once per corpus)

Evaluations, ranking, placements, and candidate searches must exist under
`.kamino/evals/tasks/{evaluations,difficulty,candidates}/` before compiling runs.
For a freshly ingested corpus:

1. **Evaluate tasks** — batched sonnet judges apply the `task-llm-judge` rubric,
   then merge with the deterministic evaluator
   (`uv run .kamino/evals/scripts/evaluate_task.py --file <task.md> --format json`);
   merged files land in `evaluations/<eval_task_id>.json`.
2. **Rank difficulty** — pairwise-judge a sparse circle design of task pairs
   (`pairwise-difficulty-judge` rubric), then fit:
   `uv run .kamino/evals/scripts/bradley_terry_pairwise_ranking.py rank --tasks <tasks.json> --comparisons <comparisons.json> --format json`
   → `corpus-<name>-ranking.json`.
3. **Place + search** — per task:
   `difficulty_calibration_report.py placement --ranking <ranking> --task-id <id>`
   → `difficulty/<eval_id>.json`, and
   `agent_candidate_search.py --ledger <ledger> --task-eval <eval> --difficulty <placement>`
   → `candidates/<eval_id>.json`.

### 2. Compile a run (physical test isolation)

`compile_run.py` stages one isolated attempt for any standard-shape corpus
produced by `/create-eval-corpus`, printing
`{"run_dir": …, "agent_file": …, "run_id": …}`:

```bash
uv run .kamino/evals/scripts/compile_run.py \
  --corpus-dir <corpus> --task-id <task-dir> --eval-id <eval_id> --attempt N \
  --model haiku --effort medium --mode auto|prescribed --sweep-id <id> \
  --format json
```

which creates:

```text
.kamino/dispatch-queue/<run_id>/
  01-python-coding-agent-single-shot.md   # blueprint bound to this task (haiku/medium)
  work/task.md                            # the ONLY thing the solver sees (+ figures)
  verify/tests/ [+ verify/tests_hidden/]  # every test tier, staged outside the solver's workdir
  execution-graph.md · route-decision.json
```

Isolation is filesystem-level and asserted, not promised: `work/` never contains
`solution_reference.py` or any test tier; the verification command targets
`verify/` and runs only at post-flight.

### 3. Dispatch the solving agent

In Claude Code, spawn a subagent — using the agent file's frontmatter `model` and
`effort`, bound at compile time — whose entire instruction is the instantiated file:

> You are executing an instantiated Kamino agent. Read
> `<run_dir>/01-python-coding-agent-single-shot.md` and follow its instructions
> exactly. Your final message must be only the JSON its OUTPUT_FORMAT requires.

The agent writes `work/solution.py` in a single shot (no test access, no revision).

### 4. Record the outcome

`record_run.py` is the post-flight driver for any isolated run dir: it copies the
solution into `verify/`, runs **both tiers** (bounded at 300 s — a solution that
cannot finish a suite the reference completes in <1 s has failed on resources),
writes the trace and run evidence, produces the deterministic success judgment,
and appends the ledger record:

```bash
uv run .kamino/evals/scripts/record_run.py \
  --task-id <eval_id> --run-dir <run_dir> --model haiku --effort medium \
  --started-at <iso> --ended-at now --attempt N --format json
```

### 5. Analyze failures

For every failed record, classify it into catalog failure modes
(`.kamino/evals/tasks/failure-mode-catalog.md`) via the `failure-analyze` skill or a
batched sonnet judge applying `.claude/agents/run-failure-classifier.md`. Analyses
land in `.kamino/evals/tasks/failures/<task_id>[-a<N>].json` (attempt 1 has no
suffix) and are picked up by the dashboards automatically.

### 6. Regenerate reports

```bash
bash .kamino/evals/scripts/generate_reports.sh
```

The sweep skills run this automatically as their final step; invoke it directly
only after out-of-sweep changes (failure classifications, pruning). It rebuilds —
idempotently,
from the current ledger and whatever corpora it discovers — the derived artifacts
in `.kamino/evals/tasks/`:

| Artifact | Contents |
|---|---|
| `calibration-report-<name>.md` (one per discovered corpus) | Per-corpus: BT rank vs attempts, solve rates, difficulty↔success correlations (task- and attempt-level) |
| `errors.html` | Every ledger attempt: filterable table, per-attempt drill-down (trace, evidence, failure analysis, LLM trace review), difficulty chart, failure-mode labeling with export |
| `difficulty.html` | Difficulty calibration joined with outcomes, including the **failure ↔ difficulty alignment** table: per failure, the BT rank, the pre-run evaluation signal, the classified failure mode, and whether the difficulty signal predicted the failure |
| `sweeps.html` | Per-sweep results (sweep id, mode, ladder, task-by-task outcomes) and the **factory-auto vs prescribed-agent** comparison per corpus |

On a factory with no corpus discovered yet, only `sweeps.html` is rebuilt; the
corpus-joined artifacts (calibration reports, `errors.html`, `difficulty.html`)
appear once at least one corpus ships its ranking + index. Open the pages directly
(`open .kamino/evals/tasks/errors.html`) — all three are self-contained static HTML.

## Routing (using the data)

`route_recommendation.py` recommends in a three-step chain: **success-rate policy**
first (an agent+model+effort combination qualifies when its success rate for the
task type clears the threshold over enough attempts — both set in
`.kamino/factory-config.json` — picked cheapest-first among qualifiers), then
**weighted-majority fallback** over successful records (weight = task-type match ×
inverse difficulty distance, ties cheapest-first), then **cold start** → the
cheapest model:

```bash
uv run .kamino/evals/scripts/route_recommendation.py \
  --ledger .kamino/evals/tasks/task-outcome-ledger.jsonl \
  --task-eval .kamino/evals/tasks/evaluations/<eval_id>.json \
  --difficulty .kamino/evals/tasks/difficulty/<eval_id>.json \
  --format json
```

## AutoResearch: prompt optimization

A second, meta-level learning loop, separate from the task-routing loop above:
instead of picking which agent/model solves a task, it **improves a solving
agent's prompt itself** via keep-or-revert iteration, model held fixed. It is the
"Improve" stage of the loop `failure-analyze` names (Run → Trace → Evaluate →
Attribute → Improve), entered deliberately through the **`improve-agent`** skill —
never during normal task completion.

Nothing is pre-created: each run builds a fresh, gitignored workspace under
`.kamino/auto-research/<timestamp>/`. The skill seeds `agent.md` from the target
blueprint, and the `autoresearch-program-author` / `autoresearch-eval-author`
agents author the per-problem harness (`program.md`, `eval.py`, `tasks.json`,
`runner-config.json`, and the runner adapter for the task type). Only `agent.md`
is editable once the baseline runs; `simulate` mode is offline and free, `real`
mode spends live tokens against `claude -p`. A reference coding harness lives in
`.kamino/tests/fixtures/auto-research/`.

Driver (invoked by the skill; `$workspace` is the run's own directory):

```bash
uv run .kamino/evals/scripts/auto_research.py init --workspace "$workspace"
uv run .kamino/evals/scripts/auto_research.py evaluate-change --workspace "$workspace"
```

The wrapper commits improving `agent.md` edits and reverts non-improving ones.
Improved prompts earn ledger records only afterwards — proven per task via
`replay`, then baked into a new blueprint version via `createblueprint`.

## Maintenance

Run directories referenced by any ledger record are **replay capsules** and are kept
indefinitely (see `.kamino/evals/tasks/dispatch-queue-retention.md`). Unreferenced
dirs may be pruned — explicitly, never automatically:

```bash
uv run .kamino/evals/scripts/prune_dispatch_queue.py \
  --dispatch-dir .kamino/dispatch-queue \
  --ledger .kamino/evals/tasks/task-outcome-ledger.jsonl \
  --format json            # list only; add --apply to delete
```

## Repository layout

```text
.claude/            factory skills (slash commands) + judge/classifier agents
.kamino/            THE FACTORY — machinery plus its own (initially empty) data locations
  agents/           blueprints: library/ (promoted) and ad-hoc/, the authoring
                    scaffold (agent-blueprint.template.md), index.md registry
  auto-research/    per-run prompt-optimization workspaces (gitignored; created by
                    /improve-agent, see AutoResearch section)
  blueprints/       empty placeholder (unused)
  dispatch-queue/   one directory per run attempt (replay capsules); gitignored;
                    starts empty, fills as the factory runs
  evals/ingest/     generated corpus builders + provenance, one dir per corpus
                    (created on first ingestion by /create-eval-corpus)
  evals/scripts/    deterministic pipeline scripts (evaluate, rank, record, report, UI)
  evals/tasks/      schema docs + samples; corpora, ledger, evaluations, difficulty,
                    candidates, outcomes, failures, reports accumulate here with use
  scripts/          template-replace / contract-check shell scripts
  tests/            the factory's pytest suite + fixtures: script contracts, workflow
                    contracts, corpus integrity (skips when no corpus is present)
docs/               slides (docs/slides/slides.md), specs, ideation, research;
                    gitignored, not part of the tracked repo
```

## Test setup notes

- Run pytest through uv: `uv run pytest` (add `--project /path/to/kamino451` when
  invoking from outside the repo).
- The suite includes contract tests for every pipeline script, workflow contract
  tests for the skills, and the corpus integrity tests described above.
- `just check` validates blueprint template contracts
  (`.kamino/scripts/template-variable-checks.sh`).
