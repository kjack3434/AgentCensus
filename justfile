# agentcensus dev tasks — run with `just <task>` (https://github.com/casey/just)
# Mirrors CI so contributors get the same lint / type / test gate locally.

# List available tasks
default:
    @just --list

# Install dependencies (pass extra args, e.g. `just sync --extra gcp`)
sync *args:
    uv sync {{args}}

# Full local dev loop — lint, format-check, type-check, tests (mirrors CI)
dev:
    uv run ruff check .
    uv run ruff format --check
    uv run pyright
    uv run pytest

# Auto-fix formatting
fmt:
    uv run ruff format .

# Open a synthetic demo report in the browser (no auth needed)
demo:
    uv run agentcensus sweep --demo --open
