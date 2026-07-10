#!/usr/bin/env python3
"""Rank task difficulty with Bradley-Terry pairwise comparison data."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "kamino451.bradley-terry-pairwise-ranking.v1"
TIE_VALUE = "Tie"
PRIOR_STRENGTH = 0.5
MAX_ITERATIONS = 500
CONVERGENCE_TOLERANCE = 0.0000000001


@dataclass(frozen=True)
class Task:
    """A task that can be ranked by relative difficulty."""

    task_id: str
    text: str


@dataclass(frozen=True)
class Comparison:
    """A pairwise difficulty judgement."""

    task_a_id: str
    task_b_id: str
    harder_task_id: str
    confidence: float
    reasoning: str
    key_factors: list[str]


@dataclass(frozen=True)
class RankingEntry:
    """One ranked task with a fitted Bradley-Terry score."""

    rank: int
    task_id: str
    task_text: str
    difficulty_score: float
    difficulty_probability: float
    comparison_count: int


@dataclass(frozen=True)
class SearchStep:
    """One binary-search comparison used to place a target task."""

    anchor_task_id: str
    anchor_rank: int
    outcome: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Rank task difficulty with Bradley-Terry pairwise comparison data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rank_parser = subparsers.add_parser("rank", help="Fit a Bradley-Terry ranking from task and comparison JSON files.")
    rank_parser.add_argument("--tasks", required=True, help="Path to a JSON file containing {'tasks': [{'id', 'text'}]}.")
    rank_parser.add_argument("--comparisons", required=True, help="Path to a JSON file containing pairwise judge outcomes.")
    rank_parser.add_argument("--format", choices=["json", "markdown"], required=True, help="Output format.")

    similar_parser = subparsers.add_parser("similar", help="Place a target task against an existing ranking by binary search.")
    similar_parser.add_argument("--ranking", required=True, help="Path to a previous rank-mode JSON output.")
    similar_parser.add_argument("--target-task", required=True, help="Path to a JSON file containing {'task': {'id', 'text'}}.")
    similar_parser.add_argument("--comparisons", required=True, help="Path to target-vs-anchor pairwise judge outcomes.")
    similar_parser.add_argument("--neighbors", required=True, type=int, help="Number of nearby ranked tasks to return.")
    similar_parser.add_argument("--format", choices=["json", "markdown"], required=True, help="Output format.")

    return parser.parse_args(argv)


def load_json_file(raw_path: str) -> object:
    """Load JSON from a required file path."""
    path = Path(raw_path)
    if not path.is_file():
        raise FileNotFoundError(f"JSON file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    if text.strip() == "":
        raise ValueError(f"JSON file is empty: {path}")
    return json.loads(text)


def require_mapping(value: object, label: str) -> dict[str, object]:
    """Require a JSON object."""
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def require_list(value: object, label: str) -> list[object]:
    """Require a JSON array."""
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a JSON array")
    return value


def require_string(value: object, label: str) -> str:
    """Require a non-empty string."""
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if value.strip() == "":
        raise ValueError(f"{label} must not be empty")
    return value


def require_number(value: object, label: str) -> float:
    """Require a JSON number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{label} must be a number")
    return float(value)


def require_key(mapping: dict[str, object], key: str, label: str) -> object:
    """Require a key in a JSON object."""
    if key not in mapping:
        raise ValueError(f"{label} is missing required key: {key}")
    return mapping[key]


def parse_tasks(payload: object) -> list[Task]:
    """Parse task repository JSON."""
    mapping = require_mapping(payload, "tasks payload")
    raw_tasks = require_list(require_key(mapping, "tasks", "tasks payload"), "tasks")
    tasks: list[Task] = []
    seen_ids: set[str] = set()

    for index, raw_task in enumerate(raw_tasks):
        task_mapping = require_mapping(raw_task, f"tasks[{index}]")
        task_id = require_string(require_key(task_mapping, "id", f"tasks[{index}]"), f"tasks[{index}].id")
        text = require_string(require_key(task_mapping, "text", f"tasks[{index}]"), f"tasks[{index}].text")
        if task_id in seen_ids:
            raise ValueError(f"duplicate task id: {task_id}")
        seen_ids.add(task_id)
        tasks.append(Task(task_id=task_id, text=text))

    if len(tasks) < 2:
        raise ValueError("rank mode requires at least two tasks")
    return tasks


