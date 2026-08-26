---
name: improve-agent
description: Improves a target agent blueprint's prompt with the AutoResearch keep-or-revert loop — the "Improve" stage of the factory's learning loop (Run → Trace → Evaluate → Attribute → Improve). It sets up a fresh optimization workspace from scratch (seeds agent.md from the target blueprint, has autoresearch-program-author and autoresearch-eval-author author the per-problem harness files, initializes the nested git), then iterates: one surgical prompt edit per round, kept only when the score strictly improves. It never writes the ledger; improvements are proven afterwards via replay and baked in via createblueprint. Use when the user wants to improve or optimize an agent's prompt, run AutoResearch, act on failure analyses, or make a blueprint stop failing a class of tasks.
---

# Improve Agent

Use this skill to **improve one agent blueprint's prompt against evidence**. It is the deliberate entry point to AutoResearch — the "Improve" stage that `failure-analyze` hands into. Every other skill correctly forbids invoking AutoResearch during normal task completion; this skill IS the sanctioned door, and it maintains the same hard boundary: **the optimization loop never touches the task-outcome ledger**. An improved prompt earns ledger records only later, through `replay` or the evaluate skills.

The workspace is **use-state, not machinery**: nothing is pre-created in the factory. Every run sets up a fresh workspace from scratch — the setup instructions below are the complete recipe.

## Inputs

```xml
<target_blueprint>
.kamino/agents/library/coding/python-coding-agent-single-shot.md
</target_blueprint>

<evidence>
<!-- what to improve against: failure analyses, captured task details, or a corpus slice -->
.kamino/evals/tasks/failures/<task_id>.json, .kamino/evals/tasks/details/<task_id>.json
or: <corpus_dir> + <tasks> selection
</evidence>
```

Optional:

```xml
<iterations>10</iterations>                  <!-- keep-or-revert rounds; default 10 -->
<runner_mode>simulate</runner_mode>          <!-- simulate (offline, free) | real (spends tokens via claude -p) -->
<workspace>.kamino/auto-research/<yymmdd-HHMMSS>/</workspace>  <!-- default shown; always a fresh timestamped dir -->
```

## Uses

- Agent: `autoresearch-program-author` — authors the workspace's `program.md`
- Agent: `autoresearch-eval-author` — authors the workspace's `eval.py`, `tasks.json`, and the runner adapter for this problem's task type
- Agent: `autoresearch-agent-improver` — drives the keep-or-revert loop
- Agent: `autoresearch-llm-evaluator` — semantic failure tagging during the loop (never the primary score)
- Script: `.kamino/evals/scripts/auto_research.py` (`init`, `evaluate-change`, `run-loop`)

## Workspace setup (the complete from-scratch recipe)

Every run creates its own workspace; nothing is assumed to exist beforehand.

1. **Create the workspace directory** (timestamped; never reuse an old one):

```bash
workspace=".kamino/auto-research/$(date +%y%m%d-%H%M%S)"
mkdir -p "$workspace"
```

2. **Ignore run byproducts inside the workspace** (it gets its own nested git from `init`):

```bash
printf '__pycache__/\n*.pyc\nlast_eval_results.json\nfailure_mode_summary.md\nresults.log\n' > "$workspace/.gitignore"
```

3. **Seed `agent.md` from the target blueprint** — the blueprint's prompt is the baseline being optimized:

```bash
cp "<target_blueprint>" "$workspace/agent.md"
```

   The blueprint's `{{...}}` invocation variables (e.g. `{{GOAL}}`, `{{PROBLEM}}`, `{{OUTPUT_FILE}}`) are the agent's interface: the runner adapter fills them per task at evaluation time, and the improver must never edit them — only the baked sections (persona, rules, steps, output format) are optimization targets.

4. **Author `program.md`** — invoke `autoresearch-program-author` with the workspace path, the target blueprint, the evidence (failure analyses / task details), and the metric. It writes `<workspace>/program.md`.

