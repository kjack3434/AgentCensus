# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-24

### Added
- **Google Cloud agent discovery** (`--source gcp`): Vertex AI **Agent Engine**, **Agentspace / Gemini
  Enterprise**, and **Dialogflow CX**, with no-code (`lowcodeAgent`) enrichment of code-deployed agents.
  Optional `[gcp]` extra (`google-auth`) for service-account / ADC auth.
- Cross-cloud `--source all`: now spans Microsoft **and** Google Cloud — each provider authenticates
  independently and an unreachable one is skipped with a warning (per-provider `Auth —` status line).
  `--source` also accepts a comma list (e.g. `foundry,gcp`).
- Auth strategies applied per provider: `cli` (reuse `az` / `gcloud`), `app` (Entra app + secret / GCP
  service-account key), `device` (Microsoft only), `adc` (Google ADC).
- Findings: **SWEEP-011** (broadly shared agent with elevated capability) and **SWEEP-012** (external /
  cross-cloud data connection); new `org_wide` audience category.
- Provider-aware report: Microsoft / Google cloud chips, GCP coverage notes, app-level (shared) data stores.
- Options: `--project`, `--location` (GCP scope), `--gcp-key-file`, `--gcp-impersonate`.

### Changed
- Default `--auth` is now `cli` (reuse the local cloud CLI) instead of `device`.
- `--source` takes `all` (now both clouds) / `demo` / a comma list of `copilot-studio,foundry,gcp`.

### Security
- `.gitignore` now guards service-account key / credential filenames (e.g. `key.json`, `*.pem`).

## [0.1.0] - 2026-06-18

### Added
- `agentcensus sweep` — discover AI agents and emit a single self-contained HTML report (or JSON).
- Sources: `demo` (bundled synthetic estate), `copilot-studio` (live), `foundry` (live), and `all` (merged).
- Auth modes: `device` (device code, default), `app` (service principal), `cli` (reuse `az login`).
- Governance findings (`SWEEP-###` rules) with severities, categories, and a per-run summary.
- Interactive HTML report: searchable / sortable / filterable table with expandable per-agent detail.
- `--fail-on` CI gate, `--format json`, `version`, and `schema` commands.

[Unreleased]: https://github.com/kjack3434/AgentCensus/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kjack3434/AgentCensus/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kjack3434/AgentCensus/releases/tag/v0.1.0