def parse_target_task(payload: object) -> Task:
    """Parse a target task JSON file."""
    mapping = require_mapping(payload, "target payload")
    raw_task = require_mapping(require_key(mapping, "task", "target payload"), "target payload.task")
    task_id = require_string(require_key(raw_task, "id", "target payload.task"), "target payload.task.id")
    text = require_string(require_key(raw_task, "text", "target payload.task"), "target payload.task.text")
    return Task(task_id=task_id, text=text)


def normalize_harder_task(raw_value: str, task_a_id: str, task_b_id: str) -> str:
    """Normalize judge output to a concrete task id or Tie."""
    if raw_value == "A" or raw_value == "a":
        return task_a_id
    if raw_value == "B" or raw_value == "b":
        return task_b_id
    if raw_value == TIE_VALUE or raw_value == "tie" or raw_value == "TIE":
        return TIE_VALUE
    if raw_value in (task_a_id, task_b_id):
        return raw_value
    raise ValueError(f"harder_task must be A, B, Tie, or one of the compared task ids: {task_a_id}, {task_b_id}")


def parse_key_factors(value: object, label: str) -> list[str]:
    """Parse judge key factors."""
    raw_items = require_list(value, label)
    factors: list[str] = []
    for index, raw_item in enumerate(raw_items):
        factors.append(require_string(raw_item, f"{label}[{index}]"))
    return factors


def parse_comparisons(payload: object, allowed_task_ids: set[str]) -> list[Comparison]:
    """Parse pairwise comparison JSON."""
    mapping = require_mapping(payload, "comparisons payload")
    raw_comparisons = require_list(require_key(mapping, "comparisons", "comparisons payload"), "comparisons")
    comparisons: list[Comparison] = []

    for index, raw_comparison in enumerate(raw_comparisons):
        comparison_mapping = require_mapping(raw_comparison, f"comparisons[{index}]")
        task_a_id = require_string(
            require_key(comparison_mapping, "task_a_id", f"comparisons[{index}]"),
            f"comparisons[{index}].task_a_id",
        )
        task_b_id = require_string(
            require_key(comparison_mapping, "task_b_id", f"comparisons[{index}]"),
            f"comparisons[{index}].task_b_id",
        )
        if task_a_id == task_b_id:
            raise ValueError(f"comparison must contain two different task ids: {task_a_id}")
        if task_a_id not in allowed_task_ids:
            raise ValueError(f"comparison references unknown task id: {task_a_id}")
        if task_b_id not in allowed_task_ids:
            raise ValueError(f"comparison references unknown task id: {task_b_id}")

        raw_harder_task = require_string(
            require_key(comparison_mapping, "harder_task", f"comparisons[{index}]"),
            f"comparisons[{index}].harder_task",
        )
        harder_task_id = normalize_harder_task(raw_harder_task, task_a_id, task_b_id)
        confidence = require_number(
            require_key(comparison_mapping, "confidence", f"comparisons[{index}]"),
            f"comparisons[{index}].confidence",
        )
        if confidence <= 0.0 or confidence > 1.0:
            raise ValueError(f"comparisons[{index}].confidence must be greater than 0.0 and less than or equal to 1.0")

        reasoning = require_string(require_key(comparison_mapping, "reasoning", f"comparisons[{index}]"), f"comparisons[{index}].reasoning")
        key_factors = parse_key_factors(
            require_key(comparison_mapping, "key_factors", f"comparisons[{index}]"),
            f"comparisons[{index}].key_factors",
        )
        comparisons.append(
            Comparison(
                task_a_id=task_a_id,
                task_b_id=task_b_id,
                harder_task_id=harder_task_id,
                confidence=confidence,
                reasoning=reasoning,
                key_factors=key_factors,
            )
        )

    return comparisons