5. **Author the harness** — invoke `autoresearch-eval-author` with the workspace path, the task source, and the runner mode. It writes `<workspace>/eval.py`, `<workspace>/tasks.json` (each entry pointing at a corpus task directory), `<workspace>/runner-config.json` (`simulate` unless the user chose `real`), and the runner adapter named for the task type (e.g. `run_swe_agent.py` for coding tasks). A reference coding harness lives at `.kamino/tests/fixtures/auto-research/` for the authors to consult.

6. **Initialize the nested git** (the keep-or-revert mechanism):

```bash
uv run .kamino/evals/scripts/auto_research.py init --workspace "$workspace"
```

   Initialization creates `best_score.txt` with an unbounded-low starting score
   when it is absent, then commits the complete baseline. No extra workspace
   files beyond the recipe above are required.

7. **Baseline sweep** — establish the starting score before any edit:

```bash
uv run .kamino/evals/scripts/auto_research.py evaluate-change --workspace "$workspace"
```

## The optimization loop

Hand the loop to `autoresearch-agent-improver` with the workspace path and the iteration budget. Per iteration it reads `program.md`, `last_eval_results.json`, and `failure_mode_summary.md`, makes exactly one surgical edit to `agent.md`, and runs:

```bash
uv run .kamino/evals/scripts/auto_research.py evaluate-change --workspace "$workspace"
```

The script commits the edit when the score strictly improves and reverts it otherwise. An external CLI meta-agent can drive the same loop via `auto_research.py run-loop --workspace "$workspace" --iterations N --improver-command <agent-cli> <args>`.

## Rules

1. Only `<workspace>/agent.md` is editable during the loop. `eval.py`, `tasks.json`, the runner adapter, and `runner-config.json` are immutable once the baseline runs.
2. Never edit the `{{...}}` invocation variables in `agent.md` — they are the blueprint's interface, filled per task by the adapter.
3. Never write the task-outcome ledger, task details, outcomes, or failures from this skill. The loop's records live only inside the workspace.
4. Never teach the agent the tasks' answers or ids — no edit may game the harness. Semantic judgment routes through `autoresearch-llm-evaluator`; it never replaces the primary scalar score.
5. `simulate` is the default runner mode; switch to `real` only on the user's explicit choice — every real-mode sweep spends live tokens (one `claude -p` per task).
6. The workspace is use-state: report its path when done; archiving it to `data/` afterwards is the user's explicit act, never automatic.
7. Run Python only through `uv run`.

## Steps

1. Resolve the target blueprint (must exist and pass `template-variable-checks.sh`), the evidence, the iteration budget, and the runner mode.
2. Execute the workspace setup recipe above (steps 1–7).
3. Run the optimization loop for the iteration budget, tracking each round's score and kept/reverted status.
4. Report the score trajectory (Output Format below).
5. Hand off: offer `replay` on the originally failed captured tasks to prove the improved prompt honestly, and `createblueprint` (seeding path) to bake the improved `agent.md` into a new blueprint version — never overwrite the original blueprint in place.

## Failure Conditions

Fail clearly if:

1. The target blueprint is missing or fails instantiation checks.
2. No evidence source is provided (nothing to improve against).
3. A harness author leaves the workspace incomplete (missing `program.md`, `eval.py`, `tasks.json`, adapter, or `runner-config.json`).
4. `auto_research.py init` or the baseline `evaluate-change` exits non-zero.
5. The harness reports that files other than `agent.md` changed during the loop.

## Output Format

```markdown
# Agent Improvement — <blueprint name>

- Workspace: `<workspace>`   Mode: simulate|real   Iterations: N
- Baseline score: <float>

## Trajectory
| Round | Edit (one line) | Score | Kept? |
|---:|---|---:|---|

## Result
- Final score: <float> (baseline <float>)
- Improved agent.md: `<workspace>/agent.md`
- Next: `replay` the originally failed tasks with the improved prompt; `createblueprint` (seeded) to register it as a new blueprint version.
```

## Success Criteria

The skill succeeds when the workspace was built from scratch per the recipe, the baseline and every iteration were scored by the immutable harness, only `agent.md` ever changed, the trajectory is reported honestly (a run with zero kept edits is a valid, honest result), and the ledger was never touched.
