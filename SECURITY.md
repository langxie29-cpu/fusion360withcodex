# Security Policy

## Supported version

Security fixes are applied to the latest release and the default branch. This project is
currently alpha software and does not promise backwards compatibility before `1.0.0`.

## Threat model

The Fusion Add-In runs inside Fusion and can create or modify geometry. Its MCP server therefore:

- binds only to `127.0.0.1`;
- rejects non-loopback Host and Origin values;
- requires JSON POST requests;
- accepts only typed tools and a strict ModelPlan schema;
- limits request and plan sizes;
- uses expiring, single-use staged plan IDs;
- defaults to creating a new document.

The bridge does not authenticate local operating-system processes. Any process running as the
same user may still connect to the loopback port. Do not run untrusted software in the same user
session while Fusion AI Modeler is enabled.

## Reporting a vulnerability

Use GitHub's **Security > Report a vulnerability** private reporting flow for the repository.
Do not publish an exploitable issue before a fix is available. Include the affected version,
reproduction steps, impact, and any proposed mitigation.
