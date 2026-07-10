---
name: taskgraph
description: Builds a task graph — decomposes a complex or multi-step task into an ordered chain of independent steps, maps each step onto an existing agent in the .kamino/agents index, instantiates those agents wired together by filename, and writes them into a timestamped dispatch-queue with an execution graph. Use when the user has a task that is too big for a single agent, wants a multi-agent pipeline or task graph planned, mentions task decomposition or breaking a task into steps, or wants several Kamino agents chained together.
---

# Task Graph

Use this skill when a task is **complex or multi-step** and no single agent can complete it. It plans a pipeline of existing agents and instantiates them, chained by filename, so every agent in the pipeline can be created up front — before any of them has produced output. It writes the assembled pipeline into a timestamped **dispatch-queue** that `run` later executes.

For a task that one agent can handle, use `clone` instead. To create an agent type that does not yet exist, use `createblueprint`.

## Core idea: chain by filename

Agents are wired output→input using **file paths**, not text content. Each step declares the file it will write (`{{OUTPUT_FILE}}`). A downstream step that consumes that result sets its corresponding input variable to **that same path** — a filename, not the (not-yet-existing) content. The agents are built to accept an input that is either inline content **or** a path to read (see the input convention in every blueprint), so handing a step a not-yet-written path is fine.

This is what makes it possible to instantiate the entire pipeline before running anything: a step does not need its predecessor's output to exist, only the agreed-upon path where it *will* appear.

```text
step 1  ▶  <run-dir>/outputs/01-research.md
step 2  reads <run-dir>/outputs/01-research.md  ▶  <run-dir>/outputs/02-writing.md
step 3  reads <run-dir>/outputs/02-writing.md   ▶  <run-dir>/outputs/03-review.md
```

## Output location: the dispatch-queue

Each decomposition writes a single self-contained run directory:

```text
.kamino/dispatch-queue/<YYMMDD-HHMMSS>/
├── execution-graph.md          # run order, per-step agent, inputs, output, dependencies
├── 01-<agent-name>.md          # instantiated agent, step-ordered filename
├── 02-<agent-name>.md
├── 03-<agent-name>.md
└── outputs/                    # each step's {{OUTPUT_FILE}} is written here at run time
```

Get the timestamp from the system clock; never invent it:

```bash
run_id="$(date +%y%m%d-%H%M%S)"
run_dir=".kamino/dispatch-queue/$run_id"
mkdir -p "$run_dir/outputs"
```

## Paths

```text
.kamino/agents/index.md                        # source of truth for available agents
.kamino/agents/library/<category>/<agent>.md   # tested agents — search here first
.kamino/agents/ad-hoc/<category>/<agent>.md    # on-the-fly agents — fall back here
.kamino/scripts/template-replace.sh            # fill an instantiated agent's {{...}} variables
.kamino/scripts/template-replace-completed.sh  # verify a fully instantiated agent has no {{...}} left
.kamino/dispatch-queue/<YYMMDD-HHMMSS>/        # the run directory this skill writes
```

## Rules

1. Treat `.kamino/agents/index.md` as the only source of truth for available agents. Do not invent agents that are not listed.
2. First read the whole index to get an overview before decomposing.
3. Decompose only as far as needed. If the task maps cleanly to one agent, say so and defer to `clone` — do not manufacture steps.
4. Each step must map to exactly one agent from the index, searching the `library/` tier first and falling back to `ad-hoc/`. Before deciding a step has no matching agent, try to **adapt** the closest agent through its declared inputs — a sharper `{{GOAL}}`, richer `{{CONTEXT}}`, or other input values can make a general agent fit. Adapt only through declared inputs, never by editing the agent's baked persona, rules, steps, or output format, and only when the inputs **do not contradict** those baked properties (e.g. an agent whose output format is JSON cannot be adapted to a Markdown task). If adapting would contradict the agent, or no agent is close enough, it is a real gap: stop and report it — the user likely wants `createblueprint` to add a new agent, ideally seeded from the closest existing agent (copy its properties, modify what differs). Never force an ill-fitting or self-contradicting agent onto a step.
5. Steps must be ordered so each builds only on the outputs of earlier steps — no cycles, no forward references.
6. Every step writes its output under `<run-dir>/outputs/NN-<agent-name>.md`. Wire dependencies by filename: a downstream input variable receives the upstream step's output **path**.
7. Fill only the blueprint's `{{...}}` invocation variables — the inputs, `{{GOAL}}`, and `{{OUTPUT_FILE}}`. Persona, rules, definition of done, steps, and output format are already baked into the blueprint by `createblueprint`; do not set them here.
8. Each instantiated agent must have every variable filled. Verify with `template-replace-completed.sh` — a fully instantiated agent has no `{{...}}` tokens left.
9. Never invent values for required variables. If a step needs an input that is neither a user-provided value nor an upstream output, stop and report what is missing.
10. Do not run the agents. This skill plans, instantiates, and queues the pipeline; execution is the separate `run` skill.
11. Write files **and** print the run order to the terminal (see Output Format).

## Steps