def unordered_pair_key(task_a_id: str, task_b_id: str) -> tuple[str, str]:
    """Return a stable key for an unordered pair of task ids."""
    if task_a_id == task_b_id:
        raise ValueError(f"pair requires two different task ids: {task_a_id}")
    if task_a_id < task_b_id:
        return (task_a_id, task_b_id)
    return (task_b_id, task_a_id)


def build_win_matrix(tasks: list[Task], comparisons: list[Comparison]) -> tuple[list[list[float]], dict[str, int], set[tuple[str, str]]]:
    """Build weighted pairwise win data for Bradley-Terry fitting."""
    task_ids = [task.task_id for task in tasks]
    id_to_index = {task_id: index for index, task_id in enumerate(task_ids)}
    size = len(tasks)
    wins = [[0.0 for _ in range(size)] for _ in range(size)]
    comparison_counts = {task_id: 0 for task_id in task_ids}
    compared_pairs: set[tuple[str, str]] = set()

    for comparison in comparisons:
        task_a_index = id_to_index[comparison.task_a_id]
        task_b_index = id_to_index[comparison.task_b_id]
        compared_pairs.add(unordered_pair_key(comparison.task_a_id, comparison.task_b_id))
        comparison_counts[comparison.task_a_id] += 1
        comparison_counts[comparison.task_b_id] += 1
        if comparison.harder_task_id == TIE_VALUE:
            half_weight = comparison.confidence / 2.0
            wins[task_a_index][task_b_index] += half_weight
            wins[task_b_index][task_a_index] += half_weight
        elif comparison.harder_task_id == comparison.task_a_id:
            wins[task_a_index][task_b_index] += comparison.confidence
        elif comparison.harder_task_id == comparison.task_b_id:
            wins[task_b_index][task_a_index] += comparison.confidence
        else:
            raise ValueError(f"harder task id is not part of comparison: {comparison.harder_task_id}")

    return wins, comparison_counts, compared_pairs


def ensure_connected_comparison_graph(tasks: list[Task], compared_pairs: set[tuple[str, str]]) -> None:
    """Require enough comparisons to connect every task to the ranking graph."""
    task_ids = [task.task_id for task in tasks]
    adjacency = {task_id: set[str]() for task_id in task_ids}
    for task_a_id, task_b_id in compared_pairs:
        adjacency[task_a_id].add(task_b_id)
        adjacency[task_b_id].add(task_a_id)

    visited: set[str] = set()
    pending = [task_ids[0]]
    while len(pending) > 0:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(neighbor for neighbor in adjacency[current] if neighbor not in visited)

    missing = [task_id for task_id in task_ids if task_id not in visited]
    if len(missing) > 0:
        raise ValueError(f"comparison graph must be connected; disconnected task ids: {', '.join(missing)}")


def fit_bradley_terry(wins: list[list[float]]) -> tuple[list[float], int]:
    """Fit Bradley-Terry strengths with a weak symmetric prior."""
    size = len(wins)
    strengths = [1.0 for _ in range(size)]
    completed_iterations = 0

    for iteration in range(1, MAX_ITERATIONS + 1):
        next_strengths: list[float] = []
        for task_index in range(size):
            weighted_wins = sum(wins[task_index])
            denominator = 0.0
            for opponent_index in range(size):
                if opponent_index == task_index:
                    continue
                total_pair_weight = wins[task_index][opponent_index] + wins[opponent_index][task_index]
                if total_pair_weight > 0.0:
                    denominator += total_pair_weight / (strengths[task_index] + strengths[opponent_index])
            next_strengths.append((weighted_wins + PRIOR_STRENGTH) / (denominator + PRIOR_STRENGTH))

        mean_strength = sum(next_strengths) / size
        if mean_strength <= 0.0:
            raise ValueError("Bradley-Terry fit produced non-positive mean strength")
        normalized_strengths = [strength / mean_strength for strength in next_strengths]
        max_delta = max(abs(math.log(normalized_strengths[index] / strengths[index])) for index in range(size))
        strengths = normalized_strengths
        completed_iterations = iteration
        if max_delta < CONVERGENCE_TOLERANCE:
            break

    return strengths, completed_iterations


