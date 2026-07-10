---
name: inventory
description: Lists all curated (library-tier) agents with their purpose. Use when the user asks what agents are available, wants to see the agent library, or needs to pick an agent and wants a quick overview. Runs a shell script to extract names and descriptions from the library and presents the result as a formatted inventory — does not read individual agent files.
---

# Inventory

Use this skill to show a formatted overview of all agents in the **library tier** (`.kamino/agents/library/`). It is a read-only inspection tool — it does not select, instantiate, or modify any agent.

## Rules

1. Run the shell script below to collect agent data. This is the **only** source of information permitted — do not read any individual agent `.md` file, do not consult `.kamino/agents/index.md`, and do not use memorised knowledge about the agents.
2. Generate the response **exclusively** from the script's stdout. If a field is blank in the output, leave it blank in the response — do not infer or fill it.
3. If the script exits non-zero or produces no output, report the error verbatim and stop.
4. Do not modify, create, or delete any file.

## Steps

1. Run the script from the **project root** (the directory that contains `.kamino/`):

```bash
.kamino/scripts/list-library-agents.sh
```

2. Parse the stdout. Each agent record is a block of five lines separated by `---------`:

```
path: <relative-path-from-library/>
name: <agent_name>
description: <agent_description>
required_inputs: {{VAR1}}, {{VAR2}}, ...
hardcoded_properties: PROP1, PROP2, ...
```

3. Format and return the inventory using the Output Format below.

## Output Format

```markdown
# Agent Library Inventory

| Path | Name | Description | Required Inputs | Hardcoded Properties |
|---|---|---|---|---|
| `<path>` | `<name>` | <description> | <required_inputs> | <hardcoded_properties> |
...

**Total: N agents**
```

## Failure Conditions

- Script exits non-zero → report the error and stop.
- Script produces no output → report "No agents found in library." and stop.
- Do not attempt to recover by reading files directly.
