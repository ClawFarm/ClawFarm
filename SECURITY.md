# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in ClawFarm, please report it responsibly.

**Email:** [security@clawfarm.dev](mailto:security@clawfarm.dev)

Please include:

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Any potential impact you've identified

## What to expect

- **Acknowledgement** within 48 hours
- **Status update** within 7 days with an assessment and remediation timeline
- **Credit** in the release notes (unless you prefer to remain anonymous)

## Scope

ClawFarm manages Docker containers, API keys, user authentication, and network isolation. We take reports seriously for all of these areas, including but not limited to:

- Authentication or authorization bypass
- Container escape or cross-agent access
- API key or credential exposure
- Injection vulnerabilities (command, path traversal, etc.)

## Out of scope

- Vulnerabilities in OpenClaw itself (report to the [OpenClaw project](https://github.com/openclaw/openclaw))
- Vulnerabilities in upstream dependencies (Caddy, Docker, Node.js) unless ClawFarm's usage introduces the issue
- Self-signed certificate warnings in `internal` TLS mode (this is expected behavior)

## Disclosure

We follow coordinated disclosure. Please do not open public issues for security vulnerabilities.