def build_ranking(tasks: list[Task], strengths: list[float], comparison_counts: dict[str, int]) -> list[RankingEntry]:
    """Build hardest-first ranking entries from fitted strengths."""
    total_strength = sum(strengths)
    if total_strength <= 0.0:
        raise ValueError("Bradley-Terry fit produced non-positive total strength")

    raw_entries: list[tuple[str, str, float, float, int]] = []
    log_scores = [math.log(strength) for strength in strengths]
    mean_log_score = sum(log_scores) / len(log_scores)
    for index, task in enumerate(tasks):
        centered_score = log_scores[index] - mean_log_score
        raw_entries.append(
            (
                task.task_id,
                task.text,
                centered_score,
                strengths[index] / total_strength,
                comparison_counts[task.task_id],
            )
        )

    raw_entries.sort(key=lambda entry: (-entry[2], entry[0]))
    ranking: list[RankingEntry] = []
    for index, entry in enumerate(raw_entries, start=1):
        ranking.append(
            RankingEntry(
                rank=index,
                task_id=entry[0],
                task_text=entry[1],
                difficulty_score=round(entry[2], 6),
                difficulty_probability=round(entry[3], 6),
                comparison_count=entry[4],
            )
        )
    return ranking


def ranking_entry_to_dict(entry: RankingEntry) -> dict[str, object]:
    """Convert a ranking entry to JSON data."""
    return {
        "rank": entry.rank,
        "task_id": entry.task_id,
        "task_text": entry.task_text,
        "difficulty_score": entry.difficulty_score,
        "difficulty_probability": entry.difficulty_probability,
        "comparison_count": entry.comparison_count,
    }


def run_rank(tasks: list[Task], comparisons: list[Comparison]) -> dict[str, object]:
    """Run Bradley-Terry ranking mode."""
    if len(comparisons) == 0:
        raise ValueError("rank mode requires at least one pairwise comparison")
    wins, comparison_counts, compared_pairs = build_win_matrix(tasks, comparisons)
    ensure_connected_comparison_graph(tasks, compared_pairs)
    strengths, iterations = fit_bradley_terry(wins)
    ranking = build_ranking(tasks, strengths, comparison_counts)
    possible_pair_count = (len(tasks) * (len(tasks) - 1)) // 2
    compared_pair_count = len(compared_pairs)
    if possible_pair_count <= 0:
        raise ValueError("rank mode requires at least two tasks")

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "rank",
        "task_count": len(tasks),
        "comparison_count": len(comparisons),
        "fit": {
            "algorithm": "Bradley-Terry",
            "iterations": iterations,
            "prior_strength": PRIOR_STRENGTH,
        },
        "coverage": {
            "compared_pair_count": compared_pair_count,
            "possible_pair_count": possible_pair_count,
            "comparison_coverage": round(compared_pair_count / possible_pair_count, 6),
        },
        "ranking": [ranking_entry_to_dict(entry) for entry in ranking],
    }


def parse_ranking_entry(raw_entry: object, index: int) -> RankingEntry:
    """Parse one ranking entry from a previous rank-mode output."""
    mapping = require_mapping(raw_entry, f"ranking[{index}]")
    rank_number = require_number(require_key(mapping, "rank", f"ranking[{index}]"), f"ranking[{index}].rank")
    rank = int(rank_number)
    if float(rank) != rank_number:
        raise ValueError(f"ranking[{index}].rank must be an integer")
    task_id = require_string(require_key(mapping, "task_id", f"ranking[{index}]"), f"ranking[{index}].task_id")
    task_text = require_string(require_key(mapping, "task_text", f"ranking[{index}]"), f"ranking[{index}].task_text")
    difficulty_score = require_number(
        require_key(mapping, "difficulty_score", f"ranking[{index}]"),
        f"ranking[{index}].difficulty_score",
    )

    parsed_count = require_number(require_key(mapping, "comparison_count", f"ranking[{index}]"), f"ranking[{index}].comparison_count")
    comparison_count = int(parsed_count)
    if float(comparison_count) != parsed_count:
        raise ValueError(f"ranking[{index}].comparison_count must be an integer")
    difficulty_probability = require_number(
        require_key(mapping, "difficulty_probability", f"ranking[{index}]"),
        f"ranking[{index}].difficulty_probability",
    )

    return RankingEntry(
        rank=rank,
        task_id=task_id,
        task_text=task_text,
        difficulty_score=round(difficulty_score, 6),
        difficulty_probability=round(difficulty_probability, 6),
        comparison_count=comparison_count,
    )


