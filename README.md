# Cyber-Team

AI-powered digital company operating system — a multi-agent platform that instantiates AI specialists (project manager, legal advisor, accountant, PR manager, ops manager, etc.) as a cooperative digital team for running your startup.

## Architecture

Compositional stack built on open-source components:

| Layer | Technology |
|-------|-----------|
| Agent Runtime | LangGraph + CrewAI |
| Durable Workflows | Temporal |
| Memory | 4-layer: Pinned → Workflow → Retrieval (Mem0+Qdrant) → Canonical (PostgreSQL+ERPNext) |
| Interoperability | MCP (agent↔tool), A2A (agent↔agent) |
| Communications | Asterisk (voice), Jasmin (SMS), Twilio, Pipecat |
| System of Record | ERPNext, PostgreSQL |
| Owner Console | React + TailwindCSS |
| Governance | Keycloak, OpenFGA, OPA, Infisical |
| Observability | Langfuse, Prometheus, Grafana |
| LLM Gateway | LiteLLM (Mistral default) |
| Deployment | Docker Compose (~15 containers) |

## Quick Start

### 1. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set MISTRAL_API_KEY
```

### 2. Start

```bash
# Option A: Screen session (recommended)
./start.sh
screen -r cyber-team    # attach

# Option B: Direct
docker compose up --build

# Option C: CLI
pip install -e ./backend
cyber-team start --build
```

> **Remote server?** The start script auto-detects your server IP. If you're running on a remote server and accessing from your PC, use the **remote URLs** shown after startup. You can also set `HOST_IP` manually: `HOST_IP=192.168.1.100 ./start.sh`

### 3. Access

**If running on the same machine** (local):

| Service | URL |
|---------|-----|
| Owner Console | http://localhost:3001 |
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

**If running on a remote server** (from your PC):

| Service | URL |
|---------|-----|
| Owner Console | http://YOUR_SERVER_IP:3001 |
| API | http://YOUR_SERVER_IP:8000 |
| API Docs | http://YOUR_SERVER_IP:8000/docs |
| Grafana | http://YOUR_SERVER_IP:3500 |
| Langfuse | http://YOUR_SERVER_IP:3100 |

> Make sure your firewall allows ports: 3001, 8000, 3500, 3100

Sign in with `OWNER_EMAIL` and `OWNER_PASSWORD` from `.env`. For production, set `ENVIRONMENT=production`, replace all default secrets, set `OWNER_PASSWORD_HASH`, and configure `CORS_ALLOWED_ORIGINS` to the exact console URL instead of `*`.

### 4. Set Up Your Team

1. Open the Owner Console (use your server URL from step 3)
2. Go to **Agents** → **Company Builder**
3. Enter your company name and industry
4. The builder will propose an optimal team structure
5. Review and approve the proposed roles

## Role Families

14 core role families are pre-loaded:

1. **Company Builder** — Sets up initial team based on company profile
2. **Supervisor** — Oversees all agents, resolves conflicts
3. **Finance & Accounting** — Invoices, cash-flow, expenses (approval-gated)
4. **Legal & Policy** — Contracts, NDAs, compliance (approval-gated)
5. **Sales & CRM** — Lead research, outreach, pipeline management
6. **Marketing & PR** — Content, social media, brand monitoring
7. **Customer Support** — Tickets, chat, phone support
8. **Product & Project Management** — Sprints, tasks, coordination
9. **Software Engineering & QA** — Code, CI/CD, testing (approval-gated)
10. **Operations & Procurement** — Day-to-day ops, vendor management
11. **People & HR** — Recruitment, onboarding, HR records
12. **Security & Compliance** — Security posture, access control, incidents
13. **Knowledge & Research** — Knowledge base, market research
14. **Communications** — Email, chat, voice, SMS, messaging

New roles can be created dynamically via the **role-gap workflow** when unmet needs are detected.

## Memory System

4-layer memory stack ensures agents behave as if they have infinite recall:

1. **Pinned Identity Memory** — Role charters, company constitution, approval matrix
2. **Workflow Memory** — LangGraph checkpoints, thread state, pending approvals
3. **Retrieval Memory** — Mem0 + Qdrant for semantic search across episodic/semantic/procedural/entity memories
4. **Canonical Records** — PostgreSQL + ERPNext as source of truth (invoices, contracts, CRM, etc.)

## Approval & Governance

Sensitive actions require human approval:
- Payments and invoices above threshold
- Contract signing
- Production deployments
- External communications to new contacts
- Hiring/termination decisions
- Data deletion

Approval policies are enforced via OPA and managed through the **Approvals** panel in the console.

## Communications

The system supports multiple channels:
- **Voice**: Asterisk PBX + Pipecat for real-time STT-LLM-TTS
- **SMS**: Jasmin gateway or Twilio
- **Email**: SMTP/IMAP integration
- **Messaging**: Telegram, WhatsApp webhooks

## API

Full REST API available at `http://localhost:8000/docs` (Swagger UI).

Key endpoints:
- `POST /api/roles/company-builder` — Generate team blueprint
- `GET /api/agents/` — List all agents
- `POST /api/agents/{id}/invoke` — Invoke an agent
- `POST /api/chat/send` — Chat with agents
- `POST /api/memory/recall` — Search memories
- `GET /api/dashboard/kpis` — Dashboard KPIs
- `GET /api/dashboard/approval-queue` — Pending approvals

## Development

```bash
# Backend
cd backend
pip install -e ".[dev]"
pytest

# Frontend
cd frontend
npm install
npm run dev
```

## Stopping

```bash
# If using screen
screen -S cyber-team -X quit
docker compose down

# To also remove data volumes
docker compose down -v
```

## License

MIT
