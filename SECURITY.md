# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.3.x   | Yes                |
| < 0.3   | No                 |

## Scope

This policy covers the open source **Cohort core framework**:

- Agent orchestration engine
- MCP server (lite and full modes)
- CLI tools
- Local LLM integration
- File-based storage backends

The proprietary Cohort web application (dashboard, real-time UI, agent distribution API) has a separate security process.

## Reporting a Vulnerability

If you discover a security vulnerability in Cohort, please report it responsibly:

1. **Email**: Send details to the repository owner via GitHub (do not open a public issue)
2. **Include**: Description of the vulnerability, steps to reproduce, and potential impact
3. **Response time**: We aim to acknowledge reports within 48 hours and provide a fix timeline within 7 days

## What to Report

- Authentication or authorization bypasses
- Injection vulnerabilities (command injection, path traversal)
- Sensitive data exposure in logs or error messages
- Dependency vulnerabilities with a known exploit path
- MCP tool permission escalation

## What NOT to Report

- Vulnerabilities in dependencies without a proof of concept
- Issues that require physical access to the host machine
- Social engineering attacks
- Denial of service via resource exhaustion (single-user tool)

## Disclosure

We follow coordinated disclosure. Once a fix is available, we will:

1. Release a patched version
2. Publish a security advisory via GitHub
3. Credit the reporter (unless anonymity is requested)