def parse_ranking(payload: object) -> list[RankingEntry]:
    """Parse a previous rank-mode JSON output."""
    mapping = require_mapping(payload, "ranking payload")
    schema_version = require_string(require_key(mapping, "schema_version", "ranking payload"), "ranking payload.schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"ranking payload schema_version must be {SCHEMA_VERSION}")
    mode = require_string(require_key(mapping, "mode", "ranking payload"), "ranking payload.mode")
    if mode != "rank":
        raise ValueError("ranking payload mode must be rank")
    raw_ranking = require_list(require_key(mapping, "ranking", "ranking payload"), "ranking")
    if len(raw_ranking) == 0:
        raise ValueError("ranking payload must contain at least one ranked task")

    entries = [parse_ranking_entry(raw_entry, index) for index, raw_entry in enumerate(raw_ranking)]
    entries.sort(key=lambda entry: entry.rank)
    seen_ids: set[str] = set()
    seen_ranks: set[int] = set()
    for entry in entries:
        if entry.rank < 1:
            raise ValueError(f"rank must be at least 1 for task id: {entry.task_id}")
        if entry.rank in seen_ranks:
            raise ValueError(f"duplicate rank in ranking payload: {entry.rank}")
        if entry.task_id in seen_ids:
            raise ValueError(f"duplicate task id in ranking payload: {entry.task_id}")
        seen_ranks.add(entry.rank)
        seen_ids.add(entry.task_id)
    return entries


def target_comparison_outcome(comparisons: list[Comparison], target_task_id: str, anchor_task_id: str) -> str | None:
    """Return target_harder, anchor_harder, tie, or None for one target-anchor pair."""
    target_weight = 0.0
    anchor_weight = 0.0
    observed = False
    for comparison in comparisons:
        pair_matches = unordered_pair_key(comparison.task_a_id, comparison.task_b_id) == unordered_pair_key(target_task_id, anchor_task_id)
        if not pair_matches:
            continue
        observed = True
        if comparison.harder_task_id == TIE_VALUE:
            half_weight = comparison.confidence / 2.0
            target_weight += half_weight
            anchor_weight += half_weight
        elif comparison.harder_task_id == target_task_id:
            target_weight += comparison.confidence
        elif comparison.harder_task_id == anchor_task_id:
            anchor_weight += comparison.confidence
        else:
            raise ValueError(f"comparison winner is not target or anchor: {comparison.harder_task_id}")

    if not observed:
        return None
    if abs(target_weight - anchor_weight) < CONVERGENCE_TOLERANCE:
        return "tie"
    if target_weight > anchor_weight:
        return "target_harder"
    return "anchor_harder"


def ranking_task_to_dict(entry: RankingEntry) -> dict[str, object]:
    """Render a ranked task for similar-mode output."""
    return {
        "task_id": entry.task_id,
        "rank": entry.rank,
        "task_text": entry.task_text,
        "difficulty_score": entry.difficulty_score,
    }


def needs_comparison_payload(target_task: Task, anchor: RankingEntry, low: int, high: int, path: list[SearchStep]) -> dict[str, object]:
    """Build a similar-mode response requesting one missing binary-search comparison."""
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "similar",
        "status": "needs_comparison",
        "next_pair": {
            "task_a": {
                "task_id": target_task.task_id,
                "task_text": target_task.text,
            },
            "task_b": ranking_task_to_dict(anchor),
            "judge": "pairwise-difficulty-judge",
            "expected_comparison_record": {
                "task_a_id": target_task.task_id,
                "task_b_id": anchor.task_id,
                "harder_task": "A, B, or Tie",
                "confidence": "number in (0.0, 1.0]",
                "reasoning": "short explanation",
                "key_factors": ["factor"],
            },
        },
        "binary_search_state": {
            "low_index": low,
            "high_index_exclusive": high,
            "comparisons_used": len(path),
        },
        "binary_search_path": [search_step_to_dict(step) for step in path],
    }


