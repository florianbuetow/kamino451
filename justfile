# Kamino451 Agent Factory

_default:
    @just help

help:
    @echo ""
    @printf "\033[0;34m=== kamino451 agent factory ===\033[0m\n"
    @echo ""
    @printf "\033[0;33mRun:\033[0m\n"
    @printf "  %-24s %s\n" "run" "Run the Agent Factory workflow smoke validation"
    @echo ""
    @printf "\033[0;33mCode quality:\033[0m\n"
    @printf "  %-24s %s\n" "check" "Validate Kamino agent template contracts"
    @printf "  %-24s %s\n" "boundary" "Check factory boundary with semgrep (no docs/ references)"
    @echo ""
    @printf "\033[0;33mTesting:\033[0m\n"
    @printf "  %-24s %s\n" "test" "Run the pytest suite"
    @printf "  %-24s %s\n" "ci" "Run check and test"
    @echo ""
    @printf "\033[0;32m✓ Help displayed\033[0m\n"
    @echo ""

run:
    @echo ""
    @printf "\033[0;34m=== Running Agent Factory Workflow Validation ===\033[0m\n"
    just check
    uv run pytest .kamino/tests/test_task_evaluator_script.py .kamino/tests/test_bradley_terry_pairwise_ranking_script.py .kamino/tests/test_task_outcome_ledger_scripts.py .kamino/tests/test_agent_candidate_search_script.py .kamino/tests/test_task_detail_script.py .kamino/tests/test_agent_factory_workflow_contracts.py .kamino/tests/test_run_trace_script.py .kamino/tests/test_success_judgment_script.py .kamino/tests/test_difficulty_calibration_script.py .kamino/tests/test_error_analysis_ui_script.py .kamino/tests/test_difficulty_report_ui_script.py .kamino/tests/test_run_swe_agent_real_mode.py .kamino/tests/test_corpus_integrity_any.py .kamino/tests/test_compile_run_script.py .kamino/tests/test_record_run_script.py .kamino/tests/test_build_sweep_report_ui_script.py .kamino/tests/test_route_recommendation_script.py .kamino/tests/test_prune_dispatch_queue_script.py .kamino/tests/test_token_costs_script.py
    @printf "\033[0;32m✓ Agent Factory workflow validation passed\033[0m\n"
    @echo ""

check:
    @echo ""
    @printf "\033[0;34m=== Checking Kamino Agent Templates ===\033[0m\n"
    .kamino/scripts/template-variable-checks.sh .kamino/agents/
    @printf "\033[0;32m✓ Kamino agent templates valid\033[0m\n"
    @echo ""

boundary:
    @echo ""
    @printf "\033[0;34m=== Checking Factory Boundary (Semgrep) ===\033[0m\n"
    semgrep --config config/semgrep/ --error --no-git-ignore --scan-unknown-extensions .claude .kamino
    @printf "\033[0;32m✓ Factory boundary clean: no docs/ references\033[0m\n"
    @echo ""

test:
    @echo ""
    @printf "\033[0;34m=== Running Test Suite ===\033[0m\n"
    uv run pytest
    @printf "\033[0;32m✓ Test suite passed\033[0m\n"
    @echo ""

ci:
    @echo ""
    @printf "\033[0;34m=== Running CI Checks ===\033[0m\n"
    just check
    just boundary
    just test
    @printf "\033[0;32m✓ CI checks passed\033[0m\n"
    @echo ""
