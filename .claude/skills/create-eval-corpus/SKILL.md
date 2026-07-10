---
name: create-eval-corpus
description: Ingests a source of problems + solutions from disk (a crawl, a dataset, a folder of problem files) into a standard-shape evaluation corpus the factory can sweep. Because the factory engine stays 100% corpus-agnostic, this skill does NOT hardcode any source format — it interviews the user, AUTHORS a corpus-specific builder script on demand, snapshots the ground-truth answers, runs the builder, validates the result against the corpus integrity gates, and records provenance. The authored builder and its input data live with the ingestion record (git-tracked), never in the generic engine. Use when the user wants to turn a problem set / dataset / answer list into a corpus, add a new corpus, ingest LeetCode/Project-Euler-style problems, or "make a corpus out of this folder".
---

# Create Corpus

Use this skill to convert a user's **source of problems + solutions** into a standard-shape corpus under `.kamino/evals/tasks/corpus-<name>/` that `evaluate-factory` and `evaluate-agent` can sweep. The corpus is the ground truth for every downstream measurement, so its integrity is the whole job: references must pass, tests must bite, and no answer may leak to the solver.

**The engine stays corpus-agnostic; the source knowledge lives in an authored builder.** Every source is shaped differently (a directory of `.py` files, a CSV of answers, a crawl with an editorial column). This skill therefore never teaches the engine a new format. Instead it **authors a corpus-specific Python builder** that knows this one source, snapshots the ground-truth answers next to it, runs the builder to emit the standard shape, and validates the emitted corpus. `compile_run.py` and `record_run.py` never change.

**The generated builder is corpus-specific by design and lives with the ingestion record.** It is written to `.kamino/evals/ingest/<corpus-name>/`, together with the answer data it consumes and the `build-report.json` provenance — and all of it is **tracked in git**. Generated does not mean disposable: the builder is the reproducible record of how the corpus was made. It MUST NOT be written to `.kamino/evals/scripts/` — that directory holds only the generic engine. (The repo previously lost track of exactly such builders in an untracked `scratch/` folder; do not repeat that.)

**Never fabricate.** The builder copies genuine reference solver code when the source ships it, or echoes a cross-validated ground-truth answer as an honestly-labeled answer-only reference. It never invents an answer, and never passes off non-solving code as a real algorithm. A task whose answer or reference cannot be obtained is **skipped and reported**, never guessed.

## Inputs

```xml
<source_dir>
/Users/flo/Developer/github-cloned/SomeProblemSet
</source_dir>

<corpus_name>
project-euler
</corpus_name>

<answer_source>
Solutions.md in the source repo (a "problem-id → answer" table); the ground truth for every task's expected value.
</answer_source>

<answer_contract>
exact-value
</answer_contract>
```

`<corpus_name>` is kebab-case and becomes `corpus-<corpus_name>/`. `<answer_contract>` names how correctness is checked; use one of:

- `unit-tests` — the visible tier calls a class/function on example (and additional) inputs and asserts outputs; the reference is the source's genuine solver code, verified to pass.
- `exact-value` — a zero-argument `solve()` must return one known value; the test asserts equality (int-exact or numeric).
- `string-precision` — like `exact-value`, but the answer is compared as a string/number rounded to a stated number of significant digits (model the Project-Euler `canonical`/`matches` helper).

Optional:

```xml
<answer_source_secondary>                                    <!-- a second, independent answer source -->
The original crawl's answer CSV; cross-validate value-by-value against <answer_source>.
</answer_source_secondary>

<hidden_tier>                                                 <!-- which tasks get tests_hidden/ and what it stresses -->
Top-20 hardest tasks: constraint-scale stress + adversarial edges (recursion at scale, O(n log n) where O(n^2) passes the visible tier).
</hidden_tier>

<precision_digits>5</precision_digits>                        <!-- string-precision contract only: significant digits -->
```

