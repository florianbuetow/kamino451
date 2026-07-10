"""Shared validation for Kamino task outcome ledger scripts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

LEDGER_SCHEMA_VERSION = "kamino451.task-outcome-ledger.v1"
TASK_EVALUATION_SCHEMA_VERSION = "kamino451.task-evaluation.v1"
PAIRWISE_SCHEMA_VERSION = "kamino451.bradley-terry-pairwise-ranking.v1"
CANDIDATE_SEARCH_SCHEMA_VERSION = "kamino451.agent-candidate-search.v1"
TASK_DETAIL_SCHEMA_VERSION = "kamino451.task-detail.v1"
FACTORY_CONFIG_SCHEMA_VERSION = "kamino451.factory-config.v1"
DEFAULT_FACTORY_CONFIG_PATH = ".kamino/factory-config.json"
DEFAULT_SUCCESS_RATE_THRESHOLD = 0.9
DEFAULT_MIN_ATTEMPTS_FOR_RATE = 3
ALLOWED_ROUTES = {"clone", "taskgraph", "createblueprint"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
FORBIDDEN_CANDIDATE_SCORE_KEYS = {"score", "similarity_score", "score_components", "weight", "weights"}


def load_json_file(raw_path: str, label: str) -> object:
    """Load a required JSON file."""
    path = Path(raw_path)
    if not path.is_file():
        raise FileNotFoundError(f"{label} file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    if text.strip() == "":
        raise ValueError(f"{label} file is empty: {path}")
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


def require_bool(value: object, label: str) -> bool:
    """Require a JSON boolean."""
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be a boolean")
    return value


def require_number(value: object, label: str) -> float:
    """Require a JSON number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{label} must be a number")
    return float(value)


def require_int_score(value: object, label: str) -> int:
    """Require an integer rubric score from 1 through 5."""
    number = require_number(value, label)
    score = int(number)
    if float(score) != number:
        raise ValueError(f"{label} must be an integer")
    if score < 1 or score > 5:
        raise ValueError(f"{label} must be between 1 and 5")
    return score


def require_positive_int(value: object, label: str) -> int:
    """Require an integer of at least 1."""
    number = require_number(value, label)
    parsed = int(number)
    if float(parsed) != number:
        raise ValueError(f"{label} must be an integer")
    if parsed < 1:
        raise ValueError(f"{label} must be at least 1")
    return parsed


def require_key(mapping: dict[str, object], key: str, label: str) -> object:
    """Require a key in a JSON object."""
    if key not in mapping:
        raise ValueError(f"{label} is missing required key: {key}")
    return mapping[key]


def require_string_list(value: object, label: str) -> list[str]:
    """Require a JSON array of non-empty strings."""
    raw_items = require_list(value, label)
    items: list[str] = []
    for index, raw_item in enumerate(raw_items):
        items.append(require_string(raw_item, f"{label}[{index}]"))
    return items


def validate_sha256_hash(value: str, label: str) -> str:
    """Validate a sha256:<hex> value."""
    prefix = "sha256:"
    if not value.startswith(prefix):
        raise ValueError(f"{label} must start with sha256:")
    digest = value[len(prefix) :]
    if len(digest) != 64:
        raise ValueError(f"{label} must contain a 64-character SHA-256 digest")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError(f"{label} digest must be hexadecimal") from exc
    return value