1. Read `.kamino/agents/index.md` and summarize the available agents (name, purpose, inputs, output).
2. Judge complexity. If a single agent fits, report that and hand off to `clone`.
3. Otherwise, decompose the task into ordered, independent steps where each step is satisfiable by one indexed agent.
4. Create the run directory:

```bash
run_id="$(date +%y%m%d-%H%M%S)"
run_dir=".kamino/dispatch-queue/$run_id"
mkdir -p "$run_dir/outputs"
```

5. Build a dependency plan. For each step `N` (zero-padded, starting at 01) record:

```text
step N:
  agent:        <category>/<agent>.md
  out_file:     <run-dir>/outputs/NN-<agent-name>.md
  inputs:       VAR = <user value | upstream step's out_file path>
  goal:         <per-step objective>
  model:        <blueprint default | bound value, with source>
  effort:       <blueprint default | bound value, with source>
  verification: <optional command whose exit code 0 proves the step, e.g. the task's test command>
  depends_on:   [<earlier steps>]
```

Model/effort binding: the blueprint frontmatter is the default. When the route decision binds different values for a step (evidence-based pick or cheap-first escalation — see the factory skill's "Model binding"), edit the **instantiated copy's** frontmatter `model:` / `effort:` lines after copying; never edit the blueprint. Record every deviation and its source in the execution graph.

6. Confirm every step maps to a real indexed agent and every input resolves to either a user value or an earlier step's `out_file`.
7. Instantiate each step's agent. Copy the blueprint to `<run-dir>/NN-<agent-name>.md`, then fill each `{{...}}` variable with `template-replace.sh` (one call per variable; the replacement is piped on stdin and the token comes first):

```bash
agent="$run_dir/01-article-writing-agent.md"
cp .kamino/agents/library/writing/article-writing-agent.md "$agent"
printf '%s' '{{GOAL}} Write the launch article' | .kamino/scripts/template-replace.sh "$agent"
printf '%s' "{{CONTEXT}} $run_dir/outputs/00-research.md" | .kamino/scripts/template-replace.sh "$agent"
printf '%s' "{{OUTPUT_FILE}} $run_dir/outputs/01-article-writing-agent.md" | .kamino/scripts/template-replace.sh "$agent"
```

8. Verify each instantiated agent has no `{{...}}` left:

```bash
.kamino/scripts/template-replace-completed.sh "$agent"
```

9. Write `<run-dir>/execution-graph.md` capturing the run order, each step's agent, inputs (value or upstream path), output file, and dependencies (see format below).
10. Print the run order to the terminal and return the result.

## execution-graph.md format

```markdown
# Execution Graph — <run_id>

## Run order
1. 01-<agent-name>.md
2. 02-<agent-name>.md
3. 03-<agent-name>.md

## Chain
01-research ▶ 02-writing ▶ 03-review

## Steps
| Step | Agent file | Blueprint | Model / Effort | Inputs (value / upstream path) | Output file | Verification | Depends on |
|---:|---|---|---|---|---|---|---|
| 01 | 01-…md | library/…/….md | sonnet / high | … | outputs/01-…md | — | — |
| 02 | 02-…md | ad-hoc/…/….md | haiku / medium (bound: escalation seed) | CONTEXT = outputs/01-…md | outputs/02-…md | `uv run pytest <work>/tests -q` | 01 |
```

- `Blueprint` is the source blueprint the step was instantiated from (used by `run` for trace records).
- `Model / Effort` shows the values written into the instantiated copy; when they deviate from the blueprint default, append the binding source in parentheses.
- `Verification` is an optional command; `run` executes it post-flight and a non-zero exit code fails the step. Use `—` when none.

## Failure Conditions

Decomposition fails if:

1. `.kamino/agents/index.md` does not exist.
2. A required step has no matching agent in the index.
3. The steps cannot be ordered without a cycle.
4. A required input cannot be resolved to a user value or an upstream output path.
5. Any instantiated agent still contains `{{...}}` tokens after replacement.
6. The run directory, an agent file, or `execution-graph.md` cannot be written.

## Output Format

Write the files above, **and** print this to the terminal:

```markdown
# Task Decomposition → <run_dir>

## Run order (run the agents in THIS sequence)
1. 01-<agent-name>.md   ← <one-line purpose>
2. 02-<agent-name>.md   ← <one-line purpose>
3. 03-<agent-name>.md   ← <one-line purpose>

## Pipeline
| Step | Agent | Inputs (value / upstream path) | Output file | Depends on | Verified (no {{...}}) |
|---:|---|---|---|---|---|

## Dispatch queue
- Directory: `.kamino/dispatch-queue/<run_id>/`
- Execution graph: `.kamino/dispatch-queue/<run_id>/execution-graph.md`
- To execute: run the `run` skill on this directory.

## Gaps
List any capability with no matching agent (candidate for `createblueprint`), or "none".
```

## Success Criteria

The skill succeeds only when:

1. The task is decomposed into an ordered, acyclic set of steps.
2. Every step maps to a real agent from the index.
3. Every step's inputs resolve to a user value or an upstream output path.
4. Each agent is instantiated (in the run directory) with all variables filled and verified.
5. `execution-graph.md` is written and the run order is printed to the terminal.
