# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## 2026-07-18

### Added

- Added `/copy-factory` skill that clones the factory into another folder, excluding tests.
- Added semgrep rules failing CI on `docs/` references from `.claude/` or `.kamino/`.

### Changed

- Inlined the judging rubric into task-llm-judge, binding recommendations to five routing tokens.

### Removed

- Removed the contract test that read the gitignored ideation document.

## 2026-07-10

### Added

- Added the complete agent factory: skills, judge agents, blueprints, and deterministic eval scripts.
- Added run capsules with compile/run phases, traces, and a task-outcome ledger.
- Added Bradley-Terry difficulty ranking, agent candidate search, and route recommendation.
- Added error-analysis, difficulty-calibration, and sweep report dashboards.
- Added corpus ingestion, the AutoResearch improve loop, and run replay.

## 2026-07-09

### Added

- Added the MIT license.