**Hidden-tier guidance (suggest, don't prescribe).** Default by contract: `unit-tests` → offer a hidden tier for the hardest slice of tasks; `exact-value` / `string-precision` → no hidden tier (a fixed answer has no input to stress — the precision matcher is the rigor). When offering, propose these adversarial patterns and let the user pick per corpus; a memorized-looking solution should fail at least one:

- **recursion-at-scale** — one structure at maximum constraint depth (per-element recursion blows the ~1000-deep limit → `RecursionError`)
- **complexity smoke** — the input shape where the naive complexity hangs and the intended one returns instantly (the 300 s verification bound turns the hang into a recorded resource failure)
- **degenerate-but-legal** — all-duplicates, worst-case orderings, boundary-crossing runs at full constraint size

## Paths

```text
<source_dir>/                                        # the user's problems + solutions — READ-ONLY, referenced by absolute path, never modified
.kamino/evals/tasks/corpus-<name>/                   # OUTPUT corpus (git-tracked product)
.kamino/evals/tasks/corpus-<name>/corpus-<name>-index.json  # task manifest (schema kamino451.corpus-index.v1) — enumeration + count reconciliation
.kamino/evals/tasks/corpus-<name>/<task-id>/         # one directory per task (standard shape below)
.kamino/evals/ingest/<corpus-name>/build_corpus.py   # the AUTHORED corpus-specific builder (git-tracked provenance)
.kamino/evals/ingest/<corpus-name>/answers.<ext>     # snapshot of the ground-truth answer data the builder consumes (git-tracked)
.kamino/evals/ingest/<corpus-name>/build-report.json # provenance: source, counts, answer validation, gate results, timestamp
.kamino/evals/scripts/                               # GENERIC engine only — the builder MUST NOT be written here
```

Standard corpus shape — one directory per task under `.kamino/evals/tasks/corpus-<name>/`:

```text
<task-id>/
  task.md                     # problem statement + required signature/starter — ALL the solver ever sees
  meta.json                   # task_id, title, intended_difficulty, solution_file, test_command (+ answer provenance for value contracts)
  solution_reference.py       # oracle-verified reference (never shown to solvers, never staged into a run)
  tests/test_solution.py      # visible tier — ground truth
  tests_hidden/test_hidden.py # OPTIONAL hidden tier — constraint-scale stress, adversarial edges
```

**Test import contract (load-bearing — the builder MUST emit tests this exact way).** Every test module resolves the solution relative to itself and imports a module named `solution`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from solution import Solution          # or: from solution import solve
```

Because the path inserted is the tests directory's parent, the same suite works in two places: during gate checks the solution sits at the task root (parent of `tests/`), and during a real run `record_run.py` stages it at `verify/` (parent of `verify/tests/`). The reference is stored as `solution_reference.py`; to exercise a suite it is staged as `solution.py` beside the tests — exactly as `.kamino/tests/test_corpus_integrity_any.py` and `record_run.py` do it.

## Rules

1. **The source is read-only.** Never write into `<source_dir>`. Read it, snapshot the ground-truth answers into the ingest dir, and derive the corpus from that snapshot.
2. **Author, do not hardcode.** The engine (`.kamino/evals/scripts/`) never learns this source. All source-specific parsing lives in the authored `build_corpus.py` under `.kamino/evals/ingest/<corpus-name>/`. Writing a builder into `.kamino/evals/scripts/` is forbidden.
3. **Provenance is tracked.** The builder, its input answer data, and `build-report.json` are committed with the corpus. They are generated but not ignorable — they are the record of how the corpus was made.
4. **Never fabricate answers or references.** Use genuine solver code (unit-tests contract) or a cross-validated known value echoed by an honestly-labeled answer-only reference (value/precision contracts). Never invent a value; never present non-solving code as an algorithm. Unknown → skip and report, never guess.
5. **Idempotent-or-refuse.** If `.kamino/evals/tasks/corpus-<name>/` (or `.kamino/evals/ingest/<corpus-name>/`) already exists, STOP and ask before overwriting. Re-ingestion must be an explicit choice, never a silent clobber.
6. **The task.md stays invisible to graders.** `task.md` contains the problem statement and the required signature/starter only — no answer, no reference code, no test content. If the source interleaves the answer with the prompt, the builder strips it.
7. **The corpus is accepted only when every integrity gate passes** (below). A gate failure on a task means that task is rejected and reported — never shipped.
8. **No silent drops.** The emitted task count must reconcile with the source; every skipped or failed conversion is listed with its reason in `build-report.json`.
9. Do not reference or invoke AutoResearch anywhere in this skill's flow.

**Integrity gates — every one MUST pass before the corpus is accepted:**

| # | Gate | How it is checked |
|---|---|---|
| G1 | Each `solution_reference.py` passes its own `tests/` (and `tests_hidden/` when present) | stage the reference as `solution.py` beside the tests, run the test tiers, assert exit 0 |
| G2 | Each suite FAILS with no solution module present | run the same tiers with no `solution.py` staged, assert non-zero exit (proves the tests bite) |
| G3 | `task.md` leaks nothing | scan `task.md`: no expected answer, no reference source, no test assertions |
| G4 | Each `meta.json` declares a runnable `test_command` | parse `meta.json`; confirm `test_command` is present and runnable (`uv run pytest tests -q`) |
| G5 | Task count reconciles with the source | index task count + skipped/failed lists account for every source item |

## Steps

1. **Interview and resolve inputs.** Collect `<source_dir>` (must exist), `<corpus_name>` (kebab-case → `corpus-<name>`), `<answer_source>`, `<answer_contract>` (one of `unit-tests` / `exact-value` / `string-precision`), and any optional `<answer_source_secondary>`, `<hidden_tier>`, `<precision_digits>`. Any required value missing → stop and ask; do not default.
2. **Guard against overwrite.** If `.kamino/evals/tasks/corpus-<name>/` or `.kamino/evals/ingest/<corpus-name>/` already exists, STOP and confirm with the user before proceeding (Rule 5).
3. **Survey the source.** Read `<source_dir>` and the answer source enough to learn: how problems are laid out, how to derive a stable kebab-case `task_id` per problem, where each statement/signature comes from, where each ground-truth answer comes from, and whether the source ships genuine solver code (→ `unit-tests` reference) or only answers (→ answer-only reference). Count the source items. Pick the test template from `<answer_contract>`.
4. **Snapshot the ground truth + author the builder.** Create `.kamino/evals/ingest/<corpus-name>/`. Copy the answer data the builder will consume into it (e.g. `answers.json`) so the build is reproducible from tracked inputs. Then author `.kamino/evals/ingest/<corpus-name>/build_corpus.py` — a self-contained `uv run` script that, per source problem:
   - derives `task_id`, `title`, `intended_difficulty` (from the source if present, else a stated default);
   - writes `task.md` = statement + examples + the exact required signature/starter, and nothing else (Rule 6);
   - writes `meta.json` with `schema_version: "kamino451.corpus-task.v1"`, `task_id`, `title`, `intended_difficulty`, `solution_file: "solution.py"`, `test_command: "uv run pytest tests -q"`, `source`, plus answer provenance for value contracts (`expected_answer`, `answer_kind`/`answer_format`, `answer_source`, and any cross-validation flag);
   - writes `solution_reference.py` = the source's genuine solver code (unit-tests) or an honestly-labeled answer-only reference returning the cross-validated value (value/precision);
   - writes `tests/test_solution.py` using the load-bearing import contract and the chosen answer-contract template; writes `tests_hidden/test_hidden.py` only for tasks selected by `<hidden_tier>`;
   - skips any problem whose answer or reference cannot be obtained, recording the reason (Rule 4, Rule 8);
   - emits `corpus-<name>-index.json` (`schema_version: "kamino451.corpus-index.v1"`, one entry per emitted task) and prints a JSON summary to stdout: converted / skipped / failed lists and the answer-validation record (including value-by-value reconciliation against `<answer_source_secondary>` when given).
5. **Run the builder.**

   ```bash
   uv run .kamino/evals/ingest/<corpus-name>/build_corpus.py --source "<source_dir>" --out ".kamino/evals/tasks/corpus-<name>" --format json
   ```

   Capture its stdout summary. The builder should build-and-verify each task as it writes it; the authoritative acceptance is the gate pass in Step 6.
6. **Run the integrity gates** over the emitted corpus (G1–G5 above), per task, in index order. For each task, in a scratch/temp working copy of `task.md` + `tests/` (+ `tests_hidden/`):
   - G1: copy `solution_reference.py` → `solution.py` beside the tests, run `uv run --project . pytest <tiers> -q`, require exit 0.
   - G2: remove `solution.py`, run the same command, require non-zero exit.
   - G3: scan `task.md` for the expected answer string, reference identifiers, and test assertions; require none present.
   - G4: parse `meta.json`; require a runnable `test_command`.
   - G5: reconcile the index count + skipped/failed lists against the source item count; require full account.
   When `<answer_source_secondary>` was given, confirm the builder's value-by-value reconciliation and record any mismatches. Any gate failure → reject that task and report it; do not ship a corpus with a red gate.
   Then run the repo's generic corpus gate — the same discovery-based integrity test CI runs, so ingestion and CI share one implementation:

   ```bash
   uv run pytest .kamino/tests/test_corpus_integrity_any.py -q
   ```

   It validates every corpus present (index↔disk reconciliation, reference passes the task's own `test_command`, tests bite without a solution, statement leaks no grader content) on a deterministic sample. It must be green before the corpus ships.
7. **Write provenance.** Write `.kamino/evals/ingest/<corpus-name>/build-report.json`: `source` (absolute `<source_dir>` + answer sources), counts (source items, converted, skipped, failed with reasons), the answer-validation / cross-validation record, the per-gate results table, and a UTC timestamp.
8. **Report and hand off.** Print the ingestion report (Output Format) and the handoff line (Steps → downstream).

## Failure Conditions

Ingestion fails (stop, report, ship nothing) if:

1. `<source_dir>` is missing or empty, or a required input (`<corpus_name>`, `<answer_source>`, `<answer_contract>`) cannot be obtained.
2. `corpus-<name>/` or `ingest/<corpus-name>/` already exists and the user has not authorized overwrite (Rule 5).
3. The authored `build_corpus.py` errors on its own valid inputs, or is (wrongly) written under `.kamino/evals/scripts/`.
4. Any integrity gate (G1–G5) fails and cannot be resolved by skipping-and-reporting the offending task.
5. A required answer or reference would have to be fabricated to proceed (Rule 4).
6. The task count cannot be reconciled with the source (an unexplained drop).

A source problem that is legitimately skipped-and-reported is NOT a failure — it is recorded provenance.

## Output Format

```markdown
# Corpus Ingested — corpus-<name>

- Source: <source_dir>  (answers: <answer_source>[ + <answer_source_secondary>])
- Contract: <answer-contract>   Hidden tier: <tasks or "none">
- Tasks: converted <c> · skipped <s> · failed <f>  (of <source total>)

## Integrity gates
| Gate | Result |
|---|---|
| G1 references pass their tiers | PASS (<c>/<c>) |
| G2 suites fail without a solution | PASS (<c>/<c>) |
| G3 task.md leaks nothing | PASS |
| G4 meta.json test_command runnable | PASS |
| G5 count reconciles with source | PASS (<c> + <s> + <f> = <source total>) |

## Skipped / failed (reasons)
- <task or source item>: <reason>

## Provenance
- Builder: `.kamino/evals/ingest/<corpus-name>/build_corpus.py`
- Report: `.kamino/evals/ingest/<corpus-name>/build-report.json`

## Handoff
Stage the difficulty pipeline for `corpus-<name>`, then sweep it:
`task-evaluate` each task.md → `rank-task-difficulty` (Bradley-Terry) → `corpus-<name>-ranking.json` → placements (`difficulty/`) + candidate searches (`candidates/`), then run `evaluate-factory` or `evaluate-agent` against `.kamino/evals/tasks/corpus-<name>/`.
```

## Success Criteria

Ingestion succeeds when `.kamino/evals/tasks/corpus-<name>/` holds one standard-shape directory per converted task plus its `corpus-<name>-index.json`; every task passed all five integrity gates (references pass their tiers, the tiers bite without a solution, `task.md` leaks nothing, `meta.json` carries a runnable `test_command`); the emitted count reconciles with the source with every skip/failure named; the authored `build_corpus.py`, its answer snapshot, and `build-report.json` are written under `.kamino/evals/ingest/<corpus-name>/` (git-tracked, never under `.kamino/evals/scripts/`); no answer or reference was fabricated; and the report ends with the difficulty-pipeline handoff line.
