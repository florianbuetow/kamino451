#!/usr/bin/env python3
"""Evaluate agent-factory task text with deterministic routing signals."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskSource:
    """Describe where the evaluated task text came from."""

    kind: str
    path: str | None


@dataclass(frozen=True)
class TaskMetrics:
    """Objective and structural metrics for a task description."""

    character_count: int
    word_count: int
    sentence_count: int
    estimated_token_count: int
    syllable_count: int
    average_sentence_words: float
    flesch_reading_ease: float
    flesch_kincaid_grade: float
    bullet_count: int
    explicit_requirement_count: int
    constraint_indicator_count: int
    success_criteria_indicator_count: int
    input_output_indicator_count: int
    vague_term_count: int
    contradiction_indicator_count: int
    tool_indicator_count: int
    domain_indicator_count: int


@dataclass(frozen=True)
class TaskJudgement:
    """Heuristic judgement scores derived from the task metrics."""

    clarity_score: int
    ambiguity_score: int
    consistency_score: int
    completeness_score: int
    difficulty_score: int
    task_type: str
    recommended_mapping: str
    human_review_required: bool
    reasons: list[str]


@dataclass(frozen=True)
class TaskEvaluation:
    """Full task evaluation report."""

    schema_version: str
    task_id: str
    task_text_hash: str
    task_text: str
    source: TaskSource
    metrics: TaskMetrics
    judgement: TaskJudgement
    open_issues: list[str]


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Evaluate a task description for agent-factory routing.",
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--task", help="Task text to evaluate.")
    input_group.add_argument("--file", help="Path to a UTF-8 text file containing the task.")
    parser.add_argument("--format", choices=["json", "markdown"], required=True, help="Output format.")
    return parser.parse_args(argv)


def load_task_text(args: argparse.Namespace) -> tuple[TaskSource, str]:
    """Load task text from the validated CLI arguments."""
    task_arg = args.task
    file_arg = args.file

    if task_arg is not None and file_arg is not None:
        raise ValueError("provide exactly one of --task or --file")

    if task_arg is not None:
        if not isinstance(task_arg, str):
            raise TypeError("--task must be a string")
        if task_arg.strip() == "":
            raise ValueError("--task must not be empty")
        return TaskSource(kind="inline", path=None), task_arg

    if file_arg is not None:
        if not isinstance(file_arg, str):
            raise TypeError("--file must be a string path")
        task_path = Path(file_arg)
        if not task_path.is_file():
            raise FileNotFoundError(f"task file does not exist: {task_path}")
        task_text = task_path.read_text(encoding="utf-8")
        if task_text.strip() == "":
            raise ValueError(f"task file is empty: {task_path}")
        return TaskSource(kind="file", path=str(task_path)), task_text

    raise ValueError("provide exactly one of --task or --file")


def split_words(text: str) -> list[str]:
    """Split text into word-like tokens for deterministic metrics."""
    return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text)


def split_sentences(text: str) -> list[str]:
    """Split text into sentence-like chunks."""
    sentences = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip() != ""]
    if len(sentences) > 0:
        return sentences

    words = split_words(text)
    if len(words) > 0:
        return [text.strip()]

    return []


def count_syllables(word: str) -> int:
    """Estimate syllables in one English-like word."""
    clean_word = re.sub(r"[^a-z]", "", word.lower())
    if clean_word == "":
        return 0

    groups = re.findall(r"[aeiouy]+", clean_word)
    syllable_count = len(groups)
    if clean_word.endswith("e") and syllable_count > 1:
        syllable_count -= 1
    if syllable_count < 1:
        return 1
    return syllable_count


def count_keyword_hits(text: str, keywords: list[str]) -> int:
    """Count case-insensitive keyword and phrase hits."""
    lower_text = text.lower()
    hit_count = 0
    for keyword in keywords:
        escaped_keyword = re.escape(keyword.lower())
        pattern = escaped_keyword
        if " " not in keyword:
            pattern = rf"\b{escaped_keyword}\b"
        hit_count += len(re.findall(pattern, lower_text))
    return hit_count


def count_bullets(text: str) -> int:
    """Count Markdown-like bullet and numbered-list lines."""
    return len(re.findall(r"(?m)^\s*(?:[-*]|\d+[.)])\s+\S", text))


def estimate_tokens(character_count: int) -> int:
    """Estimate token count with a deterministic character-ratio heuristic."""
    if character_count <= 0:
        raise ValueError("character_count must be positive")

    estimated = round(character_count / 4)
    if estimated < 1:
        return 1
    return estimated


def evaluate_metrics(task_text: str) -> TaskMetrics:
    """Compute objective metrics and structural indicators."""
    words = split_words(task_text)
    sentences = split_sentences(task_text)
    if len(words) == 0:
        raise ValueError("task text must contain at least one word")
    if len(sentences) == 0:
        raise ValueError("task text must contain at least one sentence-like unit")

    word_count = len(words)
    sentence_count = len(sentences)
    syllable_count = sum(count_syllables(word) for word in words)
    average_sentence_words = word_count / sentence_count
    syllables_per_word = syllable_count / word_count
    flesch_reading_ease = 206.835 - (1.015 * average_sentence_words) - (84.6 * syllables_per_word)
    flesch_kincaid_grade = (0.39 * average_sentence_words) + (11.8 * syllables_per_word) - 15.59

    explicit_requirement_keywords = [
        "must",
        "should",
        "need",
        "needs",
        "required",
        "requirement",
        "acceptance criteria",
        "accept",
        "accepts",
        "evaluate",
        "evaluates",
        "emit",
        "emits",
        "pass",
        "passes",
        "include",
        "includes",
        "return",
        "produce",
    ]
    constraint_keywords = ["must not", "do not", "never", "only", "without", "constraint", "non-goal", "forbidden", "avoid"]
    success_keywords = ["success", "criteria", "done when", "acceptance", "verify", "test", "pass", "validated"]
    input_output_keywords = ["input", "output", "format", "json", "file", "return", "produce", "emit", "write"]
    vague_keywords = [
        "good",
        "appropriate",
        "somehow",
        "nice",
        "stuff",
        "things",
        "better",
        "soon",
        "fast",
        "simple",
        "optimize",
        "improve",
        "probably",
        "works",
        "passes tests",
    ]
    contradiction_keywords = ["conflicting", "contradictory", "contradiction", "inconsistent", "but also not", "while also not"]
    tool_keywords = ["run", "execute", "script", "cli", "api", "tool", "browser", "github", "database", "uv", "docker", "shell"]
    domain_keywords = [
        "code",
        "python",
        "security",
        "legal",
        "medical",
        "financial",
        "research",
        "agent",
        "llm",
        "claude",
        "codex",
        "docker",
    ]

    bullet_count = count_bullets(task_text)
    explicit_requirement_count = count_keyword_hits(task_text, explicit_requirement_keywords) + bullet_count
    contradiction_indicator_count = count_keyword_hits(task_text, contradiction_keywords) + count_contradiction_patterns(task_text)

    return TaskMetrics(
        character_count=len(task_text),
        word_count=word_count,
        sentence_count=sentence_count,
        estimated_token_count=estimate_tokens(len(task_text)),
        syllable_count=syllable_count,
        average_sentence_words=round(average_sentence_words, 2),
        flesch_reading_ease=round(flesch_reading_ease, 2),
        flesch_kincaid_grade=round(flesch_kincaid_grade, 2),
        bullet_count=bullet_count,
        explicit_requirement_count=explicit_requirement_count,
        constraint_indicator_count=count_keyword_hits(task_text, constraint_keywords),
        success_criteria_indicator_count=count_keyword_hits(task_text, success_keywords),
        input_output_indicator_count=count_keyword_hits(task_text, input_output_keywords),
        vague_term_count=count_keyword_hits(task_text, vague_keywords),
        contradiction_indicator_count=contradiction_indicator_count,
        tool_indicator_count=count_keyword_hits(task_text, tool_keywords),
        domain_indicator_count=count_keyword_hits(task_text, domain_keywords),
    )


def count_contradiction_patterns(text: str) -> int:
    """Count simple linguistic patterns that often indicate conflicting requirements."""
    lower_text = text.lower()
    patterns = [
        r"\bdo\b.{0,80}\band\b.{0,80}\bdo not\b",
        r"\bmust\b.{0,80}\band\b.{0,80}\bmust not\b",
        r"\brequire\b.{0,80}\band\b.{0,80}\bforbid\b",
    ]
    pattern_count = 0
    for pattern in patterns:
        pattern_count += len(re.findall(pattern, lower_text))
    return pattern_count


def clamp_score(value: int) -> int:
    """Clamp a score to the 1 through 5 rubric range."""
    if value < 1:
        return 1
    if value > 5:
        return 5
    return value


def score_clarity(metrics: TaskMetrics) -> int:
    """Score how clearly the task describes goals, constraints, and output."""
    score = 3
    if metrics.input_output_indicator_count >= 2:
        score += 1
    if metrics.explicit_requirement_count >= 2:
        score += 1
    if metrics.bullet_count > 0:
        score += 1
    if metrics.vague_term_count >= 2:
        score -= 1
    if metrics.average_sentence_words > 28:
        score -= 1
    if metrics.flesch_reading_ease < 30:
        score -= 1
    if metrics.flesch_kincaid_grade > 14:
        score -= 1
    if metrics.word_count < 8:
        score -= 1
    return clamp_score(score)


def score_ambiguity(metrics: TaskMetrics, clarity_score: int) -> int:
    """Score how likely the task is to have multiple reasonable interpretations."""
    score = 1
    if metrics.vague_term_count > 0:
        score += 1
    if metrics.vague_term_count >= 3:
        score += 1
    if metrics.input_output_indicator_count == 0:
        score += 1
    if clarity_score <= 2:
        score += 1
    if metrics.contradiction_indicator_count > 0:
        score += 1
    return clamp_score(score)


def score_consistency(metrics: TaskMetrics) -> int:
    """Score whether the task appears internally consistent."""
    score = 5 - (metrics.contradiction_indicator_count * 2)
    return clamp_score(score)


def score_completeness(metrics: TaskMetrics) -> int:
    """Score whether the task includes enough routing and execution context."""
    score = 1
    if metrics.explicit_requirement_count > 0:
        score += 1
    if metrics.input_output_indicator_count > 0:
        score += 1
    if metrics.success_criteria_indicator_count > 0:
        score += 1
    if metrics.constraint_indicator_count > 0:
        score += 1
    if metrics.word_count >= 25 and score < 5:
        score += 1
    return clamp_score(score)


def score_difficulty(metrics: TaskMetrics, ambiguity_score: int, task_type: str) -> int:
    """Score likely execution difficulty for routing."""
    score = 1
    if metrics.word_count >= 40:
        score += 1
    if metrics.word_count >= 120:
        score += 1
    if metrics.tool_indicator_count >= 2:
        score += 1
    if metrics.domain_indicator_count >= 2:
        score += 1
    if metrics.explicit_requirement_count >= 5:
        score += 1
    if metrics.average_sentence_words > 30:
        score += 1
    if ambiguity_score >= 4:
        score += 1
    if task_type in {"code_generation", "multi_step_planning", "tool_workflow"}:
        score += 1
    return clamp_score(score)


def classify_task_type(task_text: str) -> str:
    """Classify the task into a coarse routing category."""
    category_keywords: dict[str, list[str]] = {
        "code_generation": ["code", "implement", "python", "bug", "test", "refactor", "script", "cli"],
        "research": ["research", "source", "sources", "citation", "cite", "lookup", "compare", "evidence", "apis"],
        "multi_step_planning": ["plan", "design", "architecture", "roadmap", "workflow", "strategy"],
        "writing": ["write", "draft", "summarize", "edit", "copy", "document"],
        "data_extraction": ["extract", "parse", "classify", "table", "csv", "json"],
        "tool_workflow": ["run", "execute", "github", "browser", "api", "database", "docker", "uv"],
        "factual_qa": ["what", "who", "when", "where", "why", "how many"],
    }

    best_category = "general_task"
    best_score = 0
    for category, keywords in category_keywords.items():
        category_score = count_keyword_hits(task_text, keywords)
        if category_score > best_score:
            best_category = category
            best_score = category_score

    return best_category


def choose_mapping(
    metrics: TaskMetrics,
    clarity_score: int,
    ambiguity_score: int,
    consistency_score: int,
    completeness_score: int,
    difficulty_score: int,
) -> str:
    """Choose a routing recommendation from the evaluation scores."""
    if consistency_score <= 2 or ambiguity_score >= 4:
        return "clarification_agent_or_human_review"
    if completeness_score <= 2:
        return "clarification_agent"
    if difficulty_score >= 4 or metrics.tool_indicator_count >= 3:
        return "strong_model_planning_tool_agent"
    if difficulty_score <= 2 and clarity_score >= 4 and consistency_score >= 4:
        return "small_fast_model_simple_agent"
    return "standard_model_task_agent"


def build_reasons(metrics: TaskMetrics, judgement: TaskJudgement) -> list[str]:
    """Build short evidence statements for the final judgement."""
    reasons: list[str] = []
    reasons.append(f"{metrics.word_count} words, estimated {metrics.estimated_token_count} tokens")
    reasons.append(f"readability Flesch={metrics.flesch_reading_ease}, grade={metrics.flesch_kincaid_grade}")
    if metrics.explicit_requirement_count > 0:
        reasons.append(f"{metrics.explicit_requirement_count} explicit requirement signals")
    if metrics.input_output_indicator_count > 0:
        reasons.append(f"{metrics.input_output_indicator_count} input/output or format signals")
    if metrics.vague_term_count > 0:
        reasons.append(f"{metrics.vague_term_count} vague term signals")
    if metrics.contradiction_indicator_count > 0:
        reasons.append(f"{metrics.contradiction_indicator_count} contradiction signals")
    reasons.append(f"task type classified as {judgement.task_type}")
    reasons.append(f"routing recommendation is {judgement.recommended_mapping}")
    return reasons


def task_text_hash(task_text: str) -> str:
    """Return a stable SHA-256 hash for task text."""
    digest = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def task_id_from_hash(hash_value: str) -> str:
    """Derive a stable task id from a SHA-256 task hash."""
    prefix = "sha256:"
    if not hash_value.startswith(prefix):
        raise ValueError("task hash must start with sha256:")
    digest = hash_value[len(prefix) :]
    if len(digest) < 12:
        raise ValueError("task hash digest is too short")
    return f"task-{digest[:12]}"


def build_open_issues(metrics: TaskMetrics, judgement: TaskJudgement) -> list[str]:
    """Build deterministic routing issues from objective metrics."""
    issues: list[str] = []
    if judgement.consistency_score <= 2:
        issues.append("possible contradictory requirements")
    if judgement.ambiguity_score >= 4:
        issues.append("high ambiguity")
    if judgement.completeness_score <= 2:
        issues.append("missing routing or execution context")
    if metrics.input_output_indicator_count == 0:
        issues.append("missing explicit input or output format")
    if metrics.success_criteria_indicator_count == 0:
        issues.append("missing explicit success criteria")
    if metrics.vague_term_count >= 2:
        issues.append("vague task wording")
    return issues


def evaluate_task(task_text: str, source: TaskSource) -> TaskEvaluation:
    """Evaluate task text and return a structured report."""
    if task_text.strip() == "":
        raise ValueError("task text must not be empty")

    metrics = evaluate_metrics(task_text)
    task_type = classify_task_type(task_text)
    clarity = score_clarity(metrics)
    ambiguity = score_ambiguity(metrics, clarity)
    consistency = score_consistency(metrics)
    completeness = score_completeness(metrics)
    difficulty = score_difficulty(metrics, ambiguity, task_type)
    mapping = choose_mapping(metrics, clarity, ambiguity, consistency, completeness, difficulty)
    human_review_required = mapping == "clarification_agent_or_human_review"

    provisional_judgement = TaskJudgement(
        clarity_score=clarity,
        ambiguity_score=ambiguity,
        consistency_score=consistency,
        completeness_score=completeness,
        difficulty_score=difficulty,
        task_type=task_type,
        recommended_mapping=mapping,
        human_review_required=human_review_required,
        reasons=[],
    )
    reasons = build_reasons(metrics, provisional_judgement)
    judgement = TaskJudgement(
        clarity_score=clarity,
        ambiguity_score=ambiguity,
        consistency_score=consistency,
        completeness_score=completeness,
        difficulty_score=difficulty,
        task_type=task_type,
        recommended_mapping=mapping,
        human_review_required=human_review_required,
        reasons=reasons,
    )
    hash_value = task_text_hash(task_text)

    return TaskEvaluation(
        schema_version="kamino451.task-evaluation.v1",
        task_id=task_id_from_hash(hash_value),
        task_text_hash=hash_value,
        task_text=task_text,
        source=source,
        metrics=metrics,
        judgement=judgement,
        open_issues=build_open_issues(metrics, judgement),
    )


def score_scale() -> dict[str, str]:
    """Describe each 1 through 5 score direction."""
    return {
        "clarity_score": "1 = unclear, 5 = clear",
        "ambiguity_score": "1 = low ambiguity, 5 = high ambiguity",
        "consistency_score": "1 = contradictory, 5 = internally consistent",
        "completeness_score": "1 = missing key context, 5 = complete enough to route",
        "difficulty_score": "1 = easy, 5 = hard",
    }


def evaluation_to_dict(evaluation: TaskEvaluation) -> dict[str, object]:
    """Convert the evaluation dataclasses into a JSON-serializable dictionary."""
    return {
        "schema_version": evaluation.schema_version,
        "task_id": evaluation.task_id,
        "task_text_hash": evaluation.task_text_hash,
        "task_text": evaluation.task_text,
        "task_type": evaluation.judgement.task_type,
        "clarity_score": evaluation.judgement.clarity_score,
        "ambiguity_score": evaluation.judgement.ambiguity_score,
        "consistency_score": evaluation.judgement.consistency_score,
        "completeness_score": evaluation.judgement.completeness_score,
        "difficulty_score": evaluation.judgement.difficulty_score,
        "recommended_mapping": evaluation.judgement.recommended_mapping,
        "open_issues": evaluation.open_issues,
        "score_scale": score_scale(),
        "source": {
            "kind": evaluation.source.kind,
            "path": evaluation.source.path,
        },
        "metrics": {
            "character_count": evaluation.metrics.character_count,
            "word_count": evaluation.metrics.word_count,
            "sentence_count": evaluation.metrics.sentence_count,
            "estimated_token_count": evaluation.metrics.estimated_token_count,
            "syllable_count": evaluation.metrics.syllable_count,
            "average_sentence_words": evaluation.metrics.average_sentence_words,
            "flesch_reading_ease": evaluation.metrics.flesch_reading_ease,
            "flesch_kincaid_grade": evaluation.metrics.flesch_kincaid_grade,
            "bullet_count": evaluation.metrics.bullet_count,
            "explicit_requirement_count": evaluation.metrics.explicit_requirement_count,
            "constraint_indicator_count": evaluation.metrics.constraint_indicator_count,
            "success_criteria_indicator_count": evaluation.metrics.success_criteria_indicator_count,
            "input_output_indicator_count": evaluation.metrics.input_output_indicator_count,
            "vague_term_count": evaluation.metrics.vague_term_count,
            "contradiction_indicator_count": evaluation.metrics.contradiction_indicator_count,
            "tool_indicator_count": evaluation.metrics.tool_indicator_count,
            "domain_indicator_count": evaluation.metrics.domain_indicator_count,
        },
        "judgement": {
            "clarity_score": evaluation.judgement.clarity_score,
            "ambiguity_score": evaluation.judgement.ambiguity_score,
            "consistency_score": evaluation.judgement.consistency_score,
            "completeness_score": evaluation.judgement.completeness_score,
            "difficulty_score": evaluation.judgement.difficulty_score,
            "task_type": evaluation.judgement.task_type,
            "recommended_mapping": evaluation.judgement.recommended_mapping,
            "human_review_required": evaluation.judgement.human_review_required,
            "reasons": evaluation.judgement.reasons,
        },
    }


def format_json(evaluation: TaskEvaluation) -> str:
    """Render the evaluation as stable JSON."""
    return json.dumps(evaluation_to_dict(evaluation), indent=2, sort_keys=True)


def format_markdown(evaluation: TaskEvaluation) -> str:
    """Render the evaluation as a compact Markdown report."""
    lines = [
        "# Task Evaluation",
        "",
        f"- Task type: `{evaluation.judgement.task_type}`",
        f"- Recommended mapping: `{evaluation.judgement.recommended_mapping}`",
        f"- Human review required: `{str(evaluation.judgement.human_review_required).lower()}`",
        "",
        "## Scores",
        "",
        f"- Clarity: {evaluation.judgement.clarity_score}/5",
        f"- Ambiguity: {evaluation.judgement.ambiguity_score}/5",
        f"- Consistency: {evaluation.judgement.consistency_score}/5",
        f"- Completeness: {evaluation.judgement.completeness_score}/5",
        f"- Difficulty: {evaluation.judgement.difficulty_score}/5",
        "",
        "## Score Scale",
        "",
        "- Clarity: 1 = unclear, 5 = clear",
        "- Ambiguity: 1 = low ambiguity, 5 = high ambiguity",
        "- Consistency: 1 = contradictory, 5 = internally consistent",
        "- Completeness: 1 = missing key context, 5 = complete enough to route",
        "- Difficulty: 1 = easy, 5 = hard",
        "",
        "## Objective Metrics",
        "",
        f"- Words: {evaluation.metrics.word_count}",
        f"- Estimated tokens: {evaluation.metrics.estimated_token_count}",
        f"- Flesch reading ease: {evaluation.metrics.flesch_reading_ease}",
        f"- Flesch-Kincaid grade: {evaluation.metrics.flesch_kincaid_grade}",
        f"- Explicit requirement signals: {evaluation.metrics.explicit_requirement_count}",
        f"- Vague term signals: {evaluation.metrics.vague_term_count}",
        f"- Contradiction signals: {evaluation.metrics.contradiction_indicator_count}",
        "",
        "## Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in evaluation.judgement.reasons)
    return "\n".join(lines)


def render_evaluation(evaluation: TaskEvaluation, output_format: str) -> str:
    """Render an evaluation in the requested output format."""
    if output_format == "json":
        return format_json(evaluation)
    if output_format == "markdown":
        return format_markdown(evaluation)
    raise ValueError(f"unsupported output format: {output_format}")


def main(argv: list[str]) -> int:
    """Run the task evaluator CLI."""
    try:
        args = parse_args(argv)
        output_format = args.format
        if not isinstance(output_format, str):
            raise TypeError("--format must be a string")

        source, task_text = load_task_text(args)
        evaluation = evaluate_task(task_text, source)
        print(render_evaluation(evaluation, output_format))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
