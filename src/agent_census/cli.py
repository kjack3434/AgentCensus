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
from .live.auth import build_gcp_auth, build_microsoft_auth
from .models import SEVERITY_RANK, Severity, SweepResult
from .report import render_html, render_json
from .sources import build_source

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Discover AI agents (Copilot Studio, Azure AI Foundry, Google Cloud) "
        "and emit an HTML report."
    ),
)


class AuthMode(StrEnum):
    cli = "cli"  # reuse the local cloud CLI session (Microsoft: az · Google: gcloud)
    app = "app"  # service credential (Microsoft: Entra app+secret · Google: SA key)
    device = "device"  # Entra device-code interactive sign-in (Microsoft only)
    adc = "adc"  # Application Default Credentials (Google only)


class OutputFormat(StrEnum):
    html = "html"
    json = "json"


# Connector keys grouped by which provider's auth they need.
_MS_KEYS = ("copilot_studio", "foundry")
_GCP_KEYS = ("gcp",)
_ALL_KEYS = (*_MS_KEYS, *_GCP_KEYS)


def _parse_sources(value: str) -> list[str]:
    """Parse a ``--source`` value (comma list, or 'all' / 'demo') into connector keys."""
    items = [v.strip().lower().replace("-", "_") for v in value.split(",") if v.strip()]
    if not items:
        return list(_ALL_KEYS)
    keys: list[str] = []
    for it in items:
        if it == "demo":
            return ["demo"]
        if it == "all":
            keys.extend(_ALL_KEYS)
        elif it in _ALL_KEYS:
            keys.append(it)
        else:
            raise typer.BadParameter(
                f"unknown source {it!r} — choose from: all, copilot-studio, foundry, gcp, demo"
            )
    seen: set[str] = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


_SOURCE_LABEL = {
    "copilot_studio": "Copilot Studio",
    "azure_ai_foundry": "Azure AI Foundry",
    "vertex_ai_agent_engine": "Vertex AI Agent Engine",
    "agentspace": "Agentspace",
    "dialogflow_cx": "Dialogflow CX",
}


def _split_csv(value: str | None) -> list[str] | None:
    """Split a comma-separated CLI value into a clean list (or None if empty)."""
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


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
    source: str = typer.Option(
        "all",
        "--source",
        help=(
            "Connector(s): 'all' (Copilot Studio + Foundry + GCP), 'demo', or a comma list of "
            "copilot-studio, foundry, gcp (e.g. --source foundry,gcp). Unreachable providers are "
            "skipped with a warning."
        ),
    ),
    auth_mode: AuthMode = typer.Option(
        AuthMode.cli,
        "--auth",
        help=(
            "Auth strategy, applied per provider: cli (reuse az / gcloud) · app (Entra app+secret "
            "/ GCP service-account key) · device (Entra device-code, Microsoft only) · adc (Google "
            "ADC)."
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
        help="Entra client secret (Microsoft --auth app); prefer the env var.",
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
    project: str | None = typer.Option(
        None,
        "--project",
        envvar="AGENTCENSUS_PROJECT",
        help="GCP project id(s) to scan, comma-separated (no org auto-enumeration).",
    ),
    location: str | None = typer.Option(
        None,
        "--location",
        envvar="AGENTCENSUS_LOCATION",
        help="GCP region(s) to scan, comma-separated (default: a GA region set + global).",
    ),
    gcp_key_file: str | None = typer.Option(
        None,
        "--gcp-key-file",
        envvar="AGENTCENSUS_GCP_KEY_FILE",
        help="GCP service-account JSON key (Google --auth app).",
    ),
    gcp_impersonate: str | None = typer.Option(
        None,
        "--gcp-impersonate",
        help="GCP service-account email to impersonate (Google --auth cli).",
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
    keys = ["demo"] if demo else _parse_sources(source)

    auth_notes: list[str] = []  # per-provider status, shown in the summary
    skip_warnings: list[str] = []  # providers skipped for lack of credentials

    if keys == ["demo"]:
        discoverer = build_source(["demo"], stale_days=stale_days)
    else:
        strategy = auth_mode.value
        ms_auth = gcp_auth = None

        if any(k in _MS_KEYS for k in keys):
            ms_auth, note = build_microsoft_auth(
                strategy, client_id=client_id, tenant=tenant, client_secret=client_secret
            )
            auth_notes.append(f"Microsoft: {note}")
            if ms_auth is None:
                skip_warnings.append(f"Microsoft connectors skipped — {note}")
        if any(k in _GCP_KEYS for k in keys):
            gcp_auth, note = build_gcp_auth(
                strategy, gcp_key_file=gcp_key_file, gcp_impersonate=gcp_impersonate
            )
            auth_notes.append(f"Google Cloud: {note}")
            if gcp_auth is None:
                skip_warnings.append(f"Google Cloud (gcp) skipped — {note}")

        runnable = [
            k
            for k in keys
            if (k in _MS_KEYS and ms_auth is not None) or (k in _GCP_KEYS and gcp_auth is not None)
        ]
        if not runnable:
            detail = "; ".join(auth_notes) or "no credentials available"
            raise typer.BadParameter(
                f"{detail}. Provide credentials for a selected provider, or run "
                "`agentcensus sweep --demo` to see a sample report."
            )
        discoverer = build_source(
            runnable,
            stale_days=stale_days,
            ms_auth=ms_auth,
            gcp_auth=gcp_auth,
            environment=environment,
            subscription=subscription,
            projects=_split_csv(project),
            locations=_split_csv(location),
        )

    try:
        result = discoverer.scan()
    except DiscoveryError as exc:
        typer.echo(f"discovery failed: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    if skip_warnings:  # surface skipped providers in the report's warnings too
        result.warnings = [*skip_warnings, *result.warnings]

    if out is None:
        ext = "json" if fmt is OutputFormat.json else "html"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = Path("reports") / f"agentcensus-{result.meta.source}-{stamp}.{ext}"

    rendered = render_json(result) if fmt is OutputFormat.json else render_html(result)
    try:
        if out.parent:
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        typer.echo(f"could not write report to {out}: {exc}", err=True)
        raise typer.Exit(code=4) from exc

    if not quiet:
        if auth_notes:
            typer.echo("Auth — " + " · ".join(auth_notes))
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
