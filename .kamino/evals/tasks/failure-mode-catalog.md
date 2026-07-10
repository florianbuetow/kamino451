# Failure Mode Catalog

Schema version: `kamino451.failure-mode-catalog.v1`

The controlled vocabulary for classifying failed factory run attempts. The
`run-failure-classifier` agent must use these slugs exactly; anything it cannot
ground in evidence is `unknown_failure`, never an invented slug.

Every slug carries the **component to improve** — failure attribution is what
makes the factory learn: fix the component that caused the failure, not
whichever prompt is nearest.

## Factory-decision layer

Failures caused by a compile-time decision, before the agent ever ran.

| Slug | Meaning | Component to improve |
|---|---|---|
| `wrong_template` | A different indexed agent (or a new blueprint) fit the task better. | Router / template descriptions in `index.md` |
| `wrong_model` | The bound model was too weak (or mismatched) for the task's difficulty. | Model binding / escalation policy |
| `wrong_tool` | The agent lacked a tool the task needed, or had the wrong one. | Tool/model binder |
| `missing_context` | A needed input was never supplied at instantiation. | Context assembly (input filling in `clone`/`taskgraph`) |
| `stale_context` | A supplied input was outdated or pointed at the wrong artifact. | Context assembly / upstream output wiring |
| `permission_blocked` | The agent was denied an action it legitimately needed. | Capability binding / permissions |
| `guardrail_blocked` | A guardrail stopped legitimate work. | Guardrail configuration |
| `evaluator_error` | The output was actually fine (or actually bad) and the judgment got it wrong. | Evaluator / success criteria / verification command |
| `timeout` | The attempt ran out of time or effort budget. | Effort binding / task decomposition |

## Agent-behavior layer

Failures in how the agent executed a correctly-assembled task.

| Slug | Meaning | Component to improve |
|---|---|---|
| `weak_codebase_exploration` | The agent did not look at enough of the material before acting. | Template steps (add explicit exploration steps) |
| `editing_wrong_files` | The agent wrote or modified the wrong artifact. | Template rules (tighten output boundaries) |
| `no_test_verification` | The agent never ran the available checks before finishing. | Template steps (mandate self-check) |
| `hallucinating_code` | The agent invented APIs, facts, or behavior not in its inputs. | Template rules (source-grounding constraints) |
| `premature_giving_up` | The agent stopped before exhausting its allowed retries. | Template rules / effort binding |
| `poor_context_management` | The agent lost track of provided inputs mid-run. | Template structure (restate inputs in steps) |
| `bad_final_output_format` | The agent violated its own declared `OUTPUT_FORMAT`. | Template output contract enforcement |
| `output_format_error` | The declared output contract itself was wrong for the task. | Blueprint `OUTPUT_FORMAT` definition |
| `introducing_new_bugs` | The agent's change broke something that previously worked. | Template steps (regression check step) |
| `ineffective_tool_use` | The agent had the right tools but used them badly. | Template steps / tool guidance |
| `ignoring_edge_cases` | The agent solved the happy path and missed stated edge cases. | Template steps (edge-case checklist) |
| `unclear_task_or_eval` | The task or its success criteria were too ambiguous to satisfy. | Task authoring / task evaluator thresholds |
| `unknown_failure` | The evidence is insufficient to classify. | Trace capture (collect more signal) |

## Usage rules

1. A failed attempt may carry several slugs; the classifier names one `primary_failure_mode`.
2. Every slug must cite evidence from the attempt's capsule (task detail, trace, outputs, test output, judgment). No evidence → `unknown_failure` plus a note naming the missing signal.
3. The catalog is the contract: adding a slug means editing this file first, then the classifier.
4. The ledger's deterministic `failure_mode` field (`none`, `partial_completion`, `missing_required_output`, `unverifiable_completion`, `judged_failure`) is a coarse execution class and stays untouched; this catalog powers the rich analysis stored in `.kamino/evals/tasks/failures/`.
