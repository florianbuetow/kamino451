#!/bin/bash
# copy-factory.sh - Copy this Kamino factory into a new folder in VIRGIN state.
#
#   The factory is two planes and they are BOTH required. Copying only .kamino/
#   yields an inert data plane: the slash-command skills and judge/classifier
#   agents that drive it live in .claude/, and the afac plugin is project-local
#   (it is not installed globally), so a target without .claude/ has no /factory,
#   /run, /clone, ... at all.
#
#     control plane  .claude/   plugin manifest, skills, judge+classifier agents
#     data plane     .kamino/   blueprints, deterministic scripts, evals
#
#   VIRGIN means the copy carries the machinery and none of the accumulated
#   run data. The ledger lands empty (present-but-empty = cold start in all
#   three readers), no corpora, no dispatch capsules, no auto-research
#   workspaces, no generated reports. The target starts its own flywheel.
#
#   The manifest below is an explicit ALLOWLIST. Anything not named is not
#   copied. That is deliberate: .kamino/evals/tasks/ mixes tracked schema docs
#   with generated per-run output, so it is enumerated file by file rather than
#   copied wholesale.
#
# Usage:
#   .kamino/scripts/copy-factory.sh <target-dir> [--force] [--dry-run]
#
#   --force     overwrite an existing factory in the target
#   --dry-run   print the plan, copy nothing
#
# Exit codes: 0 = factory installed (or plan printed), 1 = refused or failed.

set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# --- Manifest -----------------------------------------------------------------

# Trees copied recursively, verbatim (junk pruned afterwards).
COPY_TREES=(
    ".claude/.claude-plugin"
    ".claude/agents"
    ".claude/skills"
    ".kamino/agents"
    ".kamino/evals/scripts"
    ".kamino/scripts"
)

# Standalone files copied verbatim.
COPY_FILES=(
    ".kamino/factory-config.json"
)

# .kamino/evals/tasks/ is enumerated: these are the factory's own schema docs,
# fixtures and report shell. Everything else in that directory is run output.
COPY_EVAL_TASK_FILES=(
    "dispatch-queue-retention.md"
    "failure-mode-catalog.md"
    "run-trace-schema.md"
    "sample-difficulty-comparisons.json"
    "sample-difficulty-tasks.json"
    "sample-target-comparisons.json"
    "sample-target-task.json"
    "sweeps.html"
    "task-outcome-ledger-schema.md"
)

# Created empty in the target. Scripts write here; a fresh factory has nothing.
EMPTY_DIRS=(
    ".kamino/blueprints"
    ".kamino/dispatch-queue"
    ".kamino/evals/tasks/candidates"
    ".kamino/evals/tasks/details"
    ".kamino/evals/tasks/difficulty"
    ".kamino/evals/tasks/evaluations"
    ".kamino/evals/tasks/failures"
    ".kamino/evals/tasks/outcomes"
    ".kamino/evals/tasks/replays"
    ".kamino/evals/tasks/trace-reviews"
)

# Created as a 0-byte file: present-but-empty ledger is the cold-start signal.
EMPTY_LEDGER=".kamino/evals/tasks/task-outcome-ledger.jsonl"

# Factory-specific ignore rules appended to the target's .gitignore.
# NOTE: deliberately NOT data/ or reports/ - those are kamino451's own archive
# and report paths, and a target repo may use those names for real content.
GITIGNORE_LINES=(
    ".kamino/dispatch-queue"
    ".kamino/auto-research"
)

# Never copied, for the record (enforced by omission above + prune below):
#   .kamino/tests/                   dev-only suite; tests BUILD the factory,
#                                    deployed copies do not carry them
#   .claude/settings.local.json      local permission grants, repo-specific
#   .kamino/evals/tasks/corpus-*/    generated eval corpora
#   .kamino/auto-research/           gitignored per-run workspaces
#   .kamino/dispatch-queue/*         run capsules
#   errors.html, calibration-report.md, corpus-ranking.json, *-index.json
#   pytest.ini, justfile             root config, stays home

PRUNE_DIRS=("__pycache__" ".pytest_cache" ".mypy_cache" ".ruff_cache")
PRUNE_FILES=("*.pyc" "*.pyo" ".DS_Store")

# --- Args ---------------------------------------------------------------------

TARGET=""
FORCE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)   FORCE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*)        echo "ERROR: unknown option: $1" >&2; exit 1 ;;
        *)
            if [[ -n "$TARGET" ]]; then
                echo "ERROR: more than one target given: '$TARGET' and '$1'" >&2
                exit 1
            fi
            TARGET="$1"; shift ;;
    esac
done

if [[ -z "$TARGET" ]]; then
    echo "ERROR: no target directory given." >&2
    echo "Usage: .kamino/scripts/copy-factory.sh <target-dir> [--force] [--dry-run]" >&2
    exit 1
fi

# --- Preflight ----------------------------------------------------------------

for required in ".kamino/agents" ".kamino/evals/scripts" ".claude/skills" ".claude/agents"; do
    if [[ ! -d "$SOURCE_ROOT/$required" ]]; then
        echo "ERROR: source is not a Kamino factory - missing $required" >&2
        echo "       source root resolved to: $SOURCE_ROOT" >&2
        exit 1
    fi
done

# Resolve the target to an absolute, normalized path WITHOUT creating it - the
# guards below must be able to refuse a path without leaving a stray directory.
abspath() {
    local p="$1" out="" part
    case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
    local IFS=/
    for part in $p; do
        case "$part" in
            ''|.) ;;
            ..)   out="${out%/*}" ;;
            *)    out="$out/$part" ;;
        esac
    done
    printf '%s' "${out:-/}"
}

TARGET_ROOT="$(abspath "$TARGET")"

