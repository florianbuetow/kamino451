# Kamino451: Agent Factory

Kamino451 is a leightweight implementation of an **agent factory** on Claude Code. It turns a task description
into an auditable, replayable agent run, records the result, and uses past
outcomes to choose a suitable agent and model for future tasks.

The project demonstrates a practical learning loop:

```text
run tasks -> capture traces -> analyze failures -> rank difficulty -> improve routing
```

## Main components

| Component | Location | Responsibility |
|---|---|---|
| Factory engine | `.kamino/` | Agent blueprints, deterministic scripts, run capsules, evaluation data, and reports |
| Claude Code control plane | `.claude/` | Slash-command skills and judge or classifier agent prompts |
| Agent library | `.kamino/agents/` | Reusable Markdown blueprints for solving agents |
| Dispatch queue | `.kamino/dispatch-queue/` | Instantiated agents and isolated run directories |
| Evaluation store | `.kamino/evals/` | Corpora, task evidence, outcomes, failure analysis, and dashboards |

Solving agents are generic subagents created from Markdown blueprints. The
factory binds a blueprint to a task, writes the instantiated copy to disk, and
dispatches a subagent with that exact file.

## Requirements

- [uv](https://docs.astral.sh/uv/) for running Python commands
- A Python environment with pytest available to uv. The repository does not
  currently pin test dependencies in a project manifest.
- [just](https://github.com/casey/just) as the command runner
- [Claude Code](https://claude.com/claude-code) for factory skills and agent dispatch

## Quick start

```bash
just help
just run
just check
just test
just ci
```

- `just help` lists the available repository commands.
- `just run` smoke-tests the factory workflow contracts and integrity checks. It
  does not dispatch a solving agent.
- `just check` validates all agent blueprint templates.
- `just test` runs the complete pytest suite.
- `just ci` runs both template validation and the complete test suite.

`just run`, `just test`, and `just ci` currently depend on local files under the
gitignored `docs/` directory. See [Testing notes](#testing-notes) before using
them from a fresh clone.

## Run a task

The normal workflow has two explicit phases: assemble the run, then execute it.

### 1. Assemble the run

In Claude Code, invoke `/factory` with your goal:

```text
/factory
<goal>
Describe the task to assemble.
</goal>
```

The factory evaluates the task, places its difficulty, searches successful prior
runs, chooses a route, and instantiates one or more agents. A multi-step route is
written under `.kamino/dispatch-queue/<run-id>/`; a single cloned agent may be
returned as a standalone file.

Assembly does not execute the agents or write an outcome to the ledger.

### 2. Execute the assembled run

Pass the generated dispatch directory to `/run`:

```text
/run
<dispatch_dir>
.kamino/dispatch-queue/<run-id>/
</dispatch_dir>
<task_detail_json>
.kamino/evals/tasks/details/<task-id>.json
</task_detail_json>
```

If assembly returned standalone files instead of a dispatch directory, provide
their ordered paths in an `<agents>` field. `/run` executes the instantiated
agents in dependency order, verifies each step, records a trace, judges task
success, and writes the final outcome. The `<task_detail_json>` input is required
for success judgment and ledger recording; without it, `/run` can execute and
trace the agents but cannot record the outcome.

## How the factory works

Every task follows a **compile, then run** boundary.

### Compile

1. `task-evaluate` combines deterministic metrics with semantic task scoring.
2. `rank-task-difficulty` places the task against Bradley-Terry corpus anchors.
3. `agent-candidate-search` finds agents that succeeded on similar tasks.
4. Routing selects an agent blueprint plus a model and effort level.
5. The factory creates an instantiated agent file and records the route context.

Compile artifacts are reusable, but compile never writes to the outcome ledger.

### Run

1. The instantiated agent executes in its run directory.
2. Post-flight verification runs the command declared in the execution graph,
   when one is present.
3. The factory writes the trace and run evidence.
4. Recorded `tests_passed` evidence determines success when it is available.
   Otherwise, the general run workflow uses its LLM success judge.
5. `task-outcome-record` appends the attempt to the outcome ledger.

### Important guarantees

- Blueprint `model` and `effort` values are defaults. A compiled run may bind
  different values, but the reason must be recorded.
- The instantiated agent file is authoritative during execution.
- `.kamino/evals/tasks/task-outcome-ledger.jsonl` is append-only and is written
  only through the ledger writer.
- Corpus attempts use test exit codes as ground truth. For general runs, boolean
  `verification_evidence.tests_passed` selects deterministic judgment; without
  that evidence, the LLM success judge is used.
- Cold-start evaluation sweeps begin with the cheapest model in the ladder. When
  useful history exists, factory routing may begin with the recommended model.
- A failed sweep attempt advances to the next model when the configured ladder
  contains one.

The skill definitions live under `.claude/skills/`. Supporting judge and
classifier prompts live under `.claude/agents/`.

## Evaluation corpora

The repository starts without an evaluation corpus. Each corpus is generated
from a real source of problems and answers, then stored in a standard format
that the generic factory engine can consume.

### Create a corpus

In Claude Code, start the guided ingestion workflow:

```text
/create-eval-corpus
```

The workflow collects the source directory, corpus name, answer source, and
answer contract. It then:

1. Authors a source-specific builder in `.kamino/evals/ingest/<corpus-name>/`.
2. Snapshots the ground-truth answer data for reproducibility.
3. Generates `.kamino/evals/tasks/corpus-<name>/`.
4. Runs all corpus integrity gates.
5. Writes a provenance report beside the builder.

Source-specific parsing stays in the generated builder. The generic scripts in
`.kamino/evals/scripts/` remain corpus-agnostic.

### Corpus structure

Each task has the same layout:

```text
<task-id>/
  task.md
  meta.json
  solution_reference.py
  tests/test_solution.py
  tests_hidden/test_hidden.py
```

`tests_hidden/test_hidden.py` is optional. It is useful for scale limits,
adversarial edge cases, and complexity checks that visible examples may miss.

The solving agent sees only `task.md` plus top-level PNG, GIF, JPEG, or WebP task
figures. Reference solutions and every test tier remain outside its working
directory.

### Validate a corpus

Run the discovery-based integrity test:

```bash
uv run pytest .kamino/tests/test_corpus_integrity_any.py -q
```

The test reconciles the complete index for every discovered corpus. It then
checks every task with a hidden tier plus a deterministic sample of up to about
24 remaining tasks per corpus. For the selected tasks, it verifies that:

- the standard task files and a runnable test command are present;
- the declared test command passes with the reference solution;
- the declared test command fails when no solution is present;
- long, non-skeleton lines from the reference or hidden tests do not appear
  verbatim in the task statement.

The test skips cleanly when no corpus exists.

## Evaluate performance

A corpus is the benchmark, not the system being scored. Use it to answer one of
two different questions:

| Question | Workflow | What stays fixed |
|---|---|---|
| How well does factory routing work? | `/evaluate-factory` | The corpus |
| How well does one blueprint work? | `/evaluate-agent` | The corpus and blueprint |

### Prepare the corpus ranking

Before the first sweep, the corpus needs a Bradley-Terry ranking. Prepare a task
set JSON and a pairwise-comparison JSON, then run:

```text
/rank-task-difficulty
<tasks_file>
path/to/corpus-tasks.json
</tasks_file>
<comparisons_file>
path/to/corpus-comparisons.json
</comparisons_file>
```

The repository includes
[`sample-difficulty-tasks.json`](.kamino/evals/tasks/sample-difficulty-tasks.json)
and
[`sample-difficulty-comparisons.json`](.kamino/evals/tasks/sample-difficulty-comparisons.json)
as format examples. Pairwise outcomes must come from the difficulty judge rather
than being invented.

Save the returned ranking as
`.kamino/evals/tasks/corpus-<name>/corpus-<name>-ranking.json`. The ingestion
workflow does not currently generate these ranking inputs automatically.

Once the ranking exists, an evaluation sweep creates or reuses the per-task
evaluations, placements, and candidate searches. If the ranking is missing, the
sweep stops before compiling an attempt.

### Evaluate factory routing

Use `/evaluate-factory` when you want the factory to choose an agent and model
for each task:

```text
/evaluate-factory
<corpus_dir>
.kamino/evals/tasks/corpus-<name>
</corpus_dir>
```

The factory searches past outcomes, selects a compatible blueprint, and starts
with the historically recommended model when the evidence supports one and that
model is present in the requested ladder. With no useful history, it starts with
the cheapest model. A failed attempt advances to the next model in the ladder.
This measures routing quality across the corpus.

Optional controls are provided as additional XML fields:

- `<limit>10</limit>` caps the number of tasks in the sweep.
- `<tasks>task-a, task-b</tasks>` selects a specific subset.
- `<models>haiku, sonnet</models>` sets the escalation ladder.
- `<ranking_json>path/to/ranking.json</ranking_json>` uses an explicit ranking.

### Benchmark one agent

Use `/evaluate-agent` when every task should use the same blueprint:

```text
/evaluate-agent
<corpus_dir>
.kamino/evals/tasks/corpus-<name>
</corpus_dir>
<agent>
.kamino/agents/library/coding/python-coding-agent-single-shot.md
</agent>
```

This workflow measures the blueprint itself. Agent selection never changes
during the sweep, although an explicit model ladder may still escalate after a
failure.

Optional controls are provided as additional XML fields:

- `<limit>10</limit>` caps the number of tasks in the sweep.
- `<tasks>task-a, task-b</tasks>` selects a specific subset.
- `<models>haiku</models>` sets the model or escalation ladder.
- `<effort>medium</effort>` binds the effort level.
- `<ranking_json>path/to/ranking.json</ranking_json>` uses an explicit ranking.

The corpus compiler fills exactly `GOAL`, `PROBLEM`, and `OUTPUT_FILE`. A
compatible blueprint must declare exactly those inputs. Any extra unresolved
template variable fails compilation, and a blueprint cannot require test access.

### Compare routing with a fixed agent

Run both workflows against the same corpus, then open `sweeps.html`. The report
aggregates pass rates for automatically routed and prescribed-agent sweeps so you
can compare them. For a meaningful comparison, use the same task subset and
model ladder, then account for any effort differences in your interpretation.

### Evaluation outputs

| Artifact | Purpose |
|---|---|
| `.kamino/evals/tasks/task-outcome-ledger.jsonl` | One append-only record per attempt |
| `.kamino/evals/tasks/sweeps.html` | Sweep summaries and factory versus fixed-agent comparisons |
| `.kamino/evals/tasks/errors.html` | Filterable attempt history, traces, evidence, and failure analysis |
| `.kamino/evals/tasks/difficulty.html` | Difficulty calibration joined with outcomes and failures |
| `.kamino/evals/tasks/calibration-report-<name>.md` | Per-corpus ranking and solve-rate analysis |

Each completed sweep regenerates these reports automatically. To rebuild them
after an out-of-sweep change, run:

```bash
bash .kamino/evals/scripts/generate_reports.sh
```

The generated HTML files are self-contained and can be opened directly.

## Evaluation internals

The high-level evaluation skills drive the entire process described below. Use
the individual scripts only when developing or debugging the pipeline.

### 1. Prepare evaluation evidence

For each task, the pipeline stores evidence in:

```text
.kamino/evals/tasks/evaluations/
.kamino/evals/tasks/difficulty/
.kamino/evals/tasks/candidates/
```

Task evaluation combines the deterministic evaluator with the semantic task
judge. A corpus ranking is fitted once from pairwise comparisons:

```bash
uv run .kamino/evals/scripts/bradley_terry_pairwise_ranking.py rank \
  --tasks <tasks.json> \
  --comparisons <comparisons.json> \
  --format json
```

Each corpus task is then placed directly from that ranking, and the candidate
search uses its evaluation plus placement to find similar successful runs.

### 2. Compile an isolated attempt

`compile_run.py` creates the physical separation between solver inputs and
verification data:

```bash
uv run .kamino/evals/scripts/compile_run.py \
  --corpus-dir <corpus> \
  --task-id <task-dir> \
  --eval-id <eval-id> \
  --attempt <number> \
  --model <model> \
  --effort <effort> \
  --blueprint <blueprint-file> \
  --mode auto \
  --sweep-id <sweep-id> \
  --format json
```

Use `--mode prescribed` when the blueprint was fixed by the caller.

The command returns `run_dir`, `agent_file`, and `run_id`. The run directory has
this shape:

```text
.kamino/dispatch-queue/<run-id>/
  01-<blueprint-name>.md
  outputs/
  work/task.md
  verify/tests/
  verify/tests_hidden/
  execution-graph.md
  route-decision.json
```

The hidden test directory is present only when the corpus task defines it.
`work/` never contains a reference solution or test file. `route-decision.json`
stores the sweep mode and ID; the ledger record does not duplicate that metadata.

### 3. Dispatch the solving agent

The compiled agent runs as a subagent using the model and effort stored in its
frontmatter. Its entire instruction is:

> Read `<agent-file>`, follow its instructions exactly, and return only the JSON
> required by its output format.

Use the `agent_file` returned by `compile_run.py`. A corpus-compatible blueprint
writes `work/solution.py` without access to the staged tests.

### 4. Record the outcome

`record_run.py` copies the solution into `verify/`, runs all staged test tiers
together under a 300-second timeout, writes trace and evidence files, derives
the success result, and appends the ledger record:

```bash
uv run .kamino/evals/scripts/record_run.py \
  --task-id <eval-id> \
  --run-dir <run-dir> \
  --model <model> \
  --effort <effort> \
  --started-at <iso-timestamp> \
  --ended-at now \
  --attempt <number> \
  --format json
```

### 5. Analyze failures

Use `/failure-analyze` for each failed record. The classifier maps the attempt to
the catalog in `.kamino/evals/tasks/failure-mode-catalog.md` and stores the
analysis under `.kamino/evals/tasks/failures/`.

### 6. Regenerate reports

```bash
bash .kamino/evals/scripts/generate_reports.sh
```

Report generation is idempotent. It rebuilds the derived views from the ledger
plus available corpus, failure-analysis, trace-review, and run-capsule metadata.
Sweep comparisons resolve each attempt's raw `route-decision.json` from its
retained run capsule.

## Routing policy

`route_recommendation.py` recommends a model and effort level. When enough
same-task-type history clears the configured success threshold, it may also
recommend blueprint paths. Otherwise, candidate search and the factory's
compatibility checks select the agent separately.

The recommendation follows this order:

1. **Success-rate policy:** find agent, model, and effort combinations that clear
   the threshold with enough attempts, then prefer the cheaper qualifying model.
2. **Weighted-majority fallback:** recommend a model and effort by weighting
   successful records according to task-type match and difficulty proximity.
3. **Cold start:** use the cheapest model in the factory's built-in ladder when
   no useful history exists.

The success threshold and minimum attempt count live in
`.kamino/factory-config.json`. The model ladder is implemented in the routing
script rather than configured in that file.

Run the recommendation script directly with:

```bash
uv run .kamino/evals/scripts/route_recommendation.py \
  --ledger .kamino/evals/tasks/task-outcome-ledger.jsonl \
  --task-eval .kamino/evals/tasks/evaluations/<eval-id>.json \
  --difficulty .kamino/evals/tasks/difficulty/<eval-id>.json \
  --format json
```

## AutoResearch prompt optimization

AutoResearch is a separate learning loop that improves a blueprint's prompt
instead of choosing which blueprint should solve a task. Invoke it deliberately
with `/improve-agent`; normal task execution and evaluation sweeps never start it
automatically.

Each run creates a fresh, gitignored workspace under
`.kamino/auto-research/<timestamp>/`. The workflow seeds `agent.md`, builds a
problem-specific evaluation harness, and performs keep-or-revert iterations while
holding the model fixed.

Simulation mode is offline. Real mode invokes `claude -p` and consumes live
tokens. After the skill has prepared a complete workspace, it uses these driver
commands:

```bash
uv run .kamino/evals/scripts/auto_research.py init --workspace <workspace>
uv run .kamino/evals/scripts/auto_research.py evaluate-change --workspace <workspace>
```

These are internal driver commands, not workspace setup commands. The current
driver also requires `README.md` and `best_score.txt` in the workspace, but the
`/improve-agent` setup instructions do not create them. Direct initialization
will fail until those files are supplied or that contract is reconciled.

An improved prompt is not promoted automatically. Prove it task by task with
`/replay`, then create a new blueprint version with `/createblueprint`.

## Maintenance

Run directories referenced by ledger records are replay capsules and are kept
indefinitely. After at least one outcome has been recorded, unreferenced
directories can be listed with:

```bash
uv run .kamino/evals/scripts/prune_dispatch_queue.py \
  --dispatch-dir .kamino/dispatch-queue \
  --ledger .kamino/evals/tasks/task-outcome-ledger.jsonl \
  --format json
```

Add `--apply` only when you intend to delete the listed directories. Pruning is
never automatic.

## Repository layout

```text
.claude/
  agents/
  skills/
.kamino/
  agents/
    library/
    ad-hoc/
  auto-research/
  dispatch-queue/
  evals/
    ingest/
    scripts/
    tasks/
  scripts/
  tests/
justfile
pytest.ini
```

- `.claude/agents/` contains judge, classifier, and AutoResearch prompts.
- `.claude/skills/` contains the Claude Code slash-command workflows.
- `.kamino/agents/library/` contains curated blueprints.
- `.kamino/agents/ad-hoc/` contains reusable blueprints created for new needs.
- `.kamino/auto-research/` is created on demand and is gitignored.
- `.kamino/dispatch-queue/` stores isolated run capsules and is gitignored.
- `.kamino/evals/ingest/` is created by the first corpus ingestion and stores
  generated builders plus provenance.
- `.kamino/evals/scripts/` contains the corpus-agnostic evaluation engine.
- `.kamino/evals/tasks/` accumulates corpora, evidence, outcomes, and reports.
- `.kamino/tests/` contains script, workflow, isolation, and integrity tests.

## Testing notes

- Run pytest through uv with `uv run pytest`.
- From another directory, run
  `uv run --directory /path/to/kamino451 pytest`.
- `just check` validates blueprint template contracts with
  `.kamino/scripts/template-variable-checks.sh`.
- Corpus integrity tests discover all present corpora automatically.
- The tracked test suite and `task-llm-judge` prompt currently reference files
  under the gitignored `docs/` directory. A fresh clone does not contain those
  files, so restore the local documentation before relying on `just run`,
  `just test`, `just ci`, or semantic task evaluation.

## License

Kamino451 is available under the [MIT License](LICENSE).
