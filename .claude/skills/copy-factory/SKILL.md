---
name: copy-factory
description: Copies this Kamino factory into a new folder in virgin state by running copy-factory.sh — installing both the .claude/ control plane (afac plugin manifest, skills, judge and classifier agents) and the .kamino/ data plane (blueprints, deterministic scripts, eval schemas), with an empty ledger and no inherited run data. Use when the user wants to install, clone, bootstrap, or set up a fresh agent factory in another repository or directory, asks to copy the factory somewhere, or invokes /copy-factory.
---

# Copy Factory

Use this skill to install a fresh, working copy of this factory into another directory. The factory is **two planes and both are required**: copying `.kamino/` alone produces an inert data plane, because the slash-command skills and judge/classifier agents that drive it live in `.claude/`, and the afac plugin is project-local — it is not installed globally. A target that receives only `.kamino/` has no `/factory`, `/run`, `/clone` at all.

The copy is **virgin**: machinery only, no accumulated run data. The ledger lands present-but-empty, which is the cold-start signal in all three readers (candidate search, route recommendation, ledger read). The target then starts its own flywheel.

## What it runs

```bash
.kamino/scripts/copy-factory.sh <target-dir> [--force] [--dry-run]
```

The script carries the manifest. Do not re-implement the copy with `cp`, `rsync`, or a shell loop — the allowlist and the virgin-state guarantees live in the script and only there.

## What gets copied

| Plane | Path | Contents |
|---|---|---|
| Control | `.claude/.claude-plugin/` | afac plugin manifest |
| Control | `.claude/agents/` | judge and classifier agents |
| Control | `.claude/skills/` | every factory slash-command skill |
| Data | `.kamino/factory-config.json` | routing thresholds, central config |
| Data | `.kamino/agents/` | `index.md`, blueprint template, `library/`, `ad-hoc/` |
| Data | `.kamino/evals/scripts/` | deterministic engine (compile, record, route, report) |
| Data | `.kamino/evals/tasks/` | schema docs, sample fixtures, `sweeps.html` shell — **enumerated file by file**, because this directory mixes tracked schemas with generated run output |
| Data | `.kamino/scripts/` | template and library shell helpers |

Created empty in the target: `.kamino/blueprints/`, `.kamino/dispatch-queue/`, and the `evals/tasks/` output dirs (`candidates`, `details`, `difficulty`, `evaluations`, `failures`, `outcomes`, `replays`, `trace-reviews`), plus a 0-byte `task-outcome-ledger.jsonl`.

Also written: `.kamino/dispatch-queue` and `.kamino/auto-research` appended to the target's `.gitignore`.

## What never gets copied

`.kamino/tests/` (dev-only) · `.claude/settings.local.json` (local permission grants, repo-specific) · `.kamino/evals/tasks/corpus-*/` (generated corpora) · `.kamino/auto-research/` (per-run workspaces) · dispatch capsules · generated reports (`errors.html`, `calibration-report.md`, `corpus-ranking.json`) · caches and `.DS_Store` · the source repo's `justfile`, `data/`, and `reports/`.

## Rules

1. Require an explicit target directory. Never guess one, and never default to the current repo.
2. Run the script; do not re-implement its logic or hand-copy any part of the manifest.
3. The script refuses a target that already has a factory surface. Do not pass `--force` unless the user has been told what will be replaced and agrees.
4. It also refuses a target equal to, or nested inside, the source factory. Do not work around this.
5. If the target is an existing project, say so before running — the install is additive but it does touch `.gitignore`.
6. The exit code is authoritative: `0` = installed (or plan printed), `1` = refused or failed.
7. Report what the script reported. Do not claim a working factory without running the verification in step 4.

## Steps

1. Determine the target directory from the user. Confirm whether it is empty or an existing project.
2. Optionally run with `--dry-run` first and show the plan — do this whenever the target already contains work.
3. Run `.kamino/scripts/copy-factory.sh <target-dir>`.
4. Verify in the target: run `.kamino/scripts/template-variable-checks.sh .kamino/agents/`. It must pass.
5. Report the install summary, the verification results, and note that factory slash commands become available in the target once Claude Code is started there.

## Output Format

```markdown
# Copy Factory — <target>

| Plane | Installed |
|---|---|
| Control (.claude/) | <N> skills, <N> agents |
| Data (.kamino/) | <N> blueprints, <N> eval scripts |

Virgin state: ledger empty (cold start), no corpora, no dispatch capsules, no reports.
Target adjustments: <.gitignore lines added>

Verification:
- Template contracts: <pass|fail>

Exit code: <0|1>
```

## Success Criteria

The skill succeeds only when the script exits `0` **and** the verification passes in the target: every agent blueprint satisfies the template-variable contract. A copy that lands files but fails the check is not a working factory — report it as a failure.
