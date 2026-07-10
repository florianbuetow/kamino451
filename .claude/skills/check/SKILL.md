---
name: check
description: Validates the Kamino agent library by running template-variable-checks.sh over the .kamino/agents directory — confirming every agent's required_inputs match the {{variables}} used in its body (both directions) and that its hardcoded_properties declaration is present and accurate. Use when the user wants to check or validate the agents, verify template-variable consistency, lint the agent blueprints, or invokes /check.
---

# Check

Use this skill to validate the agent library against the agent-surface contract. Every agent must declare both `required_inputs:` and `hardcoded_properties:` in its frontmatter, and the validator enforces: (1) `required_inputs` matches the `{{VARIABLES}}` actually used in the body, in both directions; (2) every `hardcoded_properties` entry is a real, baked `<TAG>…</TAG>` section, not a bare `{{TAG}}` passthrough; (3) the two lists are disjoint; (4) the deprecated `inputs:` / `input_parameters:` keys fail outright. It is a read-only lint — it changes nothing.

## What it runs

The check is performed by `.kamino/scripts/template-variable-checks.sh`, which accepts a directory and recurses over every `*.md` file inside it. Non-agent files (`index.md`, anything without a `required_inputs` declaration) and un-filled blueprint scaffolds (a declared list still contains `<<...>>`) are skipped, not failed. A file still using the deprecated `inputs:` / `input_parameters:` keys is failed, not skipped.

Default target is the whole agent library:

```bash
.kamino/scripts/template-variable-checks.sh .kamino/agents/
```

If the user names a specific directory or a single agent file, pass that instead — the script handles both.

## Rules

1. Require an explicit target. Default to `.kamino/agents/` only because that is this skill's purpose; if the user names a path, use it. Never invent some other location.
2. Run the script; do not re-implement its logic.
3. The script's exit code is authoritative: `0` = all checked agents match, `1` = at least one mismatch (or a bad path).
4. Report exactly what the script reported — per-file ✓/✗, the skipped files, and the summary line. Do not soften a failure.
5. Do not edit any agent to "fix" a mismatch unless the user explicitly asks. This skill only reports.

## Steps

1. Determine the target: the user-supplied path, else `.kamino/agents/`.
2. Run `.kamino/scripts/template-variable-checks.sh <target>`.
3. Relay the output and the pass/fail summary. On failure, list each `✗` file and its findings verbatim (e.g. declared-but-missing variables, used-but-undeclared variables, missing or inaccurate `hardcoded_properties`, deprecated keys).

## Output Format

```markdown
# Agent Variable Check — <target>

| Agent | Result | Issues |
|---|---|---|
| writing/article-writing-agent.md | ✓ | — |
| … | ✗ | <the script's finding lines for this file> |

Skipped (not agents): <files>

Summary: <N> checked — <P> passed, <F> failed, <S> skipped.
Exit code: <0|1>
```

## Success Criteria

The skill succeeds (and the library is valid) only when the script exits `0`: every checked agent's `required_inputs` and `{{variables}}` match exactly, and every `hardcoded_properties` declaration is present and accurate.
