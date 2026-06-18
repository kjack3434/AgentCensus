# Security Policy

## Supported versions

AgentCensus is pre-1.0; security fixes land on the latest `0.x` release.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |
| < 0.1   | ❌        |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's **[Report a vulnerability](https://github.com/kjack3434/AgentCensus/security/advisories/new)**
(Security → Advisories) so we can triage and fix before disclosure.

Please include: affected version, steps to reproduce, and the impact. We aim to acknowledge within **3 business
days** and to provide a fix or mitigation timeline after triage.

## Scope

AgentCensus is **read-only** toward your tenant and ships no server. Relevant areas to consider:

- Handling of access tokens / secrets (e.g. accidental logging).
- Output safety of the HTML report (agent-controlled text is injected via `textContent`, and `</` is escaped
  in the embedded JSON, to prevent markup/script injection).
- Dependency vulnerabilities.

If you find a report that leaks data unexpectedly, or any way the tool could write to a tenant, that's
in scope and we'd love to hear about it.
