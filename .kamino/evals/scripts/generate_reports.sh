#!/bin/bash
# Regenerate every derived report artifact from the current ledger and corpora:
# one calibration markdown report per discovered corpus, the error-analysis
# page, the difficulty-calibration page, and the sweep-comparison page.
# Idempotent; reads only factory data under .kamino/evals/tasks/.
#
# Corpus-agnostic by discovery: any .kamino/evals/tasks/corpus-<name>/ dir that
# ships corpus-<name>-ranking.json + corpus-<name>-index.json gets a
# calibration report and joins the difficulty page. A virgin factory (no
# corpora, empty ledger) regenerates cleanly: per-corpus blocks are skipped
# with a note and the ledger-driven pages render their empty states.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
TASKS="$REPO/.kamino/evals/tasks"
SCRIPTS="$REPO/.kamino/evals/scripts"
LEDGER="$TASKS/task-outcome-ledger.jsonl"

# The ledger is append-only and may not exist yet on a fresh factory.
[ -f "$LEDGER" ] || : > "$LEDGER"

echo "Regenerating reports from $LEDGER"

# Discover corpora: corpus-<name>/ dirs carrying their ranking + index.
CORPORA=()
for dir in "$TASKS"/corpus-*/; do
  [ -d "$dir" ] || continue
  name="$(basename "$dir")"
  name="${name#corpus-}"
  ranking="$dir/corpus-$name-ranking.json"
  index="$dir/corpus-$name-index.json"
  if [ -f "$ranking" ] && [ -f "$index" ]; then
    CORPORA+=("$name")
  else
    echo "skipping corpus-$name (no ranking/index staged yet)"
  fi
done

if [ "${#CORPORA[@]}" -eq 0 ]; then
  echo "no corpora discovered — skipping calibration, errors, and difficulty pages"
else
  for name in "${CORPORA[@]}"; do
    uv run "$SCRIPTS/difficulty_calibration_report.py" report \
      --ranking "$TASKS/corpus-$name/corpus-$name-ranking.json" \
      --ledger "$LEDGER" \
      --corpus-index "$TASKS/corpus-$name/corpus-$name-index.json" \
      --format markdown > "$TASKS/calibration-report-$name.md"
    echo "wrote $TASKS/calibration-report-$name.md"
  done

  # errors.html enriches attempts with one corpus's BT ranks; use the first
  # discovered corpus.
  first="${CORPORA[0]}"
  uv run "$SCRIPTS/build_error_analysis_ui.py" \
    --ledger "$LEDGER" \
    --output "$TASKS/errors.html" \
    --ranking "$TASKS/corpus-$first/corpus-$first-ranking.json" \
    --corpus-index "$TASKS/corpus-$first/corpus-$first-index.json" \
    --failures-dir "$TASKS/failures" \
    --trace-reviews-dir "$TASKS/trace-reviews" \
    --catalog "$TASKS/failure-mode-catalog.md" \
    --format html

  DIFFICULTY_ARGS=()
  for name in "${CORPORA[@]}"; do
    DIFFICULTY_ARGS+=(--corpus "corpus-$name" \
      --ranking "$TASKS/corpus-$name/corpus-$name-ranking.json" \
      --corpus-index "$TASKS/corpus-$name/corpus-$name-index.json")
  done
  uv run "$SCRIPTS/build_difficulty_report_ui.py" \
    "${DIFFICULTY_ARGS[@]}" \
    --ledger "$LEDGER" \
    --evaluations-dir "$TASKS/evaluations" \
    --failures-dir "$TASKS/failures" \
    --output "$TASKS/difficulty.html" \
    --format html
fi

uv run "$SCRIPTS/build_sweep_report_ui.py" \
  --ledger "$LEDGER" \
  --output "$TASKS/sweeps.html" \
  --format json

echo "All reports regenerated."
