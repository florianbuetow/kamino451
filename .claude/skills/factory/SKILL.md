---
name: factory
description: Default entry point and orchestrator for the afac (agentfactory) plugin. Routes a user's goal to the right agent-factory capability — cloning and instantiating an existing agent, decomposing a complex task into a multi-agent pipeline, creating a new agent blueprint, or running an assembled pipeline. Use when the user states a goal and wants afac to figure out which agents are needed and assemble them, mentions the agent factory or orchestrating agents, or is unsure whether to clone, chain, build, or run agents.
---

# Factory (afac)

The default front door to the **afac** (agentfactory) plugin. Given a goal, it decides which factory capability applies and dispatches to the matching subskill. It orchestrates; it does not duplicate the subskills' logic — it routes.

## Subskills

| Subskill | Use when |
|---|---|
| `task-evaluate` | First compile step. Profiles task clarity, ambiguity, completeness, difficulty, task type, and open issues. |
| `rank-task-difficulty` | Second compile step. Places the task against historical difficulty anchors. |
| `agent-candidate-search` | Third compile step. Searches successful historical outcomes for agents to consider. |
| `task-detail-record` | Persists task context after route decision and before execution. |
| `task-outcome-lookup` | Optional read-only summary of historical task outcomes. Not required for candidate selection. |
| `clone` | One existing agent in the index can complete the task. Select, fill, and instantiate it. |
| `taskgraph` | The task is complex/multi-step. Break it into a chain of indexed agents wired by filename and instantiate them. |
| `createblueprint` | No existing agent fits — a new agent *type* must be designed and added to the index first. |
| `run` | Instantiated agents already exist and the user wants them executed in sequence with per-step verification. |
| `evaluate-factory` | The user wants to seed the factory with outcome data or measure its routing by sweeping a task corpus (factory-picked agent+model, cheap-first escalation, ground-truth judging, ledger records). |
| `evaluate-agent` | The user wants to benchmark one prescribed agent blueprint against a task corpus (agent never varies; ground-truth judging, ledger records). |
| `failure-analyze` | A recorded attempt failed and the user wants it classified into catalog failure modes with the component to improve. |
| `improve-agent` | The user explicitly wants an agent blueprint's prompt improved against failure evidence (keep-or-revert optimization in an isolated workspace). Deliberate only — never routed during normal task completion. |
| `replay` | The user wants to re-run a captured task against an improved agent/model and compare old versus new outcomes. |
| `inventory` | The user wants to see what library agents exist and what they do. |

## Agent search order (tiers)

After `agent-candidate-search`, inspect shortlisted candidate blueprint paths first. If no shortlisted candidate fits, fall back to the tiered index search below.

Agents live in two tiers under `.kamino/agents/`, recorded in `index.md` by path prefix. For every capability you need, search in this strict order and stop at the first that works:

1. **`library/…`** — curated, tested agents. Search here first.
2. **`ad-hoc/…`** — agents created on the fly and kept for reuse. Fall back here only if no `library/` agent fits (including after attempting adaptation, below).
3. If no agent in either tier fits, create a new **ad-hoc** agent via `createblueprint` (it always writes to `ad-hoc/`), then use it.

Prefer the highest tier that works: a fitting `library/` agent beats an `ad-hoc/` one; reusing an existing `ad-hoc/` agent beats creating another.

## Routing

Factory compile routing is evidence-informed. Run these steps before choosing `clone`, `taskgraph`, or `createblueprint`:

1. Accept the user task exactly as written.
2. Call `task-evaluate` and retain its JSON artifact.
3. Call `rank-task-difficulty` using the task evaluation and available historical anchors.
4. Call `agent-candidate-search` using the task evaluation and difficulty placement with `limit` set to `10`.
5. Read `.kamino/agents/index.md` to learn what agents exist.
6. Assess the goal using all evidence:
   - task type;
   - clarity, ambiguity, consistency, completeness, and difficulty scores;
   - recommended mapping and open issues;
   - pairwise difficulty placement and nearest prior tasks;
   - shortlisted successful historical agent candidates;
   - candidate blueprint paths, model, effort, and prior task excerpts.