def stable_sha256(payload: object) -> str:
    """Return a stable SHA-256 digest for a JSON-serializable payload."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_nearest_prior_tasks(value: object, label: str) -> list[dict[str, object]]:
    """Parse nearest prior task references."""
    raw_tasks = require_list(value, label)
    tasks: list[dict[str, object]] = []
    for index, raw_task in enumerate(raw_tasks):
        mapping = require_mapping(raw_task, f"{label}[{index}]")
        task_id = require_string(require_key(mapping, "task_id", f"{label}[{index}]"), f"{label}[{index}].task_id")
        if "distance" in mapping:
            distance = require_number(require_key(mapping, "distance", f"{label}[{index}]"), f"{label}[{index}].distance")
        elif "score_distance" in mapping:
            distance = require_number(
                require_key(mapping, "score_distance", f"{label}[{index}]"),
                f"{label}[{index}].score_distance",
            )
        else:
            raise ValueError(f"{label}[{index}] is missing required key: distance")
        if distance < 0.0:
            raise ValueError(f"{label}[{index}].distance must be non-negative")
        tasks.append({"task_id": task_id, "distance": round(distance, 6)})
    return tasks


def load_routing_config(raw_path: str | None) -> dict[str, object]:
    """Load the central factory routing config.

    An explicitly passed path must exist. The default path may be absent
    (fresh checkout / cold start), in which case built-in defaults apply.
    """
    path = Path(raw_path) if raw_path is not None else Path(DEFAULT_FACTORY_CONFIG_PATH)
    if not path.is_file():
        if raw_path is not None:
            raise FileNotFoundError(f"factory config file does not exist: {path}")
        return {
            "success_rate_threshold": DEFAULT_SUCCESS_RATE_THRESHOLD,
            "min_attempts_for_rate": DEFAULT_MIN_ATTEMPTS_FOR_RATE,
            "config_source": "built_in_defaults",
            "config_path": str(path),
        }

    mapping = require_mapping(load_json_file(str(path), "factory config"), "factory config")
    schema_version = require_string(require_key(mapping, "schema_version", "factory config"), "factory config.schema_version")
    if schema_version != FACTORY_CONFIG_SCHEMA_VERSION:
        raise ValueError(f"factory config schema_version must be {FACTORY_CONFIG_SCHEMA_VERSION}")
    routing = require_mapping(require_key(mapping, "routing", "factory config"), "factory config.routing")
    threshold = require_number(
        require_key(routing, "success_rate_threshold", "factory config.routing"),
        "factory config.routing.success_rate_threshold",
    )
    if threshold < 0.0 or threshold >= 1.0:
        raise ValueError("factory config.routing.success_rate_threshold must be >= 0 and < 1")
    min_attempts = require_positive_int(
        require_key(routing, "min_attempts_for_rate", "factory config.routing"),
        "factory config.routing.min_attempts_for_rate",
    )
    return {
        "success_rate_threshold": threshold,
        "min_attempts_for_rate": min_attempts,
        "config_source": "config_file",
        "config_path": str(path),
    }


def parse_task_evaluation(payload: object) -> dict[str, object]:
    """Parse a task evaluation JSON artifact."""
    mapping = require_mapping(payload, "task evaluation")
    schema_version = require_string(require_key(mapping, "schema_version", "task evaluation"), "task evaluation.schema_version")
    if schema_version != TASK_EVALUATION_SCHEMA_VERSION:
        raise ValueError(f"task evaluation schema_version must be {TASK_EVALUATION_SCHEMA_VERSION}")

    parsed = {
        "schema_version": schema_version,
        "task_id": require_string(require_key(mapping, "task_id", "task evaluation"), "task evaluation.task_id"),
        "task_text_hash": validate_sha256_hash(
            require_string(require_key(mapping, "task_text_hash", "task evaluation"), "task evaluation.task_text_hash"),
            "task evaluation.task_text_hash",
        ),
        "task_text": require_string(require_key(mapping, "task_text", "task evaluation"), "task evaluation.task_text"),
        "task_type": require_string(require_key(mapping, "task_type", "task evaluation"), "task evaluation.task_type"),
        "clarity_score": require_int_score(require_key(mapping, "clarity_score", "task evaluation"), "task evaluation.clarity_score"),
        "ambiguity_score": require_int_score(
            require_key(mapping, "ambiguity_score", "task evaluation"),
            "task evaluation.ambiguity_score",
        ),
        "consistency_score": require_int_score(
            require_key(mapping, "consistency_score", "task evaluation"),
            "task evaluation.consistency_score",
        ),
        "completeness_score": require_int_score(
            require_key(mapping, "completeness_score", "task evaluation"),
            "task evaluation.completeness_score",
        ),
        "difficulty_score": require_int_score(
            require_key(mapping, "difficulty_score", "task evaluation"),
            "task evaluation.difficulty_score",
        ),
        "recommended_mapping": require_string(
            require_key(mapping, "recommended_mapping", "task evaluation"),
            "task evaluation.recommended_mapping",
        ),
        "open_issues": require_string_list(require_key(mapping, "open_issues", "task evaluation"), "task evaluation.open_issues"),
    }
    return parsed


def parse_difficulty_placement(payload: object) -> dict[str, object]:
    """Parse rank-task-difficulty placement output."""
    mapping = require_mapping(payload, "difficulty placement")
    if "schema_version" in mapping:
        schema_version = require_string(
            require_key(mapping, "schema_version", "difficulty placement"),
            "difficulty placement.schema_version",
        )
        if schema_version != PAIRWISE_SCHEMA_VERSION:
            raise ValueError(f"difficulty placement schema_version must be {PAIRWISE_SCHEMA_VERSION}")

    estimated_score = require_number(
        require_key(mapping, "estimated_difficulty_score", "difficulty placement"),
        "difficulty placement.estimated_difficulty_score",
    )
    insertion_rank = require_number(
        require_key(mapping, "estimated_insertion_rank", "difficulty placement"),
        "difficulty placement.estimated_insertion_rank",
    )
    if insertion_rank < 1:
        raise ValueError("difficulty placement.estimated_insertion_rank must be at least 1")

    if "nearest_prior_tasks" in mapping:
        nearest_prior_tasks = parse_nearest_prior_tasks(
            require_key(mapping, "nearest_prior_tasks", "difficulty placement"),
            "difficulty placement.nearest_prior_tasks",
        )
    elif "similar_tasks" in mapping:
        nearest_prior_tasks = parse_nearest_prior_tasks(
            require_key(mapping, "similar_tasks", "difficulty placement"),
            "difficulty placement.similar_tasks",
        )
    else:
        raise ValueError("difficulty placement is missing required key: nearest_prior_tasks")

    if len(nearest_prior_tasks) == 0:
        raise ValueError("difficulty placement.nearest_prior_tasks must not be empty")

    return {
        "estimated_insertion_rank": int(insertion_rank),
        "estimated_difficulty_score": round(estimated_score, 6),
        "nearest_prior_tasks": nearest_prior_tasks,
    }


def parse_route_decision(payload: object) -> dict[str, object]:
    """Parse a factory route decision artifact."""
    mapping = require_mapping(payload, "route decision")
    route = require_string(require_key(mapping, "route_chosen", "route decision"), "route decision.route_chosen")
    if route not in ALLOWED_ROUTES:
        allowed = ", ".join(sorted(ALLOWED_ROUTES))
        raise ValueError(f"route decision.route_chosen must be one of: {allowed}")

    return {
        "route_chosen": route,
        "agent_files_used": require_string_list(require_key(mapping, "agent_files_used", "route decision"), "route decision.agent_files_used"),
        "agent_blueprints_used": require_string_list(
            require_key(mapping, "agent_blueprints_used", "route decision"),
            "route decision.agent_blueprints_used",
        ),
        "model": require_string(require_key(mapping, "model", "route decision"), "route decision.model"),
        "effort": require_string(require_key(mapping, "effort", "route decision"), "route decision.effort"),
    }


def parse_run_evidence(payload: object) -> dict[str, object]:
    """Parse run verification evidence."""
    mapping = require_mapping(payload, "run evidence")
    execution_status = require_string(require_key(mapping, "execution_status", "run evidence"), "run evidence.execution_status")
    if execution_status not in {"completed", "failed"}:
        raise ValueError("run evidence.execution_status must be completed or failed")
    verification_evidence = require_mapping(
        require_key(mapping, "verification_evidence", "run evidence"),
        "run evidence.verification_evidence",
    )
    return {
        "execution_status": execution_status,
        "output_paths": require_string_list(require_key(mapping, "output_paths", "run evidence"), "run evidence.output_paths"),
        "verification_evidence": verification_evidence,
    }


def parse_success_judgment(payload: object) -> dict[str, object]:
    """Parse a binary run-success judgment."""
    mapping = require_mapping(payload, "success judgment")
    success = require_bool(require_key(mapping, "success", "success judgment"), "success judgment.success")
    confidence = require_string(require_key(mapping, "confidence", "success judgment"), "success judgment.confidence")
    if confidence not in ALLOWED_CONFIDENCE:
        allowed = ", ".join(sorted(ALLOWED_CONFIDENCE))
        raise ValueError(f"success judgment.confidence must be one of: {allowed}")

    parsed = {
        "success": success,
        "reason": require_string(require_key(mapping, "reason", "success judgment"), "success judgment.reason"),
        "satisfied_requirements": require_string_list(
            require_key(mapping, "satisfied_requirements", "success judgment"),
            "success judgment.satisfied_requirements",
        ),
        "missing_requirements": require_string_list(
            require_key(mapping, "missing_requirements", "success judgment"),
            "success judgment.missing_requirements",
        ),
        "partial_requirements": require_string_list(
            require_key(mapping, "partial_requirements", "success judgment"),
            "success judgment.partial_requirements",
        ),
        "unverifiable_requirements": require_string_list(
            require_key(mapping, "unverifiable_requirements", "success judgment"),
            "success judgment.unverifiable_requirements",
        ),
        "confidence": confidence,
    }
    return parsed


def reject_forbidden_candidate_keys(payload: object, label: str) -> None:
    """Reject factory-facing candidate payloads that expose numeric scoring details."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in FORBIDDEN_CANDIDATE_SCORE_KEYS:
                raise ValueError(f"{label} contains forbidden candidate score key: {key}")
            reject_forbidden_candidate_keys(value, f"{label}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            reject_forbidden_candidate_keys(value, f"{label}[{index}]")


def parse_candidate_prior_task(value: object, label: str) -> dict[str, object]:
    """Parse one prior task summary from candidate search output."""
    mapping = require_mapping(value, label)
    return {
        "record_id": require_string(require_key(mapping, "record_id", label), f"{label}.record_id"),
        "task_id": require_string(require_key(mapping, "task_id", label), f"{label}.task_id"),
        "task_text_excerpt": require_string(
            require_key(mapping, "task_text_excerpt", label),
            f"{label}.task_text_excerpt",
        ),
        "task_type": require_string(require_key(mapping, "task_type", label), f"{label}.task_type"),
        "route_chosen": require_string(require_key(mapping, "route_chosen", label), f"{label}.route_chosen"),
        "model": require_string(require_key(mapping, "model", label), f"{label}.model"),
        "effort": require_string(require_key(mapping, "effort", label), f"{label}.effort"),
    }


def parse_candidate_search_candidate(value: object, label: str) -> dict[str, object]:
    """Parse one score-free candidate search result."""
    mapping = require_mapping(value, label)
    success_count = require_number(require_key(mapping, "historical_success_count", label), f"{label}.historical_success_count")
    success_count_int = int(success_count)
    if float(success_count_int) != success_count:
        raise ValueError(f"{label}.historical_success_count must be an integer")
    if success_count_int < 1:
        raise ValueError(f"{label}.historical_success_count must be at least 1")
    route = require_string(require_key(mapping, "route_chosen", label), f"{label}.route_chosen")
    if route not in ALLOWED_ROUTES:
        allowed = ", ".join(sorted(ALLOWED_ROUTES))
        raise ValueError(f"{label}.route_chosen must be one of: {allowed}")

    raw_prior_tasks = require_list(require_key(mapping, "similar_prior_tasks", label), f"{label}.similar_prior_tasks")
    prior_tasks = [
        parse_candidate_prior_task(raw_prior_task, f"{label}.similar_prior_tasks[{index}]")
        for index, raw_prior_task in enumerate(raw_prior_tasks)
    ]
    if len(prior_tasks) == 0:
        raise ValueError(f"{label}.similar_prior_tasks must not be empty")

    parsed = {
        "candidate_id": require_string(require_key(mapping, "candidate_id", label), f"{label}.candidate_id"),
        "route_chosen": route,
        "agent_blueprints_used": require_string_list(
            require_key(mapping, "agent_blueprints_used", label),
            f"{label}.agent_blueprints_used",
        ),
        "agent_files_used": require_string_list(require_key(mapping, "agent_files_used", label), f"{label}.agent_files_used"),
        "model": require_string(require_key(mapping, "model", label), f"{label}.model"),
        "effort": require_string(require_key(mapping, "effort", label), f"{label}.effort"),
        "historical_success_count": success_count_int,
        "matched_task_types": require_string_list(
            require_key(mapping, "matched_task_types", label),
            f"{label}.matched_task_types",
        ),
        "similar_prior_tasks": prior_tasks,
        "match_reasons": require_string_list(require_key(mapping, "match_reasons", label), f"{label}.match_reasons"),
    }
    # Success-rate statistics are optional so pre-rate v1 artifacts stay valid.
    for count_key in ("historical_attempt_count", "same_task_type_attempt_count", "same_task_type_success_count"):
        if count_key in mapping:
            raw_count = require_number(require_key(mapping, count_key, label), f"{label}.{count_key}")
            count = int(raw_count)
            if float(count) != raw_count:
                raise ValueError(f"{label}.{count_key} must be an integer")
            if count < 0:
                raise ValueError(f"{label}.{count_key} must be non-negative")
            parsed[count_key] = count
    for rate_key in ("historical_success_rate", "same_task_type_success_rate"):
        if rate_key in mapping:
            rate = require_number(require_key(mapping, rate_key, label), f"{label}.{rate_key}")
            if rate < 0.0 or rate > 1.0:
                raise ValueError(f"{label}.{rate_key} must be between 0 and 1")
            parsed[rate_key] = rate
    if "meets_success_rate_threshold" in mapping:
        parsed["meets_success_rate_threshold"] = require_bool(
            require_key(mapping, "meets_success_rate_threshold", label),
            f"{label}.meets_success_rate_threshold",
        )
    return parsed


def parse_candidate_search(payload: object) -> dict[str, object]:
    """Parse score-free agent candidate search JSON."""
    reject_forbidden_candidate_keys(payload, "candidate search")
    mapping = require_mapping(payload, "candidate search")
    schema_version = require_string(require_key(mapping, "schema_version", "candidate search"), "candidate search.schema_version")
    if schema_version != CANDIDATE_SEARCH_SCHEMA_VERSION:
        raise ValueError(f"candidate search.schema_version must be {CANDIDATE_SEARCH_SCHEMA_VERSION}")
    task_id = require_string(require_key(mapping, "task_id", "candidate search"), "candidate search.task_id")
    task_text_hash = validate_sha256_hash(
        require_string(require_key(mapping, "task_text_hash", "candidate search"), "candidate search.task_text_hash"),
        "candidate search.task_text_hash",
    )
    limit = require_number(require_key(mapping, "limit", "candidate search"), "candidate search.limit")
    limit_int = int(limit)
    if float(limit_int) != limit:
        raise ValueError("candidate search.limit must be an integer")
    if limit_int < 1:
        raise ValueError("candidate search.limit must be at least 1")
    candidate_count = require_number(
        require_key(mapping, "candidate_count", "candidate search"),
        "candidate search.candidate_count",
    )
    candidate_count_int = int(candidate_count)
    if float(candidate_count_int) != candidate_count:
        raise ValueError("candidate search.candidate_count must be an integer")
    if candidate_count_int < 0:
        raise ValueError("candidate search.candidate_count must be non-negative")
    raw_candidates = require_list(require_key(mapping, "candidates", "candidate search"), "candidate search.candidates")
    if len(raw_candidates) != candidate_count_int:
        raise ValueError("candidate search.candidate_count must match candidates length")
    if len(raw_candidates) > limit_int:
        raise ValueError("candidate search.candidates length must not exceed limit")

    return {
        "schema_version": schema_version,
        "task_id": task_id,
        "task_text_hash": task_text_hash,
        "limit": limit_int,
        "candidate_count": candidate_count_int,
        "candidates": [
            parse_candidate_search_candidate(raw_candidate, f"candidate search.candidates[{index}]")
            for index, raw_candidate in enumerate(raw_candidates)
        ],
    }


def parse_task_detail(payload: object) -> dict[str, object]:
    """Parse a durable pre-run task detail artifact."""
    mapping = require_mapping(payload, "task detail")
    schema_version = require_string(require_key(mapping, "schema_version", "task detail"), "task detail.schema_version")
    if schema_version != TASK_DETAIL_SCHEMA_VERSION:
        raise ValueError(f"task detail.schema_version must be {TASK_DETAIL_SCHEMA_VERSION}")

    task_id = require_string(require_key(mapping, "task_id", "task detail"), "task detail.task_id")
    task_text_hash = validate_sha256_hash(
        require_string(require_key(mapping, "task_text_hash", "task detail"), "task detail.task_text_hash"),
        "task detail.task_text_hash",
    )
    task_text = require_string(require_key(mapping, "task_text", "task detail"), "task detail.task_text")
    task_evaluation_path = require_string(
        require_key(mapping, "task_evaluation_path", "task detail"),
        "task detail.task_evaluation_path",
    )
    difficulty_placement_path = require_string(
        require_key(mapping, "difficulty_placement_path", "task detail"),
        "task detail.difficulty_placement_path",
    )
    candidate_search_path = require_string(
        require_key(mapping, "candidate_search_path", "task detail"),
        "task detail.candidate_search_path",
    )
    route_decision_path = require_string(
        require_key(mapping, "route_decision_path", "task detail"),
        "task detail.route_decision_path",
    )
    created_at = require_string(require_key(mapping, "created_at", "task detail"), "task detail.created_at")

    task_evaluation = parse_task_evaluation(require_key(mapping, "task_evaluation", "task detail"))
    difficulty_placement = parse_difficulty_placement(require_key(mapping, "difficulty_placement", "task detail"))
    candidate_search = parse_candidate_search(require_key(mapping, "candidate_search", "task detail"))
    route_decision = parse_route_decision(require_key(mapping, "route_decision", "task detail"))

    if task_evaluation["task_id"] != task_id:
        raise ValueError("task detail.task_id must match embedded task evaluation")
    if task_evaluation["task_text_hash"] != task_text_hash:
        raise ValueError("task detail.task_text_hash must match embedded task evaluation")
    if task_evaluation["task_text"] != task_text:
        raise ValueError("task detail.task_text must match embedded task evaluation")
    if candidate_search["task_id"] != task_id:
        raise ValueError("task detail.task_id must match embedded candidate search")
    if candidate_search["task_text_hash"] != task_text_hash:
        raise ValueError("task detail.task_text_hash must match embedded candidate search")

    validated: dict[str, object] = {
        "schema_version": schema_version,
        "task_id": task_id,
        "task_text_hash": task_text_hash,
        "task_text": task_text,
        "task_evaluation_path": task_evaluation_path,
        "difficulty_placement_path": difficulty_placement_path,
        "candidate_search_path": candidate_search_path,
        "route_decision_path": route_decision_path,
        "created_at": created_at,
        "task_evaluation": task_evaluation,
        "difficulty_placement": difficulty_placement,
        "candidate_search": candidate_search,
        "route_decision": route_decision,
    }
    if "attempt" in mapping:
        validated["attempt"] = require_positive_int(require_key(mapping, "attempt", "task detail"), "task detail.attempt")
    return validated


def normalized_success(parsed_judgment: dict[str, object]) -> bool:
    """Apply the ledger rule that partial, missing, or unverifiable completion is failure."""
    success = require_bool(require_key(parsed_judgment, "success", "success judgment"), "success judgment.success")
    missing = require_string_list(
        require_key(parsed_judgment, "missing_requirements", "success judgment"),
        "success judgment.missing_requirements",
    )
    partial = require_string_list(
        require_key(parsed_judgment, "partial_requirements", "success judgment"),
        "success judgment.partial_requirements",
    )
    unverifiable = require_string_list(
        require_key(parsed_judgment, "unverifiable_requirements", "success judgment"),
        "success judgment.unverifiable_requirements",
    )
    if len(missing) > 0 or len(partial) > 0 or len(unverifiable) > 0:
        return False
    return success


def failure_mode_for_judgment(parsed_judgment: dict[str, object]) -> str:
    """Derive a deterministic failure mode from the binary judgment."""
    if normalized_success(parsed_judgment):
        return "none"
    if len(require_string_list(require_key(parsed_judgment, "partial_requirements", "success judgment"), "partial_requirements")) > 0:
        return "partial_completion"
    if len(require_string_list(require_key(parsed_judgment, "missing_requirements", "success judgment"), "missing_requirements")) > 0:
        return "missing_required_output"
    if len(require_string_list(require_key(parsed_judgment, "unverifiable_requirements", "success judgment"), "unverifiable_requirements")) > 0:
        return "unverifiable_completion"
    return "judged_failure"


def validate_ledger_record(payload: object, label: str) -> dict[str, object]:
    """Validate one task outcome ledger record."""
    mapping = require_mapping(payload, label)
    schema_version = require_string(require_key(mapping, "schema_version", label), f"{label}.schema_version")
    if schema_version != LEDGER_SCHEMA_VERSION:
        raise ValueError(f"{label}.schema_version must be {LEDGER_SCHEMA_VERSION}")

    route = require_string(require_key(mapping, "route_chosen", label), f"{label}.route_chosen")
    if route not in ALLOWED_ROUTES:
        allowed = ", ".join(sorted(ALLOWED_ROUTES))
        raise ValueError(f"{label}.route_chosen must be one of: {allowed}")
    raw_record_sequence = require_number(require_key(mapping, "record_sequence", label), f"{label}.record_sequence")
    record_sequence = int(raw_record_sequence)
    if float(record_sequence) != raw_record_sequence:
        raise ValueError(f"{label}.record_sequence must be an integer")
    if record_sequence < 1:
        raise ValueError(f"{label}.record_sequence must be at least 1")

    validated = {
        "schema_version": schema_version,
        "record_id": require_string(require_key(mapping, "record_id", label), f"{label}.record_id"),
        "record_sequence": record_sequence,
        "timestamp": require_string(require_key(mapping, "timestamp", label), f"{label}.timestamp"),
        "task_detail_path": None,
        "task_id": require_string(require_key(mapping, "task_id", label), f"{label}.task_id"),
        "task_text_hash": validate_sha256_hash(
            require_string(require_key(mapping, "task_text_hash", label), f"{label}.task_text_hash"),
            f"{label}.task_text_hash",
        ),
        "task_text": require_string(require_key(mapping, "task_text", label), f"{label}.task_text"),
        "task_type": require_string(require_key(mapping, "task_type", label), f"{label}.task_type"),
        "clarity_score": require_int_score(require_key(mapping, "clarity_score", label), f"{label}.clarity_score"),
        "ambiguity_score": require_int_score(require_key(mapping, "ambiguity_score", label), f"{label}.ambiguity_score"),
        "consistency_score": require_int_score(require_key(mapping, "consistency_score", label), f"{label}.consistency_score"),
        "completeness_score": require_int_score(require_key(mapping, "completeness_score", label), f"{label}.completeness_score"),
        "semantic_difficulty_score": require_number(
            require_key(mapping, "semantic_difficulty_score", label),
            f"{label}.semantic_difficulty_score",
        ),
        "pairwise_difficulty_score": require_number(
            require_key(mapping, "pairwise_difficulty_score", label),
            f"{label}.pairwise_difficulty_score",
        ),
        "nearest_prior_tasks": parse_nearest_prior_tasks(
            require_key(mapping, "nearest_prior_tasks", label),
            f"{label}.nearest_prior_tasks",
        ),
        "route_chosen": route,
        "agent_files_used": require_string_list(require_key(mapping, "agent_files_used", label), f"{label}.agent_files_used"),
        "agent_blueprints_used": require_string_list(
            require_key(mapping, "agent_blueprints_used", label),
            f"{label}.agent_blueprints_used",
        ),
        "model": require_string(require_key(mapping, "model", label), f"{label}.model"),
        "effort": require_string(require_key(mapping, "effort", label), f"{label}.effort"),
        "execution_status": require_string(require_key(mapping, "execution_status", label), f"{label}.execution_status"),
        "success": require_bool(require_key(mapping, "success", label), f"{label}.success"),
        "failure_mode": require_string(require_key(mapping, "failure_mode", label), f"{label}.failure_mode"),
        "success_judgment_path": require_string(
            require_key(mapping, "success_judgment_path", label),
            f"{label}.success_judgment_path",
        ),
        "output_paths": require_string_list(require_key(mapping, "output_paths", label), f"{label}.output_paths"),
        "verification_evidence": require_mapping(
            require_key(mapping, "verification_evidence", label),
            f"{label}.verification_evidence",
        ),
        "success_judgment": parse_success_judgment(require_key(mapping, "success_judgment", label)),
    }
    if "task_detail_path" in mapping:
        validated["task_detail_path"] = require_string(require_key(mapping, "task_detail_path", label), f"{label}.task_detail_path")
    return validated


def load_ledger_records(path: Path, *, allow_empty: bool) -> list[dict[str, object]]:
    """Load and validate JSONL ledger records."""
    if not path.is_file():
        raise FileNotFoundError(f"ledger file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    if text.strip() == "":
        if allow_empty:
            return []
        raise ValueError(f"ledger file is empty: {path}")

    records: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if raw_line.strip() == "":
            raise ValueError(f"ledger line {line_number} is empty")
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ledger line {line_number} is malformed JSON") from exc
        records.append(validate_ledger_record(payload, f"ledger line {line_number}"))
    if len(records) == 0 and not allow_empty:
        raise ValueError(f"ledger file is empty: {path}")
    return records
