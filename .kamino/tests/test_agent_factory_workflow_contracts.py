"""Contract tests for the Agent Factory workflow skills and agent docs."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def read_text(relative_path: str) -> str:
    """Read a repository file."""
    return (repo_root() / relative_path).read_text(encoding="utf-8")


def test_new_agent_and_skills_exist_with_required_contracts() -> None:
    """The planned agent and five wrapper skills should exist with strict boundaries."""
    expected_paths = [
        ".claude/agents/task-run-success-judge.md",
        ".claude/skills/task-evaluate/SKILL.md",
        ".claude/skills/rank-task-difficulty/SKILL.md",
        ".claude/skills/agent-candidate-search/SKILL.md",
        ".claude/skills/task-detail-record/SKILL.md",
        ".claude/skills/task-outcome-lookup/SKILL.md",
        ".claude/skills/run-success-evaluate/SKILL.md",
        ".claude/skills/task-outcome-record/SKILL.md",
    ]
    for relative_path in expected_paths:
        assert (repo_root() / relative_path).is_file(), relative_path

    judge_text = read_text(".claude/agents/task-run-success-judge.md")
    assert "strict JSON only" in judge_text
    assert "Partial completion is failure" in judge_text
    assert "Do not run shell commands" in judge_text
    assert "Do not edit files" in judge_text
    for field in [
        "success",
        "reason",
        "satisfied_requirements",
        "missing_requirements",
        "partial_requirements",
        "unverifiable_requirements",
        "confidence",
    ]:
        assert field in judge_text

    lookup_text = read_text(".claude/skills/task-outcome-lookup/SKILL.md")
    assert ".kamino/evals/scripts/task_outcome_ledger_read.py" in lookup_text
    assert "Do not invoke any LLM agent" in lookup_text
    assert "Do not write files" in lookup_text

    candidate_text = read_text(".claude/skills/agent-candidate-search/SKILL.md")
    assert ".kamino/evals/scripts/agent_candidate_search.py" in candidate_text
    assert "Do not invoke any LLM agent" in candidate_text
    assert "Do not write the ledger" in candidate_text
    assert "--limit \"10\"" in candidate_text

    detail_text = read_text(".claude/skills/task-detail-record/SKILL.md")
    assert ".kamino/evals/scripts/task_detail_write.py" in detail_text
    assert "Do not write the task outcome ledger" in detail_text
    assert "Refuse to overwrite" in detail_text

    record_text = read_text(".claude/skills/task-outcome-record/SKILL.md")
    assert ".kamino/evals/scripts/task_outcome_ledger_write.py" in record_text
    assert "--task-detail" in record_text
    assert "Refuse to write unless `success` is present and boolean" in record_text
    assert "Partial" in record_text or "partial" in record_text


def test_just_test_entrypoints_initialize_the_managed_environment() -> None:
    """User-facing validation should go through just and share one init contract."""
    justfile = read_text("justfile")

    assert "init:" in justfile
    assert "requirements-dev.txt" in justfile
    assert "run: init check" in justfile
    assert "boundary: init" in justfile
    assert "test *args: init" in justfile
    assert ".venv/bin/pytest {{args}}" in justfile


def test_factory_compile_flow_uses_evaluation_ranking_candidate_search_before_routing() -> None:
    """Factory should run the new compile evidence steps before route selection."""
    factory_text = read_text(".claude/skills/factory/SKILL.md")
    evaluation_index = factory_text.index("Call `task-evaluate`")
    ranking_index = factory_text.index("Call `rank-task-difficulty`")
    candidate_index = factory_text.index("Call `agent-candidate-search`")
    index_read_index = factory_text.index("Read `.kamino/agents/index.md`")
    route_index = factory_text.index("Choose the route")
    detail_index = factory_text.index("call `task-detail-record`")

    assert evaluation_index < ranking_index < candidate_index < index_read_index < route_index < detail_index
    assert "Call `task-outcome-lookup`" not in factory_text
    assert "inspect shortlisted candidate blueprint files" in factory_text
    assert "fall back to the normal `.kamino/agents/index.md` search" in factory_text
    assert "contradicts that agent's baked goal" in factory_text
    assert "Compile phase must never write" in factory_text
    assert "task-detail-record" in factory_text
    assert "Factory **assembles only** by default" in factory_text
    assert "Treat prior partial completion as failure" in factory_text


def test_run_flow_records_only_after_binary_success_judgment() -> None:
    """Run should separate execution success from task success and gate ledger writes."""
    run_text = read_text(".claude/skills/run/SKILL.md")
    success_index = run_text.index("call `run-success-evaluate`")
    record_index = run_text.index("call `task-outcome-record`")

    assert success_index < record_index
    assert "Execution success is not task success" in run_text
    assert "valid binary success judgment" in run_text
    assert "task detail JSON" in run_text
    assert "Task success: true / false / not judged" in run_text


def test_run_flow_traces_every_step_and_supports_verification_commands() -> None:
    """Run should append one trace record per step attempt and honor verification commands."""
    run_text = read_text(".claude/skills/run/SKILL.md")

    assert ".kamino/evals/scripts/run_trace_write.py" in run_text
    assert "trace.jsonl" in run_text
    assert "kamino451.run-trace.v1" in run_text
    assert "verification command" in run_text
    assert "Exit code `0` passes" in run_text
    assert "A trace-writer failure fails the run" in run_text
    assert "`OK`, `SKIPPED`, or `FAILED`" in run_text


def test_model_binding_happens_at_instantiation_not_in_blueprints() -> None:
    """Blueprint model/effort are defaults; binding edits only the instantiated copy."""
    factory_text = read_text(".claude/skills/factory/SKILL.md")
    clone_text = read_text(".claude/skills/clone/SKILL.md")
    taskgraph_text = read_text(".claude/skills/taskgraph/SKILL.md")

    assert "## Model binding" in factory_text
    assert "**defaults**, not bindings" in factory_text
    assert "Escalation policy (cheap-first)" in factory_text
    assert "escalate to `sonnet` only on a failed attempt" in factory_text
    assert "silent substitution is forbidden" in factory_text
    assert "Never edit the original blueprint" in clone_text
    assert "never edit the blueprint" in taskgraph_text
    assert "Model / Effort" in taskgraph_text
    assert "Verification" in taskgraph_text


def test_run_success_evaluate_prefers_deterministic_ground_truth() -> None:
    """Ground-truth test results should be judged deterministically, not by the LLM judge."""
    text = read_text(".claude/skills/run-success-evaluate/SKILL.md")

    assert ".kamino/evals/scripts/success_judgment_from_tests.py" in text
    assert "tests_passed" in text
    assert "Ground truth beats opinion" in text
    assert "task-run-success-judge" in text
    assert "Never fabricate `tests_passed`" in text


def test_failure_analysis_uses_catalog_slugs_and_immutable_artifacts() -> None:
    """Failure analysis must classify with catalog slugs and never touch the ledger."""
    catalog_text = read_text(".kamino/evals/tasks/failure-mode-catalog.md")
    classifier_text = read_text(".claude/agents/run-failure-classifier.md")
    skill_text = read_text(".claude/skills/failure-analyze/SKILL.md")

    for slug in [
        "wrong_template",
        "wrong_model",
        "missing_context",
        "no_test_verification",
        "hallucinating_code",
        "unknown_failure",
    ]:
        assert f"`{slug}`" in catalog_text
    assert "Component to improve" in catalog_text
    assert "Never invent a slug" in classifier_text
    assert "Output strict JSON only" in classifier_text
    assert "primary_failure_mode" in classifier_text
    assert ".kamino/evals/tasks/failure-mode-catalog.md" in skill_text
    assert "Analyze failed attempts only" in skill_text
    assert "Refuse to overwrite" in skill_text
    assert "Do not write the ledger" in skill_text


def test_normal_factory_workflow_does_not_reference_autoresearch_runtime() -> None:
    """Normal task-completion skills must not invoke AutoResearch implementation surfaces."""
    relative_paths = [
        ".claude/skills/factory/SKILL.md",
        ".claude/skills/run/SKILL.md",
        ".claude/skills/task-evaluate/SKILL.md",
        ".claude/skills/rank-task-difficulty/SKILL.md",
        ".claude/skills/agent-candidate-search/SKILL.md",
        ".claude/skills/task-detail-record/SKILL.md",
        ".claude/skills/task-outcome-lookup/SKILL.md",
        ".claude/skills/run-success-evaluate/SKILL.md",
        ".claude/skills/task-outcome-record/SKILL.md",
    ]
    forbidden_fragments = [
        "auto_research.py",
        "autoresearch-agent-improver",
        "autoresearch-eval-author",
        "autoresearch-program-author",
        "autoresearch-llm-evaluator",
    ]
    for relative_path in relative_paths:
        text = read_text(relative_path)
        for forbidden_fragment in forbidden_fragments:
            assert forbidden_fragment not in text, f"{relative_path} references {forbidden_fragment}"


def test_eval_sweep_skills_exist_with_isolation_contracts() -> None:
    """The new eval-sweep skills should exist, share isolation contracts, and diverge on mode."""
    factory_text = read_text(".claude/skills/evaluate-factory/SKILL.md")
    agent_text = read_text(".claude/skills/evaluate-agent/SKILL.md")

    for text in (factory_text, agent_text):
        assert "compile_run.py" in text
        assert "record_run.py" in text
        assert "generate_reports.sh" in text
        assert "sweeps.html" in text
        assert "Never read or reveal `solution_reference.py`" in text
        assert "verify/" in text

    assert "--mode auto" in factory_text
    assert "route_recommendation.py" in factory_text
    assert "--mode prescribed" in agent_text
    assert "route_recommendation.py" not in agent_text

    assert not (repo_root() / ".claude" / "skills" / "flywheel").exists()


def test_create_eval_corpus_skill_contract() -> None:
    """The corpus ingestion skill should name its output paths, gates, and overwrite refusal."""
    text = read_text(".claude/skills/create-eval-corpus/SKILL.md")

    assert "name: create-eval-corpus" in text
    assert ".kamino/evals/ingest/<corpus-name>/" in text
    assert "The engine (`.kamino/evals/scripts/`) never learns this source." in text
    for gate_id in ["G1", "G2", "G3", "G4", "G5"]:
        assert gate_id in text
    assert "STOP and ask before overwriting" in text


def test_sweep_skills_do_not_reference_autoresearch() -> None:
    """The eval-sweep skills must not invoke AutoResearch implementation surfaces."""
    relative_paths = [
        ".claude/skills/evaluate-factory/SKILL.md",
        ".claude/skills/evaluate-agent/SKILL.md",
        ".claude/skills/create-eval-corpus/SKILL.md",
    ]
    forbidden_fragments = [
        "auto_research.py",
        "autoresearch-agent-improver",
        "autoresearch-eval-author",
        "autoresearch-program-author",
        "autoresearch-llm-evaluator",
    ]
    for relative_path in relative_paths:
        text = read_text(relative_path)
        for forbidden_fragment in forbidden_fragments:
            assert forbidden_fragment not in text, f"{relative_path} references {forbidden_fragment}"


def test_improve_agent_skill_is_the_reachable_autoresearch_door() -> None:
    """AutoResearch is reachable only through improve-agent, wired into the learning loop."""
    text = read_text(".claude/skills/improve-agent/SKILL.md")
    assert "auto_research.py" in text
    assert "--workspace" in text
    assert "autoresearch-agent-improver" in text
    assert "autoresearch-eval-author" in text
    assert "autoresearch-program-author" in text
    assert "Never write the task-outcome ledger" in text
    assert "simulate" in text
    assert ".kamino/tests/fixtures/auto-research/" in text

    factory_text = read_text(".claude/skills/factory/SKILL.md")
    assert "`improve-agent`" in factory_text

    analyze_text = read_text(".claude/skills/failure-analyze/SKILL.md")
    assert "improve-agent" in analyze_text

    improver_text = read_text(".claude/agents/autoresearch-agent-improver.md")
    assert "<workspace>" in improver_text
    assert ".kamino/evals/auto-research" not in improver_text
