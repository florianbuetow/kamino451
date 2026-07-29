"""Contract tests for the post-run token usage and cost accounting script."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def test_factory_config_has_pricing_for_model_ladder():
    config = json.loads((repo_root() / ".kamino" / "factory-config.json").read_text(encoding="utf-8"))
    pricing = config["pricing"]
    assert pricing["currency"] == "USD"
    for name in ("haiku", "sonnet", "opus"):
        entry = pricing["models"][name]
        assert entry["model_ids"], f"{name} needs at least one API model id"
        assert entry["input_per_mtok"] > 0
        assert entry["output_per_mtok"] > 0
