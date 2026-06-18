<!-- Badge: update kjack3434/AgentCensus if the repo slug changes. -->
[![CI](https://github.com/kjack3434/AgentCensus/actions/workflows/ci.yml/badge.svg)](https://github.com/kjack3434/AgentCensus/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

# AgentCensus

**A simple, ad-hoc view of your Microsoft AI agent estate — run it when you need it.**

AgentCensus pulls your agents from across **Microsoft Copilot Studio** and **Azure AI Foundry** and combines
the scattered details — models, tools, knowledge sources, owners, channels, posture, and governance flags —
into one self-contained HTML report, so you can review the whole estate at a glance instead of clicking
through portals. It also surfaces a few governance risks (public/unauthenticated bots, autonomous actions
without approval, ungoverned models, broad exposure). The result is one file you can open, share, or attach
to a ticket — no database, no server, nothing to set up but Python.

> **Early & evolving.** This is a lightweight, ad-hoc snapshot — not a full governance platform. Deeper checks
> and **wider providers** (beyond Microsoft) are in the works.

> **Read-only & private.** AgentCensus only *reads* discovery metadata — it never creates, modifies, or deletes
> anything in your tenant. It sends **no telemetry**; it talks only to Microsoft's own APIs.

## Contents

- [Quick start](#quick-start-no-account-needed)
- [Sample report](#sample-report)
- [What it finds](#what-it-finds)
- [Discover your live agents (3 ways)](#discover-your-live-agents)
- [Sources](#sources)
- [Command reference](#command-reference)
- [Output](#output)
- [Findings](#findings)
- [Use it in CI](#use-it-in-ci)
- [Troubleshooting](#troubleshooting)
- [Security](#security) · [Contributing](#contributing) · [License](#license)

## Quick start (no account needed)

**New to tools like this?** You need three free tools first — install whichever you don't already have
(one-time):

| Tool | What it's for | Install |
|---|---|---|
| **Git** | downloads the code | [git-scm.com/downloads](https://git-scm.com/downloads) |
| **Python 3.12+** | runs AgentCensus | [python.org/downloads](https://www.python.org/downloads/) |
| **uv** | installs dependencies & runs it in one step | [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |

Then open a terminal (Terminal on macOS, PowerShell on Windows) and copy-paste these three steps:

```bash
# 1 — download the code
git clone https://github.com/kjack3434/AgentCensus.git
cd AgentCensus

# 2 — install dependencies
uv sync

# 3 — open a sample report (synthetic data — no Azure account, no sign-in)
uv run agentcensus sweep --demo --open
```

Step 3 opens an interactive HTML report built from a **synthetic sample estate** (~13 fictional agents). If
that worked, you're ready to point it at your real tenant — see
[Discover your live agents](#discover-your-live-agents).

<details>
<summary>Prefer plain <code>pip</code> instead of uv?</summary>

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
agentcensus sweep --demo --open
```
</details>

> The examples below use `uv run agentcensus …` (works from the cloned folder after `uv sync`). If you used the
> `pip` route above with the venv activated, drop the `uv run` prefix and just type `agentcensus …`.

## Sample report

This is the `--demo` report (synthetic data). A ready-to-open copy is committed at
[`examples/sample-report.html`](examples/sample-report.html) — download it and open in any browser.

![AgentCensus demo — search, sort, and expand agents](examples/demo.gif)

Each agent expands to its full configuration and findings, each with a one-line remediation:

![AgentCensus report — expanded agent detail](examples/report-detail.png)

## What it finds

Each agent is normalized to a flat record: name, source, kind, model (+ tier), instructions, tools, knowledge
sources, guardrails, owners, channels, status, lifecycle dates, and posture flags (autonomous,
shared-with-everyone, no-auth-required, multi-tenant). On top of that, governance rules flag risk — see
[Findings](#findings).

---

## Discover your live agents

Three ways to connect, **easiest first**. All are **read-only**. Pick the row that fits you:

| Option | Best for | App registration | You provide |
|---|---|---|---|
| **1 · Azure CLI** (`--auth cli`) | a quick personal one-off | **none** | just `az login` |
| **2 · Device code** (`--auth device`, default) | individuals, repeatable | one **public-client** app | `--client-id` |
| **3 · Service principal** (`--auth app`) | CI / automation / org-wide | one **app + secret** | `--client-id --tenant --client-secret` |

Whichever you choose, the **identity** doing the discovery (you, or the service principal) also needs
read access to the data — see [What the identity needs to read](#what-the-identity-needs-to-read). Run all the
commands below from the `AgentCensus` folder you cloned in [Quick start](#quick-start-no-account-needed).

### Option 1 — Easiest: reuse your Azure sign-in (no app registration)

The fastest way to see your *real* agents. If you can run `az login`, AgentCensus borrows that session —
nothing to register, no admin help needed.

From the `AgentCensus` folder you cloned in [Quick start](#quick-start-no-account-needed), copy-paste:

```bash
# 1 — sign in to Azure (opens your browser)
az login

# 2 — run the sweep and open the report
uv run agentcensus sweep --auth cli --open
```

**Don't have the Azure CLI yet?** Install it once, then run the two commands above:

- [**Install the Azure CLI**](https://learn.microsoft.com/cli/azure/install-azure-cli) — Windows, macOS, or Linux.
- Signing in to a specific tenant: `az login --tenant <tenant-id>`.

Good to know:
- **App registration:** none — the Azure CLI signs you in.
- **Permissions:** your account just needs **read** access to the agents — see
  [What the identity needs to read](#what-the-identity-needs-to-read).
- **If Copilot Studio shows an auth error** (some tenants restrict which apps may call Dataverse), use
  **Option 2** for that part — Foundry still works fine via the CLI. With `--source all`, a failing connector
  is skipped and noted in the report; everything else still completes.

### Option 2 — Sign in as yourself (device code)

Repeatable interactive sign-in; acts as you. This is the default `--auth`.

```bash
uv run agentcensus sweep --client-id <app-client-id> --open
# you'll be prompted to open https://microsoft.com/devicelogin and enter a code
```

**One-time app registration (public client):**

1. **Entra admin center → App registrations → New registration.** Name it (e.g. `agentcensus`). Register.
2. **Authentication → Advanced settings → Allow public client flows → Yes.** (Enables device code; no
   redirect URI needed.)
3. **API permissions → Add a permission → Delegated:**

   | API | Delegated permission | For |
   |---|---|---|
   | Dynamics CRM | `user_impersonation` | Copilot Studio (Dataverse) |
   | Azure Service Management | `user_impersonation` | Foundry (control plane) |
   | *(Azure AI data plane `https://ai.azure.com`)* | consented at first run | Foundry (agents) |

4. Copy the **Application (client) ID** → pass as `--client-id` (or set `AGENTCENSUS_CLIENT_ID`).

Plus the read roles in [What the identity needs to read](#what-the-identity-needs-to-read) on your account.

### Option 3 — Service principal (CI / automation / org-wide)

No interactive sign-in; runs as the app itself. Best for pipelines and unattended, org-wide sweeps.

```bash
export AGENTCENSUS_CLIENT_SECRET='…'        # keep the secret out of shell history
uv run agentcensus sweep --auth app \
  --tenant <tenant-id> --client-id <app-id> \
  --source all --out report.html
```

**One-time setup (service principal):**

1. **Certificates & secrets → New client secret** on the app registration. Store the value as
   `AGENTCENSUS_CLIENT_SECRET`.
2. **Copilot Studio (Dataverse S2S):** for each environment,
   **Power Platform admin center → Environment → Settings → Users + permissions → Application users →
   New app user**, add the app, and assign a **read** security role on the bot tables.
3. **Foundry:** assign the service principal Azure RBAC **Reader** on the subscription(s) **and** a read role
   on the Foundry project(s)/account(s).

### What the identity needs to read

Regardless of auth option, the account or service principal must be able to *read* the agents:

| Ecosystem | Read access required |
|---|---|
| **Copilot Studio** (Dataverse) | A Power Platform security role that can **read** the `bot` and `botcomponent` tables in each environment (admins see everything; or use a custom read-only role). |
| **Azure AI Foundry** | Azure RBAC **Reader** on the subscription(s), plus a read role on the Foundry project/account (e.g. **Azure AI User**) so agents can be listed. |

---

## Sources

```bash
uv run agentcensus sweep --demo                                  # synthetic sample (no auth)
uv run agentcensus sweep --source all          --client-id <id>  # Copilot Studio + Foundry (default)
uv run agentcensus sweep --source copilot-studio --client-id <id> --environment <env>
uv run agentcensus sweep --source foundry      --client-id <id> --subscription <azure-sub>
```

`all` merges every live connector into one report and skips any that fail (with a warning), so partial access
still produces a useful inventory.

## Command reference

```
agentcensus sweep [OPTIONS]

  --demo                       Use the bundled synthetic estate (implies --source demo).
  --source [all|copilot-studio|foundry|demo]   Discovery source (default: all).
  --auth   [device|app|cli]    device (sign in as you) · app (service principal) · cli (reuse az login).
  -o, --out PATH               Output file (default: reports/agentcensus-<source>-<timestamp>.html).
  -f, --format [html|json]     Output format (default: html).
  --stale-days INTEGER         Flag agents not modified in this many days (default: 90).
  --client-id TEXT             Entra app registration client id (device/app).  [env: AGENTCENSUS_CLIENT_ID]
  --tenant TEXT                Entra tenant id (app auth).                      [env: AGENTCENSUS_TENANT]
  --client-secret TEXT         Client secret (app auth).                        [env: AGENTCENSUS_CLIENT_SECRET]
  --environment TEXT           Limit Copilot Studio to one environment.         [env: AGENTCENSUS_ENVIRONMENT]
  --subscription TEXT          Limit Foundry to one Azure subscription.         [env: AGENTCENSUS_SUBSCRIPTION]
  --open                       Open the report in your browser when done.
  --fail-on [critical|high|medium|low|info]   Exit non-zero if a finding is at/above this severity.
  -q, --quiet                  Suppress the terminal summary.

uv run agentcensus version     # print the version
uv run agentcensus schema      # print the JSON schema of the report
```

## Output

- **HTML (default):** one self-contained file — embedded CSS + JS, **no external requests**, no build step.
  Summary cards, a findings-by-severity bar, and a **searchable / sortable / filterable** agent table; click
  any row to expand full detail. Reports are written to `reports/` (gitignored), timestamped per run.
- **JSON** (`-f json`): the full `SweepResult` for piping into other tools (`uv run agentcensus schema` prints its
  JSON Schema).
- **Terminal summary:** a one-line tally prints after each run (silence with `--quiet`). Install the optional
  `rich` extra (`uv sync --extra rich`) for a colorized summary.

## Findings

| ID | Title | Severity |
|----|-------|----------|
| SWEEP-001 | Public, unauthenticated agent | **critical** |
| SWEEP-004 | Autonomous without human-in-the-loop | high |
| SWEEP-005 | Write-capable tool without approval | high |
| SWEEP-006 | Uses external MCP server | medium |
| SWEEP-007 | Experimental/preview/unknown model | medium |
| SWEEP-008 | Broad channel exposure | medium |
| SWEEP-009 | Empty or placeholder instructions | low |
| SWEEP-010 | Stale agent (`--stale-days`) | low |

Each finding carries a one-line remediation in the report. Agents are also bucketed into a category:
`autonomous`, `customer_facing`, or `internal`.

> **Coverage:** content-safety / RAI is applied by default, and its real posture (plus DLP) is governed via
> **Microsoft Purview** (DSPM for AI) and Azure AI Content Safety — not exposed by the discovery APIs.
> Verified ownership is also often not harvestable. AgentCensus shows these gaps rather than scoring them, so
> the absence of a guardrail or owner is never itself a finding.

## Use it in CI

```bash
uv run agentcensus sweep --auth app --source all \
  --tenant "$TENANT" --client-id "$CLIENT_ID" \
  --format json -o agents.json --fail-on high
```

Writes the report **and** fails the build if any high/critical finding exists. Exit codes: `0` ok ·
`1` `--fail-on` tripped · `2` bad parameters · `3` discovery/auth error · `4` could not write the report.

## Troubleshooting

- **`live discovery needs --client-id`** — you ran a live source (`--auth device`/`app`) without an app
  registration id. Pass `--client-id`, switch to `--auth cli`, or use `--demo`.
- **`app auth requires --tenant and --client-secret`** — `--auth app` needs both; set
  `AGENTCENSUS_CLIENT_SECRET` and pass `--tenant`.
- **`Azure CLI ('az') not found`** / **`az token acquisition failed`** — `--auth cli` needs the Azure CLI
  installed and `az login` completed (try `az login --tenant <id>`).
- **Signed in but zero agents discovered** — the identity lacks read access; grant the roles in
  [What the identity needs to read](#what-the-identity-needs-to-read). The report's warnings name the resource.
- **`access denied (403)` for one environment/subscription** — that resource is skipped and noted in the
  report warnings; the rest still completes. Add the missing read role.
- **Copilot Studio fails under `--auth cli` but Foundry works** — some tenants restrict which clients may call
  Dataverse; use `--auth device` (with an app registration) for Copilot Studio.
- **One ecosystem fails under `--source all`** — `all` skips the failing connector and reports the other; see
  the warnings in the report.

## Security

AgentCensus is read-only and sends no telemetry. Reports contain agent metadata (names, instructions, owners) —
treat `report.html`/JSON as sensitive; generated `reports/` are gitignored so you don't commit one by accident.
To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Contributing

Issues and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and our
[Code of Conduct](CODE_OF_CONDUCT.md). Feature requests and feedback:
[open an issue](https://github.com/kjack3434/AgentCensus/issues).

## License

[MIT](LICENSE).
