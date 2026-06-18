"""agentcensus command-line interface (Typer)."""

from __future__ import annotations

import json
import webbrowser
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import typer

from . import __version__
from .errors import DiscoveryError
from .live.auth import build_auth
from .models import SEVERITY_RANK, Severity, SweepResult
from .report import render_html, render_json
from .sources import build_source

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Discover AI agents (Copilot Studio + Azure AI Foundry) and emit an HTML report.",
)


class SourceOpt(StrEnum):
    all = "all"
    copilot_studio = "copilot-studio"
    foundry = "foundry"
    demo = "demo"


class AuthMode(StrEnum):
    device = "device"  # interactive device-code; runs as the signed-in user
    app = "app"  # client-credentials; service principal (headless/CI)
    cli = "cli"  # reuse an existing `az login` session; no app registration


class OutputFormat(StrEnum):
    html = "html"
    json = "json"


_SOURCE_LABEL = {"copilot_studio": "Copilot Studio", "azure_ai_foundry": "Azure AI Foundry"}


def _print_summary(result: SweepResult, out: Path) -> None:
    s = result.summary
    src_bits = ", ".join(f"{n} {_SOURCE_LABEL.get(k, k)}" for k, n in sorted(s.by_source.items()))
    typer.echo(
        f"Swept {s.total_agents} agents from '{result.meta.source}'"
        + (f"  ({src_bits})" if src_bits else "")
    )

    if s.findings_by_severity:
        order = ["critical", "high", "medium", "low", "info"]
        tally = ", ".join(
            f"{s.findings_by_severity[k]} {k}" for k in order if s.findings_by_severity.get(k)
        )
        typer.echo(f"Findings: {s.total_findings}  —  {tally}")
    else:
        typer.echo("Findings: 0")

    for w in result.warnings:
        typer.echo(f"warning: {w}", err=True)
    typer.echo(f"Report written to {out}")


@app.command()
def sweep(
    demo: bool = typer.Option(
        False, "--demo", help="Use the bundled synthetic estate (implies --source demo)."
    ),
    source: SourceOpt = typer.Option(
        SourceOpt.all,
        "--source",
        help=(
            "Discovery source: all (Copilot Studio + Foundry), copilot-studio, foundry, or demo."
        ),
        case_sensitive=False,
    ),
    auth_mode: AuthMode = typer.Option(
        AuthMode.device,
        "--auth",
        help=(
            "Live auth: device (sign in as yourself), app (service principal), "
            "or cli (reuse your `az login`; no app registration)."
        ),
        case_sensitive=False,
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Output file (default: reports/agentcensus-<source>-<timestamp>.<ext>).",
    ),
    fmt: OutputFormat = typer.Option(
        OutputFormat.html, "--format", "-f", help="Output format.", case_sensitive=False
    ),
    stale_days: int = typer.Option(
        90, "--stale-days", help="Flag agents not modified in this many days."
    ),
    client_id: str | None = typer.Option(
        None,
        "--client-id",
        envvar="AGENTCENSUS_CLIENT_ID",
        help="Entra app registration client id (required for live).",
    ),
    tenant: str | None = typer.Option(
        None,
        "--tenant",
        envvar="AGENTCENSUS_TENANT",
        help="Entra tenant id (required for --auth app; optional for device).",
    ),
    client_secret: str | None = typer.Option(
        None,
        "--client-secret",
        envvar="AGENTCENSUS_CLIENT_SECRET",
        help="Client secret (required for --auth app); prefer the env var.",
    ),
    environment: str | None = typer.Option(
        None,
        "--environment",
        envvar="AGENTCENSUS_ENVIRONMENT",
        help="Limit Copilot Studio discovery to one environment.",
    ),
    subscription: str | None = typer.Option(
        None,
        "--subscription",
        envvar="AGENTCENSUS_SUBSCRIPTION",
        help="Limit Foundry discovery to one Azure subscription id.",
    ),
    open_report: bool = typer.Option(
        False, "--open", help="Open the report in your browser when done."
    ),
    fail_on: Severity | None = typer.Option(
        None,
        "--fail-on",
        case_sensitive=False,
        help="Exit non-zero if any finding is at or above this severity (CI gate).",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the terminal summary."),
) -> None:
    """Discover agents and write a report."""
    if demo:
        source = SourceOpt.demo

    if source is SourceOpt.demo:
        discoverer = build_source("demo", stale_days=stale_days)
    else:
        try:
            auth = build_auth(
                mode=auth_mode.value,
                client_id=client_id,
                tenant=tenant,
                client_secret=client_secret,
            )
        except DiscoveryError as exc:
            raise typer.BadParameter(
                f"{exc}. Or run `agentcensus sweep --demo` to see a sample report."
            ) from exc
        discoverer = build_source(
            source.value,
            stale_days=stale_days,
            auth=auth,
            environment=environment,
            subscription=subscription,
        )

    try:
        result = discoverer.scan()
    except DiscoveryError as exc:
        typer.echo(f"discovery failed: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    if out is None:
        ext = "json" if fmt is OutputFormat.json else "html"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = Path("reports") / f"agentcensus-{source.value}-{stamp}.{ext}"

    rendered = render_json(result) if fmt is OutputFormat.json else render_html(result)
    try:
        if out.parent:
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        typer.echo(f"could not write report to {out}: {exc}", err=True)
        raise typer.Exit(code=4) from exc

    if not quiet:
        _print_summary(result, out)

    if open_report:
        webbrowser.open(out.resolve().as_uri())

    if fail_on is not None:
        worst = max(
            (SEVERITY_RANK[f.severity] for a in result.agents for f in a.findings),
            default=-1,
        )
        if worst >= SEVERITY_RANK[fail_on]:
            typer.echo(f"FAILED: findings at or above '{fail_on.value}'", err=True)
            raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the agentcensus version."""
    typer.echo(__version__)


@app.command()
def schema() -> None:
    """Print the JSON schema of the sweep result."""
    typer.echo(json.dumps(SweepResult.model_json_schema(), indent=2))


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