def search_step_to_dict(step: SearchStep) -> dict[str, object]:
    """Convert a binary-search step to JSON data."""
    return {
        "anchor_task_id": step.anchor_task_id,
        "anchor_rank": step.anchor_rank,
        "outcome": step.outcome,
    }


def estimate_target_score(entries: list[RankingEntry], insertion_index: int, tie_anchor_index: int) -> float:
    """Estimate the target difficulty score from the insertion point."""
    if tie_anchor_index >= 0:
        return entries[tie_anchor_index].difficulty_score
    if insertion_index == 0:
        return entries[0].difficulty_score + 0.001
    if insertion_index == len(entries):
        return entries[-1].difficulty_score - 0.001
    return round((entries[insertion_index - 1].difficulty_score + entries[insertion_index].difficulty_score) / 2.0, 6)


def nearest_neighbors(entries: list[RankingEntry], estimated_score: float, neighbor_count: int) -> list[dict[str, object]]:
    """Return ranked tasks nearest to an estimated target difficulty score."""
    scored_neighbors: list[tuple[float, int, RankingEntry]] = []
    for entry in entries:
        score_distance = abs(entry.difficulty_score - estimated_score)
        scored_neighbors.append((score_distance, entry.rank, entry))
    scored_neighbors.sort(key=lambda item: (item[0], item[1]))

    selected: list[dict[str, object]] = []
    for score_distance, _rank, entry in scored_neighbors[:neighbor_count]:
        selected.append(
            {
                "task_id": entry.task_id,
                "rank": entry.rank,
                "task_text": entry.task_text,
                "difficulty_score": entry.difficulty_score,
                "score_distance": round(score_distance, 6),
            }
        )
    return selected


def complete_similar_payload(
    entries: list[RankingEntry],
    target_task: Task,
    insertion_index: int,
    tie_anchor_index: int,
    neighbor_count: int,
    path: list[SearchStep],
) -> dict[str, object]:
    """Build a completed similar-mode response."""
    estimated_score = estimate_target_score(entries, insertion_index, tie_anchor_index)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "similar",
        "status": "complete",
        "target": {
            "task_id": target_task.task_id,
            "task_text": target_task.text,
        },
        "estimated_insertion_rank": insertion_index + 1,
        "estimated_difficulty_score": estimated_score,
        "similar_tasks": nearest_neighbors(entries, estimated_score, neighbor_count),
        "binary_search_path": [search_step_to_dict(step) for step in path],
    }


def run_similar(entries: list[RankingEntry], target_task: Task, comparisons: list[Comparison], neighbor_count: int) -> dict[str, object]:
    """Place a target task into an existing difficulty ranking by binary search."""
    if neighbor_count < 1:
        raise ValueError("--neighbors must be at least 1")
    if target_task.task_id in {entry.task_id for entry in entries}:
        raise ValueError(f"target task id already exists in ranking: {target_task.task_id}")

    low = 0
    high = len(entries)
    path: list[SearchStep] = []
    tie_anchor_index = -1

    while low < high:
        midpoint = (low + high) // 2
        anchor = entries[midpoint]
        outcome = target_comparison_outcome(comparisons, target_task.task_id, anchor.task_id)
        if outcome is None:
            return needs_comparison_payload(target_task, anchor, low, high, path)
        path.append(SearchStep(anchor_task_id=anchor.task_id, anchor_rank=anchor.rank, outcome=outcome))
        if outcome == "tie":
            tie_anchor_index = midpoint
            low = midpoint
            high = midpoint
            break
        if outcome == "target_harder":
            high = midpoint
        elif outcome == "anchor_harder":
            low = midpoint + 1
        else:
            raise ValueError(f"unexpected comparison outcome: {outcome}")

    return complete_similar_payload(entries, target_task, low, tie_anchor_index, neighbor_count, path)


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def format_markdown_rank(payload: dict[str, object]) -> str:
    """Render rank-mode output as Markdown."""
    ranking = require_list(require_key(payload, "ranking", "rank payload"), "ranking")
    lines = [
        "# Bradley-Terry Pairwise Ranking",
        "",
        f"- Tasks: {payload['task_count']}",
        f"- Comparisons: {payload['comparison_count']}",
        f"- Compared pairs: {require_mapping(payload['coverage'], 'coverage')['compared_pair_count']}",
        "",
        "## Difficulty Ranking",
        "",
    ]
    for raw_entry in ranking:
        entry = require_mapping(raw_entry, "ranking entry")
        lines.append(f"{entry['rank']}. `{entry['task_id']}` score={entry['difficulty_score']} - {entry['task_text']}")
    return "\n".join(lines)


