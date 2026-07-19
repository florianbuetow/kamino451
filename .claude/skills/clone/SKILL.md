---
name: clone
description: Selects the best-matching Kamino agent template for a given task, fills its template variables from the provided task and context, verifies no placeholders remain, and writes the completed agent file into the per-task folder .kamino/tasks/<task_id>/. Use when the user provides a task and wants a Kamino agent chosen and instantiated, mentions selecting or creating an agent from .kamino/agents, references the kamino agent index, or asks to run template-replace on an agent template.
---

# Clone

Use this skill when the user provides a task and wants the best matching Kamino agent template selected, instantiated, verified, and copied into the per-task folder `.kamino/tasks/<task_id>/`.

The skill discovers available agents from `.kamino/agents/index.md`, ranks candidate agents, fills the selected agent template, verifies that no template variables remain, and writes the completed agent file to `.kamino/tasks/<task_id>/` (the task id comes from `task-evaluate`; create the folder if it does not exist). All artifacts of one task live together in that folder.

## Inputs

The user must provide:

```xml
<task>
{{TASK}}
</task>

<context>
{{CONTEXT}}
</context>
```

Optional:

```xml
<output_name>
{{OUTPUT_NAME}}
</output_name>

<model>haiku</model>
<effort>medium</effort>
```

If `<output_name>` is not provided, derive a safe filename from the selected agent name.

