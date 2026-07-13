# GitHub Discoverability Notes

This document records the public-repository visibility work for Cyber AI Team.

## Research Summary

- GitHub recommends a README that explains what the project does, why it is useful, how to start, where to get help, and who maintains it.
- GitHub topics are a first-class discovery mechanism: they appear on the repository page and topic pages help people find projects in a subject area.
- GitHub supports repository social preview images, but the image must be uploaded through repository settings. GitHub recommends at least 640x320 pixels and 1280x640 for best display.
- The market positioning is timely: agentic AI is already being adopted broadly in enterprises, and the one-person-company narrative is visible in current AI-agent coverage.

## Positioning

Cyber AI Team should be described as:

> A self-hosted AI company operating system for solo founders, one-person companies, and small digital startups.

Supporting phrases:

- autonomous AI company OS
- AI workers for solo founders
- ERPNext-backed agent memory and business operations
- owner-visible autonomous agents
- FOSS-first multi-agent startup operations
- human-in-the-loop executive agent

## GitHub Topics

Recommended topic set:

- `ai-agents`
- `ai-agent`
- `agentic-ai`
- `autonomous-agents`
- `multi-agent-system`
- `multi-agent`
- `company-os`
- `one-person-company`
- `solo-founder`
- `startup-automation`
- `digital-workers`
- `llm`
- `rag`
- `mcp`
- `a2a`
- `langgraph`
- `crewai`
- `erpnext`
- `temporal`
- `qdrant`

This mixes high-volume discovery topics with narrow intent topics. Broad terms help general AI-agent discovery; narrow terms help the project stand out for solo founders and company-operations searches.

## Public-Facing Improvements Completed

- Rewrote the root README around the one-person-company and autonomous AI company OS problem.
- Added GitHub badges for CI, license, self-hosting, and FOSS-first policy.
- Added a social preview asset under `docs/assets/`.
- Added an MIT `LICENSE` file so GitHub can detect the license.
- Added contribution, security, code-of-conduct, issue template, and pull request template files.
- Added repository metadata and topics through GitHub.

## Manual Follow-Up

GitHub does not currently expose a normal `gh repo edit` flag for social preview upload. Upload the generated PNG manually:

1. Open repository **Settings**.
2. Scroll to **Social preview**.
3. Upload `docs/assets/cyber-ai-team-social-preview.png`.

## Outreach Ideas

- Submit the project to AI-agent awesome lists after the README has stabilized.
- Write a short technical post around "building a self-hosted AI company OS with ERPNext, memory, and owner-visible autonomy."
- Tag future releases with clear changelogs: `governor`, `observer`, `erpnext`, `memory`, `role-backlog`, `readiness`.
- Keep issues beginner-friendly by labeling docs, frontend, integration, and tests work.