7. Choose the route:
   - First inspect shortlisted candidate blueprint files and decide whether each candidate can satisfy the current task through declared inputs.
   - Reject a shortlisted candidate if the current task contradicts that agent's baked goal, persona, rules, steps, or output format.
   - **Exactly one shortlisted agent covers the whole goal** → route to `clone`.
   - **Several shortlisted agents fit as an ordered workflow** → route to `taskgraph`.
   - **No shortlisted candidate fits** → fall back to the normal `.kamino/agents/index.md` search.
   - **No indexed agent is an exact fit** for a needed capability → first try to **adapt** an existing agent through its inputs (see "Adapt before you build"). Only if no existing agent can be adapted, route to `createblueprint` to add a new one, then re-assess.
   - **The user wants already-assembled agents executed** → route to `run`.
8. Hand the goal, task evaluation artifact, difficulty placement artifact, candidate search result, and any provided context to the chosen subskill. Let that subskill own selection, filling, verification, and writing.
9. After a route decision exists, call `task-detail-record` to write `.kamino/evals/tasks/details/<task_id>.json`.
10. If decomposition reveals a capability gap mid-plan, first attempt adaptation (see "Adapt before you build"); only if that fails, pause and route that gap to `createblueprint` before continuing.
11. Report which subskill ran, what it produced, and the task detail path.

Route bias rules:

- Prefer `clone` when one shortlisted or indexed agent fits.
- Prefer `taskgraph` when the task needs ordered steps or several shortlisted/indexed agents fit as a pipeline.
- Prefer `createblueprint` when no indexed agent fits without contradicting baked agent behavior.
- Prefer shortlisted candidates whose `meets_success_rate_threshold` is `true` — their agent+model+effort combination has a success rate above the configured threshold for this task type. Among qualified candidates, do not chase the highest rate; a cheaper qualified candidate beats a higher-rate expensive one.
- Treat prior partial completion as failure.
- Do not use historical success alone to override a current task/agent contradiction.

## Model binding

The blueprint's `model`/`effort` frontmatter is the agent author's **default**. At compile time the factory may bind different values into the instantiated copy — never into the blueprint — when the evidence justifies it:

1. **Evidence-based binding (success-rate policy).** Historical success **rates** — not raw success counts — decide the binding. The deterministic picker computes, for every agent+model+effort combination in the ledger, its success rate over attempts (failures included) scoped to the current `task_type`. Run it with the task evaluation and difficulty placement and prefer its `recommended_model`/`recommended_effort`/`recommended_agent_blueprints` unless there is a stated reason to deviate:

```bash
uv run .kamino/evals/scripts/route_recommendation.py \
  --ledger ".kamino/evals/tasks/task-outcome-ledger.jsonl" \
  --task-eval "<task-eval.json>" \
  --difficulty "<difficulty.json>" \
  --format json
```

   Policy chain, in order:
   1. **Success-rate policy.** A combination qualifies when its same-task-type success rate is strictly above `routing.success_rate_threshold` over at least `routing.min_attempts_for_rate` attempts — both read from the central factory config `.kamino/factory-config.json`. Qualifiers are ranked **cheap-first** (model ladder `haiku` → `sonnet` → `opus`, then effort, then similarity support) — deliberately **not** by highest rate: a good-enough rate on a cheap model beats a perfect rate on an expensive one.
   2. **Weighted-majority fallback.** When no combination clears the bar, it weights successful outcomes by task-type match and pairwise-difficulty proximity (weighted majority, ties cheap-first).
   3. **Cold start.** With no successful history at all, the cheap-first escalation policy applies.
2. **Escalation policy (cheap-first).** When seeding data or when no history says otherwise, bind the cheapest plausible model first (`haiku`) to surface failures early and cheaply, and escalate to `sonnet` only on a failed attempt. Each attempt is its own instantiation, task detail (`attempt` N), and outcome record.
3. **Recorded reason.** Every binding that deviates from the blueprint default must be recorded in the route decision JSON (`model`, `effort`) and thus in the task detail — silent substitution is forbidden.

`clone` and `taskgraph` perform the binding by setting the instantiated copy's frontmatter; `run` then simply respects the instantiated file.

## Adapt before you build