def format_markdown_similar(payload: dict[str, object]) -> str:
    """Render similar-mode output as Markdown."""
    status = require_string(require_key(payload, "status", "similar payload"), "similar payload.status")
    lines = ["# Bradley-Terry Similar Difficulty Search", "", f"- Status: `{status}`", ""]
    if status == "needs_comparison":
        next_pair = require_mapping(require_key(payload, "next_pair", "similar payload"), "similar payload.next_pair")
        task_a = require_mapping(require_key(next_pair, "task_a", "next_pair"), "next_pair.task_a")
        task_b = require_mapping(require_key(next_pair, "task_b", "next_pair"), "next_pair.task_b")
        lines.extend(
            [
                "## Next Required Pair",
                "",
                f"- Task A: `{task_a['task_id']}` - {task_a['task_text']}",
                f"- Task B: `{task_b['task_id']}` - {task_b['task_text']}",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            f"- Estimated insertion rank: {payload['estimated_insertion_rank']}",
            f"- Estimated difficulty score: {payload['estimated_difficulty_score']}",
            "",
            "## Similar Tasks",
            "",
        ]
    )
    similar_tasks = require_list(require_key(payload, "similar_tasks", "similar payload"), "similar_tasks")
    for raw_entry in similar_tasks:
        entry = require_mapping(raw_entry, "similar task")
        lines.append(f"- rank {entry['rank']} `{entry['task_id']}` distance={entry['score_distance']} - {entry['task_text']}")
    return "\n".join(lines)


def format_output(payload: dict[str, object], output_format: str) -> str:
    """Render output in the requested format."""
    if output_format == "json":
        return format_json(payload)
    if output_format == "markdown":
        mode = require_string(require_key(payload, "mode", "payload"), "payload.mode")
        if mode == "rank":
            return format_markdown_rank(payload)
        if mode == "similar":
            return format_markdown_similar(payload)
        raise ValueError(f"unsupported payload mode: {mode}")
    raise ValueError(f"unsupported output format: {output_format}")


def main(argv: list[str]) -> int:
    """Run the Bradley-Terry pairwise ranking CLI."""
    try:
        args = parse_args(argv)
        command = args.command
        output_format = args.format
        if not isinstance(command, str):
            raise TypeError("command must be a string")
        if not isinstance(output_format, str):
            raise TypeError("--format must be a string")

        if command == "rank":
            tasks = parse_tasks(load_json_file(args.tasks))
            allowed_task_ids = {task.task_id for task in tasks}
            comparisons = parse_comparisons(load_json_file(args.comparisons), allowed_task_ids)
            payload = run_rank(tasks, comparisons)
            print(format_output(payload, output_format))
            return 0

        if command == "similar":
            entries = parse_ranking(load_json_file(args.ranking))
            target_task = parse_target_task(load_json_file(args.target_task))
            allowed_task_ids = {entry.task_id for entry in entries}
            allowed_task_ids.add(target_task.task_id)
            comparisons = parse_comparisons(load_json_file(args.comparisons), allowed_task_ids)
            neighbor_count = args.neighbors
            if not isinstance(neighbor_count, int):
                raise TypeError("--neighbors must be an integer")
            payload = run_similar(entries, target_task, comparisons, neighbor_count)
            print(format_output(payload, output_format))
            return 0

        raise ValueError(f"unsupported command: {command}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
