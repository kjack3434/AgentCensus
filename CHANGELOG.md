# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-18

### Added
- `agentcensus sweep` — discover AI agents and emit a single self-contained HTML report (or JSON).
- Sources: `demo` (bundled synthetic estate), `copilot-studio` (live), `foundry` (live), and `all` (merged).
- Auth modes: `device` (device code, default), `app` (service principal), `cli` (reuse `az login`).
- Governance findings (`SWEEP-###` rules) with severities, categories, and a per-run summary.
- Interactive HTML report: searchable / sortable / filterable table with expandable per-agent detail.
- `--fail-on` CI gate, `--format json`, `version`, and `schema` commands.

[Unreleased]: https://github.com/kjack3434/AgentCensus/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kjack3434/AgentCensus/releases/tag/v0.1.0