if [[ "$TARGET_ROOT" == "$SOURCE_ROOT" ]]; then
    echo "ERROR: target is the source factory itself: $TARGET_ROOT" >&2
    exit 1
fi

if [[ "$TARGET_ROOT" == "$SOURCE_ROOT"/* ]]; then
    echo "ERROR: target is inside the source factory: $TARGET_ROOT" >&2
    exit 1
fi

EXISTING=()
for probe in ".kamino" ".claude/skills" ".claude/agents"; do
    [[ -e "$TARGET_ROOT/$probe" ]] && EXISTING+=("$probe")
done

if [[ ${#EXISTING[@]} -gt 0 && $FORCE -eq 0 ]]; then
    echo "ERROR: target already has a factory surface: ${EXISTING[*]}" >&2
    echo "       Refusing to overwrite. Re-run with --force to replace it." >&2
    exit 1
fi

# --- Plan ---------------------------------------------------------------------

# Every guard has passed; only now may the target come into existence.
[[ $DRY_RUN -eq 0 ]] && mkdir -p "$TARGET_ROOT"

echo ""
echo "=== Kamino factory copy ==="
echo "  source: $SOURCE_ROOT"
echo "  target: $TARGET_ROOT"
[[ $DRY_RUN -eq 1 ]] && echo "  mode:   DRY RUN (nothing will be written)"
[[ $FORCE  -eq 1 && ${#EXISTING[@]} -gt 0 ]] && echo "  mode:   FORCE (replacing: ${EXISTING[*]})"
echo ""

copy_tree() {
    local rel="$1" src="$SOURCE_ROOT/$1" dst="$TARGET_ROOT/$1"
    if [[ ! -e "$src" ]]; then
        echo "  skip  $rel (absent in source)"
        return
    fi
    local n
    n=$(find "$src" -type f -not -name '.DS_Store' -not -name '*.pyc' \
            -not -path '*__pycache__*' | wc -l | tr -d ' ')
    echo "  tree  $rel ($n files)"
    [[ $DRY_RUN -eq 1 ]] && return
    mkdir -p "$(dirname "$dst")"
    rm -rf "$dst"
    cp -R "$src" "$dst"
}

copy_file() {
    local rel="$1" src="$SOURCE_ROOT/$1" dst="$TARGET_ROOT/$1"
    if [[ ! -e "$src" ]]; then
        echo "  skip  $rel (absent in source)"
        return
    fi
    echo "  file  $rel"
    [[ $DRY_RUN -eq 1 ]] && return
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
}

echo "Control plane + data plane:"
for rel in "${COPY_TREES[@]}"; do copy_tree "$rel"; done
for rel in "${COPY_FILES[@]}"; do copy_file "$rel"; done

echo ""
echo "Eval schema docs and fixtures:"
for name in "${COPY_EVAL_TASK_FILES[@]}"; do
    copy_file ".kamino/evals/tasks/$name"
done

echo ""
echo "Virgin state:"
for rel in "${EMPTY_DIRS[@]}"; do
    echo "  mkdir $rel/"
    [[ $DRY_RUN -eq 0 ]] && mkdir -p "$TARGET_ROOT/$rel"
done
echo "  empty $EMPTY_LEDGER (cold start)"
if [[ $DRY_RUN -eq 0 ]]; then
    mkdir -p "$(dirname "$TARGET_ROOT/$EMPTY_LEDGER")"
    : > "$TARGET_ROOT/$EMPTY_LEDGER"
fi

# --- Prune ---------------------------------------------------------------------

if [[ $DRY_RUN -eq 0 ]]; then
    for d in "${PRUNE_DIRS[@]}"; do
        find "$TARGET_ROOT/.kamino" "$TARGET_ROOT/.claude" -type d -name "$d" \
            -prune -exec rm -rf {} + 2>/dev/null || true
    done
    for f in "${PRUNE_FILES[@]}"; do
        find "$TARGET_ROOT/.kamino" "$TARGET_ROOT/.claude" -type f -name "$f" \
            -delete 2>/dev/null || true
    done
fi

# --- .gitignore ----------------------------------------------------------------

echo ""
echo "Ignore rules:"
GITIGNORE="$TARGET_ROOT/.gitignore"
for line in "${GITIGNORE_LINES[@]}"; do
    if [[ -f "$GITIGNORE" ]] && grep -qxF "$line" "$GITIGNORE" 2>/dev/null; then
        echo "  have  $line"
    else
        echo "  add   $line"
        if [[ $DRY_RUN -eq 0 ]]; then
            if [[ -f "$GITIGNORE" && -n "$(tail -c 1 "$GITIGNORE")" ]]; then
                echo "" >> "$GITIGNORE"
            fi
            echo "$line" >> "$GITIGNORE"
        fi
    fi
done

# --- Report --------------------------------------------------------------------

echo ""
if [[ $DRY_RUN -eq 1 ]]; then
    echo "DRY RUN complete - nothing was written."
    exit 0
fi

SKILLS=$(find "$TARGET_ROOT/.claude/skills" -name SKILL.md 2>/dev/null | wc -l | tr -d ' ')
AGENTS=$(find "$TARGET_ROOT/.claude/agents" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
BLUEPRINTS=$(find "$TARGET_ROOT/.kamino/agents/library" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
SCRIPTS=$(find "$TARGET_ROOT/.kamino/evals/scripts" -type f 2>/dev/null | wc -l | tr -d ' ')

echo "Installed: $SKILLS skills, $AGENTS agents, $BLUEPRINTS blueprints, $SCRIPTS eval scripts."
echo "Ledger: empty (cold start). No corpora, no dispatch capsules, no reports."
echo ""
echo "Verify from $TARGET_ROOT:"
echo "  .kamino/scripts/template-variable-checks.sh .kamino/agents/"
echo ""
exit 0
