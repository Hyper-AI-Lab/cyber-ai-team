# Cyber-Team Development Plan

## 1. Project Purpose & Vision
The **Cyber-Team** project aims to build an AI-powered, open-source multi-agent company operating system. The system acts as a digital-AI team for a startup, covering roles like project manager, operations manager, legal advisor, accountant, and more.

Key defining aspects:
- **Proactive and Interactive Agents**: Not merely chatbots, these agents operate autonomously, collaborating on tasks and executing processes within defined constraints.
- **Adaptive Organization**: A "Company Builder" agent dynamically creates and configures new roles based on organizational needs.
- **Persistent Infinite Memory**: Combining short-term graph state, Mem0/Qdrant retrieval memory, and PostgreSQL/ERPNext canonical records, agents possess an illusion of infinite, correct recall.
- **Omni-channel Communication**: Support for external messaging (Asterisk/Jasmin for voice/SMS, SMTP, webhooks).
- **Human Governance**: A dedicated control plane for the founder to monitor, inspect memories, chat, and approve high-stakes agent decisions.

## 2. Current Architecture & State
Based on the `README.md` and `docker-compose.yml`, the infrastructure skeleton is robustly defined:

### Technologies & Infrastructure:
- **Agent Runtime & Orchestration**: LangGraph, Temporal, and CrewAI for defining workflows, roles, state machines, and task recovery.
- **Backend API**: Python-based FastAPI service in `backend/` exposing endpoints for agent execution, role building, and chat.
- **Frontend / Owner Console**: React + Tailwind Next.js application in `frontend/` acting as the control plane for the system.
- **Memory & Storage**: PostgreSQL (system of record), Qdrant (semantic vector DB for Mem0), Redis (caching and pub/sub).
- **Enterprise Integrations**: ERPNext (HR, CRM, accounting), Nextcloud (documents).
- **Governance & Observability**: OpenFGA & OPA (permissions and policies), Keycloak (IAM), Langfuse & Grafana/Prometheus (tracing and metrics).
- **Communications**: Asterisk & Jasmin for telephony and SMS.

### Current Implementation State:
- The foundational **Docker Compose** environment is in place, connecting roughly 15 services.
- Basic scaffolding exists for the **Backend** (`backend/src/cyber_team/` with API routes and agent structure) and **Frontend** Next.js app.
- Theoretical and design research is thoroughly documented in the `request/` folder (GPT and Perplexity reports) validating the open-source stack.

## 3. Development Roadmap: What Should Be Done Next

The repository provides the infrastructure blueprint, but the application layer (agent manifests, workflow graphs, tooling connectors) requires implementation. The execution plan should follow a phased approach:

### Phase 1: Core Orchestration and Role Instantiation
- [ ] **Agent Manager & Orchestrator Implementation**: Develop the core LangGraph state machine in `backend/src/cyber_team/api/workflows.py` and `agents.py`. Define the supervisor and worker node logic.
- [ ] **Company Builder Agent**: Implement the bootstrap logic in `roles/company-builder.py` that takes startup details and creates the YAML/JSON manifests for the initial team.
- [ ] **Role Manifest Schema**: Define the standard schema for roles (goals, allowed tools, memory namespaces). Implement the first core roles: CEO Advisor, Project Manager, and Operations.
- [ ] **Temporal Integration**: Wire up the Temporal worker (`backend/src/cyber_team/worker.py`) to execute long-running LangGraph processes durably.

### Phase 2: Memory Fabric & Systems of Record
- [ ] **Memory Service API**: Implement the Memory Service using `mem0` and `qdrant-client` in `backend/src/cyber_team/memory/`. Establish the separation between Episodic, Semantic, and Entity memories.
- [ ] **ERPNext Connector**: Build tools in `tools/erpnext.py` using `erpnext-client` allowing agents to read and write canonical records (e.g., invoices, CRM entries). Ensure memory retrieval supplements ERP data rather than replacing it.

### Phase 3: Governance, Approvals, and Safety
- [ ] **Human-in-the-Loop Interruption**: Utilize LangGraph's interrupt features to pause workflows pending human approval for sensitive actions (payments, contract signing, production deployments).
- [ ] **OpenFGA & OPA Enforcements**: Integrate the OpenFGA client in API endpoints and agent tool executions to ensure agents only access data and perform actions they are authorized for.

### Phase 4: Omni-Channel Communications
- [ ] **Communication Adapters**: Implement Asterisk (Voice via Pipecat) and Jasmin (SMS) adapters. Expose these as tools for the Sales, Support, and PR agents.
- [ ] **Routing Webhooks**: Add endpoints in `backend/src/cyber_team/api/routes/comms.py` to ingest incoming calls/SMS and route them to the active state graph of the assigned agent.

### Phase 5: Owner Console / Frontend
- [ ] **Dashboard Implementation**: Build out the Next.js frontend to visualize LangGraph workflows, display pending approvals, and show top-level metrics.
- [ ] **Memory Browser & Chat**: Add UI components to let the founder chat directly with agents and inspect their multi-layered memory structures.

### Phase 6: Testing & Iteration
- [ ] **End-to-End Workflows**: Design and execute tests for complete workflows (e.g., Lead generation -> Outreach via SMS -> CRM Update -> Owner Notification).
- [ ] **Deployment Tuning**: Optimize Docker container resource limits and refine the configuration management script (`start.sh`).
