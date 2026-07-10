---
name: createblueprint
description: Creates a new reusable Kamino agent blueprint (an agent template) from the blueprint scaffold. It discovers the authoring fields marked with <<...>> in the scaffold, interviews the user for each (persona, model, effort, rules, definition of done, steps, output format, input schema), fills them in, leaves the {{...}} invocation variables intact, writes the blueprint into the ad-hoc tier, and registers it in the index. Use when the user wants to add a new agent type, create an agent blueprint or template, design a new agent from scratch, or extend the .kamino/agents library so clone can later select it.
---

# Create Blueprint

Use this skill when the user wants to **add a new kind of agent** to the Kamino agent library — not run an existing one. It produces a reusable **blueprint** (an agent template) from the scaffold at `.kamino/agents/agent-blueprint.template.md`, modeled on the structure of the existing library agents, then registers it in `.kamino/agents/index.md` so that `clone` and `taskgraph` can later select and instantiate it.

New agents are written to the **ad-hoc** tier (`.kamino/agents/ad-hoc/<category>/`). A freshly created agent is untested by definition, so it starts in `ad-hoc/`; promotion to `library/` is a separate step taken once the agent has been validated.

A blueprint is a *template*, not a finished agent. The agent's stable identity is baked in now; only the task-specific slots stay as variables to be filled at instantiation time.

## The two tiers

The agent library is split into two tiers under `.kamino/agents/`:

- `library/<category>/…` — curated, tested agents.
- `ad-hoc/<category>/…` — agents created on the fly (by this skill), kept for reuse until validated and promoted.

This skill always writes into `ad-hoc/`.

## Two marker conventions (do not collapse them)

The scaffold uses **two distinct marker syntaxes** so the two tiers are unambiguous:

| Marker | Tier | Who fills it | When |
|---|---|---|---|
| `<<NAME>>` | **authoring (agent-template)** | this skill (`createblueprint`) | now, while designing the blueprint |
| `{{NAME}}` | **invocation** | `clone` / `taskgraph` (via `template-replace.sh`) | later, per task, when the agent is instantiated |

- This skill fills **every** `<<...>>` marker and **must not touch any** `{{...}}` marker.
- The produced blueprint therefore contains **no `<<...>>`** and **still contains its `{{...}}`** variables.
- `template-replace.sh` only ever operates on `{{...}}`, so the two namespaces never collide.

## Which tier each thing belongs to — grounded in the existing agents

This split is taken from reading the real agents in `.kamino/agents/library/writing/`. Do not re-derive it by guessing; the agents are the source of truth.

**Authoring `<<...>>` (baked now — fixed and unique per agent):**
- `<<AGENT_NAME>>`, `<<AGENT_DESCRIPTION>>`, `<<MODEL>>`, `<<EFFORT>>` — identity (`effort` = the effort level the agent should be launched with).
- `<<PERSONA>>` — the persona prompt / role / voice (e.g. the food-critic reviewer, the adversarial fact-checker). This is an agent-template variable, **never** an invocation variable.
- `<<RULES>>`, `<<STEPS>>`, `<<OUTPUT_FORMAT>>` — fixed behavior.
- `<<INPUTS>>` (the input schema), `<<REQUIRED_INPUTS>>` (the frontmatter list of invocation variables), and `<<HARDCODED_PROPERTIES>>` (the frontmatter list of baked property sections).

**Invocation `{{...}}` (filled later, per task):**
- one variable per declared input — the input *materials* (e.g. `{{STYLE_GUIDE}}`, `{{ARTICLE}}`, `{{SOURCE_MATERIAL}}`, `{{CONTEXT}}`), file paths by default.
- `{{GOAL}}` — the per-task objective.
- `{{OUTPUT_FILE}}` — where this run writes its result.

**Fixed standards (already written into the scaffold — do not interview for these, do not change them):**
- `<DEFINITION_OF_DONE>` carries a fixed standard line: *"All steps have been completed following the rules to reach the goal and the output was provided in the required output format."*
- The input convention note (each input may be inline content **or** a path to a file to read) is a fixed line above `<INPUTS>`.

## Dynamic field discovery (the skill must be smart)

Do **not** hardcode the list of fields to ask for. Derive it from the scaffold:

1. Read `.kamino/agents/agent-blueprint.template.md`.
2. Extract every distinct `<<...>>` marker. Those — and only those — are the authoring fields you must collect from the user.
3. Extract every distinct `{{...}}` marker. Those are the invocation variables you must preserve untouched, and they (plus any inputs you add inside `<<INPUTS>>`) define `required_inputs`.

This keeps the skill correct even if the scaffold changes: new `<<...>>` markers automatically become new interview questions; new `{{...}}` markers are automatically preserved.

