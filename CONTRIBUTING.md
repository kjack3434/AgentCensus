# Contributing to AgentCensus

Thanks for your interest! Issues, feature requests, and PRs are all welcome.

## Ground rules

- **Never commit real tenant data.** Use only synthetic data (the `contoso.example` domain, placeholder
  GUIDs). Generated reports live in `reports/` and are gitignored. A pre-commit secret/PII scanner is provided
  (see below) — please keep it enabled.
- Be kind. This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Dev setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # create the venv + install deps (incl. dev tools)
uv run agentcensus sweep --demo --open   # run it
```

## The dev loop

```bash
uv run ruff check .           # lint (must be clean)
uv run ruff format            # auto-format (CI checks `ruff format --check`)
uv run pyright                # type-check (must be clean)
uv run pytest                 # tests (must be green; runs with coverage)
uv build                      # build the wheel + sdist
```

`ruff check`, `ruff format --check`, `pyright`, and `pytest` all run in CI on every PR.

### Enable the secret/PII pre-commit guard

```bash
git config core.hooksPath .githooks
```

It blocks commits that contain secrets, keys, non-example emails, real tenant domains/GUIDs, or report files.

## Project layout

```
src/agent_census/
  cli.py        # Typer CLI (sweep / version / schema)
  models.py     # Agent + SweepResult (Pydantic)
  findings.py   # SWEEP-001..010 governance rules
  normalize.py  # raw API records -> Agent
  sources/      # demo, copilot_studio, foundry, all  (+ build_source registry)
  live/         # auth (device/app/cli), dataverse, foundry, constants
  report/       # html.py + assets/, json_out.py
  fixtures/     # demo_estate.json (synthetic)
tests/
```

## Pull requests

1. Branch off `main`.
2. Keep changes focused; add/update tests for behavior changes.
3. Ensure lint, format, type-check, and tests all pass (see the dev loop above).
4. For user-facing changes, add a line under **Unreleased** in `CHANGELOG.md` ([Keep a Changelog](https://keepachangelog.com) format).
5. Describe the change and rationale in the PR (the template will prompt you).

## Style

- Ruff-enforced, line length 100; prefer type hints and small, pure functions.
- Findings use the `SWEEP-###` namespace; new rules go in `findings.py` with a test.
