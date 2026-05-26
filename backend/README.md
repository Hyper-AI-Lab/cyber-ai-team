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
for development. Production also refuses to start while
`COMMUNICATIONS_ALLOW_SIMULATION=true`, so real outbound providers must be configured
or communication tools will fail closed instead of pretending to send. Generate a bcrypt
hash with:

```bash
python -m cyber_team.cli hash-password
```

The backend issues short-lived one-time WebSocket tickets through
`POST /api/auth/ws-ticket` instead of putting long-lived access tokens in WebSocket
query strings. High-risk actions have in-memory per-minute rate limits configurable
through the `RATE_LIMIT_*` environment variables.

## Communications

`CommsGateway` reports runtime provider status through `GET /api/integrations/status`.
Twilio is used for voice and SMS when `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
and `TWILIO_PHONE_NUMBER` are set. SMTP is used for email when `SMTP_HOST` and
`SMTP_FROM_EMAIL` are set. Jasmin SMS is used when `JASMIN_USERNAME`,
`JASMIN_PASSWORD`, and `JASMIN_FROM_NUMBER` are set and Twilio SMS is not
configured. Slack incoming webhooks, Telegram Bot API, and Twilio WhatsApp are
runtime messaging providers when their credentials are configured. Asterisk is
still Compose-profile-only; runtime voice calls use Twilio.

Outbound communication tools accept an optional `idempotency_key`. The gateway
reserves that key in `communication_logs` before contacting a provider and stores
the final response for replay, so client retries do not duplicate external sends.