## Paths

```text
.kamino/agents/agent-blueprint.template.md          # the scaffold to read, discover markers in, and fill
.kamino/agents/index.md                             # agent registry (read for collisions + append new entry)
.kamino/agents/ad-hoc/<category>/<agent-name>.md    # where the new (ad-hoc) blueprint is written
```

## Rules

1. Never invent a required value. For each discovered `<<...>>` field, ask the user. If a needed value still cannot be obtained, fail with a clear error — do not fall back to a default.
2. Fill **every** `<<...>>` marker. After authoring, **no `<<...>>` may remain** anywhere in the file (frontmatter or body).
3. Never fill or remove a `{{...}}` marker. Every `{{...}}` in the scaffold must appear, unchanged, in the produced blueprint.
4. When filling `<<INPUTS>>`, write one UPPERCASE-tagged block per input, each wrapping a `{{...}}` invocation variable, e.g. `<STYLE_GUIDE>\n{{STYLE_GUIDE}}\n</STYLE_GUIDE>`. Default each input to a **file path** (so blueprints can be chained by filename — see `taskgraph`); use inline content only when the user explicitly wants it.
5. `<<REQUIRED_INPUTS>>` MUST list exactly the `{{...}}` invocation variables present in the finished blueprint — the input variables you placed inside `<<INPUTS>>`, plus `GOAL` and `OUTPUT_FILE` — no extras, no omissions.
6. `<<HARDCODED_PROPERTIES>>` MUST list every **property** section whose content is baked into the blueprint rather than a bare `{{VAR}}` passthrough — with the current scaffold at minimum `OUTPUT_FORMAT`, plus any property you bake (e.g. a fixed `GOAL` or `STYLE_GUIDE` when seeding a variant). Structural sections (`DEFINITION_OF_DONE`, `RULES`, `STEPS`, `INPUTS`) are never listed. The list must be disjoint from `<<REQUIRED_INPUTS>>`. Do not interview for this list — derive it from what you authored.
7. Treat `agent_name` as kebab-case and unique within its category across **both** tiers (`library/` and `ad-hoc/`). If it collides with an existing `index.md` entry, stop and report the collision.
8. Author by direct substitution into a copy — do not edit the scaffold asset in place, and do not use `template-replace.sh` (that script targets `{{...}}`, the tier you must preserve).
9. Only write the blueprint into the ad-hoc tier `.kamino/agents/ad-hoc/<category>/` after the no-`<<...>>`-remain and all-`{{...}}`-present checks pass and `.kamino/scripts/template-variable-checks.sh` passes on the finished file.
10. After writing, append one line to `.kamino/agents/index.md` under the matching category heading, using the `ad-hoc/<category>/<agent-name>.md` path. Create the heading if it does not exist.
11. Keep all section tags UPPERCASE (`<GOAL>`, `<DEFINITION_OF_DONE>`, `<INPUTS>`, `<OUTPUT_FILE>`, `<RULES>`, `<STEPS>`, `<OUTPUT_FORMAT>`), matching the scaffold and the existing agents.

## Interview

For each `<<...>>` marker discovered in the scaffold, ask the user for its content. With the current scaffold these are:

- `<<AGENT_NAME>>` — kebab-case agent name.
- `<<AGENT_DESCRIPTION>>` — one or two sentences; shaped by the agent's purpose and persona.
- `<<MODEL>>` — `sonnet`, `opus`, or `haiku`.
- `<<EFFORT>>` — the effort level to launch the agent with (e.g. `low`, `medium`, `high`, `xhigh`); set it to match how much reasoning the agent's job demands.
- `<<PERSONA>>` — the agent's character / voice and the framing of its job (this is the "personality" to give it).
- `<<INPUTS>>` — the named inputs the agent needs, each wrapped in an UPPERCASE tag around a `{{...}}` variable (file paths by default).
- `<<REQUIRED_INPUTS>>` — derived from the inputs above plus `GOAL` and `OUTPUT_FILE`; not interviewed.
- `<<HARDCODED_PROPERTIES>>` — derived from the baked property sections (at minimum `OUTPUT_FORMAT`); not interviewed.
- `<<RULES>>` — the constraints the agent must obey.
- `<<STEPS>>` — the ordered steps the agent follows.
- `<<OUTPUT_FORMAT>>` — the exact structure the agent must produce.

Always confirm before writing: this list is whatever `<<...>>` markers the scaffold actually contains, not a fixed set.

## Seeding from an existing agent (variant creation)