The indexed agents are **parameterized** — their behavior is steered at instantiation through their `{{...}}` invocation variables (`{{GOAL}}`, `{{CONTEXT}}`, and their other inputs). So "fit" is not fixed: an agent that does not match a task out of the box may still complete it once given the right inputs.

Before concluding that no agent fits and routing to `createblueprint`, check whether an existing indexed agent can be **adapted** to the task by how its inputs are filled:

1. Take the closest-matching agent(s) from the index, searching `library/` first, then `ad-hoc/`.
2. Ask: can this agent complete the task if given a sharper `{{GOAL}}`, richer `{{CONTEXT}}`, or values for its other input parameters? The adaptation must come **only** through the agent's declared inputs — never by editing the agent's baked persona, rules, steps, or output format.
3. **The inputs must not contradict the agent's baked properties.** If what the task requires conflicts with the agent's baked goal, persona, rules, steps, or output format (e.g. the agent's output format is JSON but the task needs Markdown, or its baked goal pulls the other way), the agent **cannot** be safely adapted — forcing it produces an agent at war with its own instructions, and it will misbehave.
4. If it fits **with no contradiction** → route to `clone` (single) or use it as a step in `taskgraph` (pipeline), filling the inputs that adapt it.
5. If adapting would contradict the agent, or no agent in either tier is close enough → route to `createblueprint` (which writes a new **ad-hoc** agent), and prefer **seeding from the closest existing agent**: copy that agent's baked properties (persona, rules, steps, output format, inputs) into a new blueprint and modify only what the task requires. Build fully from scratch only when nothing is close.

A new blueprint is justified only when the task needs baked behavior that no existing agent has, or that **contradicts** an existing agent's baked behavior. Otherwise, prefer adaptation through inputs.

## Rules

1. `.kamino/agents/index.md` is the source of truth for what agents exist. Do not invent agents.
2. Route; do not re-implement. Each subskill owns its own mechanics and failure handling.
3. Never invent required values. If routing needs information the user has not given (e.g. which goal, which inputs), ask before dispatching.
4. Prefer the smallest capable path: a single `clone` over a decomposition when one agent suffices.
5. A blueprint's frontmatter `model` and `effort` are **defaults**, not bindings. The factory may bind different values into the **instantiated copy** at compile time (see "Model binding"); the blueprint itself is never edited. Once instantiated, the copy's frontmatter is authoritative: `run` launches every agent with exactly the instantiated `model` and `effort`.
6. Compile phase must never write to `.kamino/evals/tasks/task-outcome-ledger.jsonl`.
7. Compile phase may write `.kamino/evals/tasks/details/<task_id>.json` through `task-detail-record`.
8. Do not invoke AutoResearch during factory compilation or normal task completion.
9. The success-rate threshold and minimum attempt count live only in the central factory config `.kamino/factory-config.json` (`routing.success_rate_threshold`, `routing.min_attempts_for_rate`). Never hardcode these values elsewhere; change routing strictness by editing that file.

## Execution boundary

Factory **assembles only** by default. It routes the goal, instantiates the agent file(s) via `clone` / `taskgraph` / `createblueprint`, and then stops. For a multi-step pipeline, `taskgraph` writes the assembled agents and an `execution-graph.md` into a timestamped dispatch-queue at `.kamino/dispatch-queue/<YYMMDD-HHMMSS>/` and prints the run order. Factory does **not** automatically run the assembled agents — executing them is a separate, explicit step via the `run` subskill (point it at the dispatch-queue), routed only when the user asks to execute. Report the assembled agent files and their intended run order, then hand back.

The compile phase may read the task outcome ledger through `agent-candidate-search` or optional `task-outcome-lookup`, but it must not write the ledger. Outcome writes belong only to `run` after `run-success-evaluate` produces a valid binary judgment.

## Output Format

```markdown
# Factory Result

## Goal
<restate the goal>

## Route
- Chosen subskill: `clone` | `taskgraph` | `createblueprint` | `run`
- Why: <one line>

## Evidence
- Task evaluation: `<path or inline JSON>`
- Difficulty placement: `<path or inline JSON>`
- Candidate search: `<path or summary>`
- Task detail: `<path>`

## Result
<summary of what the subskill produced, or the gap that must be filled first>
```
