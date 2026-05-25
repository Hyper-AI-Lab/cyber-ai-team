# Cyber-Team Backend

FastAPI, Temporal worker, agent orchestration, memory, tool, audit, and integration services for Cyber-Team.

The main project documentation lives in the repository root `README.md`.

## Database Migrations

The backend uses Alembic for PostgreSQL schema migrations.

Run migrations from this directory with:

```bash
alembic upgrade head
```

Create a new migration after model changes with:

```bash
alembic revision --autogenerate -m "describe change"
```

Application startup runs migrations by default. Set `DATABASE_MIGRATIONS_ON_STARTUP=false`
only when another deployment step runs `alembic upgrade head`. The legacy `create_all`
fallback is disabled by default and can be enabled for local experiments with
`DATABASE_CREATE_ALL_FALLBACK=true`.

## Security Defaults

Production startup requires `OWNER_PASSWORD_HASH`; plaintext `OWNER_PASSWORD` is only
for development. Generate a bcrypt hash with:

```bash
python -m cyber_team.cli hash-password
```

The backend issues short-lived one-time WebSocket tickets through
`POST /api/auth/ws-ticket` instead of putting long-lived access tokens in WebSocket
query strings. High-risk actions have in-memory per-minute rate limits configurable
through the `RATE_LIMIT_*` environment variables.