If `<model>` / `<effort>` are provided (e.g. by the factory's model binding or an escalation attempt), bind them into the **copied** agent's frontmatter before verification — replace the `model:` / `effort:` values in the copy only. Never edit the original blueprint, and never bind values silently: report the binding and its source in the result. Without these inputs, the blueprint's frontmatter defaults stand.

## Paths

Use these paths:

```text
.kamino/agents/index.md
.kamino/scripts/template-replace.sh
.kamino/scripts/template-replace-completed.sh
```

Agent template files are discovered through `.kamino/agents/index.md`.

## Rules

1. Treat `.kamino/agents/index.md` as the source of truth for available agents.
2. Do not guess agent files that are not listed or referenced in the index.
3. First scan the index to identify likely candidate agents.
4. Then inspect the full Markdown files for the strongest candidates.
5. Rank agents by task fit, tool fit, required template variables, available context, and output suitability.
6. Prefer the agent that can complete the task with the fewest unsupported assumptions. Search the `library/` (tested) tier first and prefer it; fall back to an `ad-hoc/` agent only when no `library/` agent fits.
7. Inspect required template variables before selecting the final agent.
8. Do not invent values for required template variables.
9. Use values from `<task>` and `<context>` when replacing template variables.
10. If a required template variable cannot be filled, agent creation must fail with a clear error.
11. Copy the selected agent Markdown file into a temporary folder before modifying it.
12. Never modify the original agent template.
13. Use `.kamino/scripts/template-replace.sh` to replace template variables.
14. Use `.kamino/scripts/template-replace-completed.sh` to verify completion.
15. If verification shows any remaining template variables, agent creation failed.
16. Only copy the completed agent file to `.kamino/tasks/<task_id>/` after verification succeeds. When filling `{{OUTPUT_FILE}}`, point it to `.kamino/tasks/<task_id>/run-report.json` unless the task dictates otherwise.
17. Return a concise summary of the selected agent, ranking rationale, filled variables, and output path.

## Template Variable Rules

Template variables are placeholders inside the selected agent Markdown file.

Common formats may include:

```text
{{VARIABLE_NAME}}
{{ VARIABLE_NAME }}
<VARIABLE_NAME>
```

Before replacement:

1. Extract all template variables from the candidate agent file.
2. Classify each variable as:
   - Required and fillable from task/context.
   - Required but missing.
   - Optional.
3. Fail before writing the final file if any required variable is missing.
4. Do not leave placeholder values such as `TODO`, `TBD`, or `{{VARIABLE}}`.

## Script Usage

`template-replace.sh` takes the template file as its only argument and reads the replacement from **stdin**. The piped input must begin with the `{{TOKEN}}` itself, followed by a space, then the replacement text (which may span multiple lines and contain any characters — replacement is literal, not regex). It replaces one token per invocation, so call it once per variable. It exits 1 if the token is not present in the file.

```bash
echo '{{TOKEN}} replacement text' | .kamino/scripts/template-replace.sh <file>
```

Example:

```bash
echo '{{GOAL}} Review this pull request for security risks' | .kamino/scripts/template-replace.sh /tmp/agent.md
```

Multi-line values work too (here-doc / heredoc or piped variable):

```bash
printf '%s' "{{CONTEXT}} $context_text" | .kamino/scripts/template-replace.sh /tmp/agent.md
```

Verification — `template-replace-completed.sh` takes the file as its only argument and exits 1 if any `{{...}}` token remains:

```bash
.kamino/scripts/template-replace-completed.sh <file>
```

Neither script supports `--help`; read the header comment in the script source for usage.

## Steps

1. Read the user-provided `<task>` and `<context>`.
2. Open `.kamino/agents/index.md`.
3. Extract the list of available agents and their referenced Markdown file paths.
4. Perform a first-pass scan of the index.
5. Select a shortlist of candidate agents that appear relevant to the task.
6. Open and inspect each candidate agent Markdown file.
7. Extract each candidate's purpose, tools, constraints, required inputs, required template variables, and output format.
8. Rank the candidates using this scoring model:

```text
Task fit:                       0 to 5
Required tool fit:              0 to 5
Context fit:                    0 to 5
Template variable fillability:  0 to 5
Output fit:                     0 to 5
Risk / ambiguity penalty:       0 to -5
```

9. Pick the highest-ranked agent.
10. If there is a tie, prefer the agent with fewer missing assumptions and fewer required template variables.
11. Create a temporary folder:

```bash
mkdir -p .kamino/tmp
tmp_dir="$(mktemp -d .kamino/tmp/agent.XXXXXX)"
```

12. Copy the selected agent Markdown file into the temporary folder:

```bash
cp "<selected_agent_file>" "$tmp_dir/"
```

13. Replace all required template variables in the copied file using `.kamino/scripts/template-replace.sh`.
13a. If `<model>` / `<effort>` were provided, bind them by editing the copied file's frontmatter `model:` / `effort:` lines (the copy only — the original blueprint is never modified).
14. Verify the copied file using `.kamino/scripts/template-replace-completed.sh`.
15. If verification fails, stop and report failure.
16. If verification succeeds, copy the completed agent Markdown file to `.kamino/tasks/<task_id>/` (create the folder if needed).
17. Return the final result.

## Failure Conditions

Agent creation fails if:

1. `.kamino/agents/index.md` does not exist.
2. No suitable agent is found.
3. The selected agent file cannot be found.
4. Required template variables cannot be filled from task/context.
5. `template-replace.sh` fails.
6. `template-replace-completed.sh` fails.
7. Any template variables remain after replacement.
8. The completed file cannot be copied to `.kamino/tasks/<task_id>/`.

## Output Format

Return Markdown with this structure:

```markdown
# Agent Creation Result

## Selected Agent

- Agent: `<agent_name>`
- Source file: `<selected_agent_file>`
- Output file: `<completed_agent_file>`

## Ranking

| Rank | Agent | Score | Reason |
|---:|---|---:|---|
| 1 |  |  |  |

## Filled Template Variables

| Variable | Value Source |
|---|---|

## Model Binding

- Model: `<blueprint default | bound value>` (source: blueprint | factory binding | escalation)
- Effort: `<blueprint default | bound value>`

## Verification

- Template replacement completed: yes/no
- Remaining template variables: none / list variables
- Verification command: `<command>`

## Result

State whether the completed agent file was copied to `.kamino/tasks/<task_id>/`.

If creation failed, state the exact failure reason.
```

## Success Criteria

The skill succeeds only when:

1. The best matching agent was selected.
2. All required template variables were replaced.
3. Verification confirms that no template variables remain.
4. The completed agent Markdown file exists in `.kamino/tasks/<task_id>/`.
