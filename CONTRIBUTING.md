# Contributing to Cyber AI Team

Thanks for helping build an open-source AI company operating system.

## Project Principles

Contributions should preserve these constraints:

- FOSS-first and self-hostable by default.
- No paid/SaaS-only dependency as a production-readiness requirement.
- No fake-success paths for missing tools, missing credentials, generated code, or outsourced work.
- High-impact and external side effects must stay approval-gated.
- Owner-visible audit, memory, readiness, and operation traces must not be bypassed.
- Secrets must never be committed, printed in docs, or stored in evidence artifacts.

## Local Setup

```bash
cp .env.example .env
docker compose up --build
```

Backend:

```bash
cd backend
pip install -e ".[dev]"
pytest
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Quality Checks

Run the local gate before proposing changes:

```bash
./scripts/quality-gate.sh
```

For larger changes, also run:

```bash
RUN_MIGRATION_REHEARSAL=1 RUN_COMPOSE_SMOKE=1 ./scripts/quality-gate.sh
```

## Pull Request Checklist

- Explain the problem and why the change is needed.
- Link related issues or docs.
- Include tests for behavior changes.
- Update docs/runbooks when operator behavior changes.
- Confirm secrets are not present.
- Confirm new tools/dependencies comply with the FOSS-first resource policy.
- Describe any approval, audit, readiness, or migration impact.

## Good First Contributions

- Improve runbooks.
- Add integration tests around existing APIs.
- Improve empty/error states in the owner console.
- Add FOSS-compatible provider adapters.
- Improve memory, audit, readiness, or role-backlog evidence.
- Improve docs for deployment, backup, restore, or security.