Often the goal is "make a new agent like an existing one but with some baked behavior changed" — e.g. the same writer but emitting Markdown instead of JSON. This is the path `factory` / `taskgraph` route here when an existing agent *almost* fits but the task would **contradict** one of its baked properties, so adapting it through inputs would be unsafe.

In that case, seed the blueprint from the closest existing agent instead of authoring every field from scratch:

1. Read the closest existing agent from `.kamino/agents/` (either tier).
2. Use its baked sections — persona, rules, steps, output format, and input schema — as the **default values** for the corresponding `<<...>>` authoring fields.
3. Modify only the fields the task requires to differ; keep the rest as-is.
4. Give the new agent its own unique `agent_name` — never overwrite or shadow the original. The new variant is still written to `ad-hoc/`.

The scaffold still defines the structure and the two-marker rules (`<<authoring>>` vs `{{invocation}}`); the existing agent only supplies starting content for the authoring fields.

## Steps

1. Read the scaffold `.kamino/agents/agent-blueprint.template.md`.
2. Discover its `<<...>>` markers (authoring fields) and `{{...}}` markers (invocation variables to preserve).
3. Read `.kamino/agents/index.md` to learn existing categories and check for an `agent_name` collision (across both tiers).
4. Interview the user for every discovered `<<...>>` field. Stop and ask if any required value is missing.
5. Build the concrete content for each `<<...>>` field. For `<<INPUTS>>`, produce one UPPERCASE-tagged block per input wrapping a `{{VAR}}` token; set `<<REQUIRED_INPUTS>>` to those variable names plus `GOAL` and `OUTPUT_FILE`; set `<<HARDCODED_PROPERTIES>>` to the baked property sections (at minimum `OUTPUT_FORMAT`).
6. Author the blueprint: copy the scaffold to a temp file and substitute every `<<...>>` marker with its value, leaving all `{{...}}` markers untouched.

```bash
mkdir -p .kamino/tmp
tmp_file="$(mktemp .kamino/tmp/blueprint.XXXXXX.md)"
```

7. Verify the authoring tier is complete — **no `<<...>>` may remain**:

```bash
grep -nE '<<[^>]+>>' "$tmp_file" && { echo "Authoring incomplete: <<...>> markers remain" >&2; exit 1; } || true
```

8. Verify the invocation tier survived — every `{{...}}` from the scaffold must still be present (at minimum `{{GOAL}}`, `{{OUTPUT_FILE}}`, and each input variable).
9. Verify the frontmatter contract — the finished blueprint must pass the library validator:

```bash
.kamino/scripts/template-variable-checks.sh "$tmp_file"
```

10. Move the finished blueprint into the ad-hoc tier:

```bash
mkdir -p ".kamino/agents/ad-hoc/<category>"
mv "$tmp_file" ".kamino/agents/ad-hoc/<category>/<agent-name>.md"
```

11. Append the registry line to `.kamino/agents/index.md` under the category heading:

```text
ad-hoc/<category>/<agent-name>.md - <agent_description>
```

12. Return the summary.

## Failure Conditions

Blueprint creation fails if:

1. The scaffold `.kamino/agents/agent-blueprint.template.md` cannot be read.
2. `.kamino/agents/index.md` does not exist.
3. A discovered `<<...>>` field's value is missing and cannot be obtained from the user.
4. The chosen `agent_name` collides with an existing entry in either tier.
5. Any `<<...>>` marker remains after authoring.
6. Any `{{...}}` invocation variable from the scaffold is missing from the result.
7. The finished blueprint fails `.kamino/scripts/template-variable-checks.sh`.
8. The blueprint file or the index entry cannot be written.

## Output Format

```markdown
# Blueprint Created

## Blueprint
- Name: `<agent_name>`
- Category: `<category>`
- File: `.kamino/agents/ad-hoc/<category>/<agent-name>.md`
- Model: `<model>`   Effort: `<effort>`

## Baked authoring fields (<<...>>)
| Field | Value |
|---|---|
| Persona |  |
| Inputs (schema) |  |
| Rules |  |
| Steps |  |
| Output format |  |

## Open invocation variables ({{...}}) — filled later by clone / taskgraph
- `{{GOAL}}`, `{{OUTPUT_FILE}}`, plus one per input: <input vars>

## Registry
- Added to `.kamino/agents/index.md` under `## <Category>`:
  `ad-hoc/<category>/<agent-name>.md - <description>`
```

## Success Criteria

The skill succeeds only when:

1. A new blueprint file exists at `.kamino/agents/ad-hoc/<category>/<agent-name>.md`.
2. Every `<<...>>` authoring marker is filled (none remain), including `<<PERSONA>>`.
3. Every `{{...}}` invocation variable from the scaffold is preserved.
4. The blueprint is registered in `.kamino/agents/index.md`.
