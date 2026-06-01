# Cyber-Team Autonomous Company OS v2 Development Plan

## 1. Product North Star

Cyber-Team is an adaptive AI company operating system. It should not be a fixed bundle
of demo agents or canned workflows. It should continuously infer what the business needs,
create or activate roles when gaps appear, orchestrate those roles through governed tools,
and preserve company memory so agents can operate with stable long-term context.

The core operating principle is:

- Company context drives roles, tools, memory policy, workflows, and integrations.
- Roles are created from a catalog when possible and generated dynamically when needed.
- Workflows are adaptive operating loops, not hard-coded default business processes.
- Memory is treated as company infrastructure, not incidental chat history.
- The owner can inspect, interrupt, approve, and redirect every important action.

## 2. Current State

The repository already has a working control-plane foundation:

- FastAPI backend with auth, audit log, role catalog, agents, workflows, tools, memory,
  approvals, communications, integrations, and health checks.
- Next.js owner console with dashboard, agents, memory, workflows, chat, approvals,
  integrations, and audit views.
- PostgreSQL, Redis, Qdrant, OPA, Langfuse, Temporal, and related services defined for
  Docker Compose deployment.
- Staging deployment path through Caddy and compose release scripts.
- Seed role manifests covering company builder, supervisor, finance, legal, sales,
  marketing, support, product, engineering, operations, HR, security, knowledge, and
  communications.

The main product gap is that the existing behavior is still too static. It can provision
roles, but it does not yet act as a self-evolving digital organization.

## 3. Target Architecture

### 3.1 Company Builder

The Company Builder is the bootstrap and evolution engine. It receives company context,
builds an operating model, selects immediate roles, defers lower-priority roles, detects
capability gaps, seeds memory, and creates adaptive operating loops.

Responsibilities:

- Normalize company profile and infer business needs.
- Compare needed capabilities against role catalog and tool registry.
- Instantiate catalog roles where available.
- Generate specialized role manifests when no catalog role fits.
- Seed company memory with constitution, role map, operating loops, and gap backlog.
- Keep the role backlog alive for future activation.

### 3.2 Supervisor / Orchestrator

The Supervisor coordinates execution and enforces safety.

Responsibilities:

- Route work to the right role.
- Inspect plans before sensitive actions.
- Detect blocked work, stale tasks, duplicated effort, and conflicting agent outputs.
- Escalate to the owner when authority, risk, or uncertainty requires it.
- Trigger Company Builder when a new role, tool, or skill is needed.

### 3.3 Role Factory

The Role Factory turns needs into executable role manifests.

Responsibilities:

- Prefer existing catalog roles when they fit.
- Generate new specialized roles with clear tools, memory namespace, instructions, metrics,
  approval policy, and activation triggers.
- Require approval for high-risk tool access or broad authority.
- Preserve generated role provenance in manifest config.

### 3.4 Memory Fabric

Memory must create the practical illusion of persistent, reliable context.

Layers:

- Company memory: constitution, goals, operating model, integrations, constraints.
- Role memory: role-specific procedures, active responsibilities, prior decisions.
- Workflow memory: task state, decisions, approvals, outputs, follow-ups.
- Entity memory: customers, vendors, projects, people, deals, documents.
- Canonical records: ERPNext/PostgreSQL records that override memory when facts conflict.

Rules:

- Agents query memory before material actions.
- Completed work writes concise episodic and procedural memories.
- The Memory Steward consolidates stale, duplicated, or conflicting entries.
- Canonical records are never silently overwritten by retrieved memory.

### 3.5 Adaptive Operating Loops

Operating loops are durable policies that create work when conditions appear.

Initial loops:

- Owner alignment loop.
- Role gap monitoring loop.
- Memory consolidation loop.
- Integration discovery loop.
- Risk review loop.
- Customer communication loop when customer channels exist.

Future loops should be created from business context, not installed as static sample
workflows.

### 3.6 Tool and Integration Layer

Tools are governed capabilities. Integrations are activated when the company context or
agent work proves the need.

Priority integration families:

- Communications: email, SMS, phone, messaging.
- CRM and customer records.
- Accounting and invoicing.
- Calendar and scheduling.
- Documents and knowledge base.
- Support desk.
- Analytics and reporting.
- Payments, only behind strict approval and audit controls.

## 4. Implementation Phases

### Phase 1: Dynamic Company Builder Foundation

Status: first foundation slice implemented; remaining Phase 1 work is UI depth,
role-gap persistence, and deployment validation.

Deliverables:

- [x] Deterministic operating-model builder.
- [x] Dynamic role specs and generated role manifests.
- [x] Company memory seed generation.
- [x] Capability gap detection from tool registry and profile signals.
- [x] Owner console builder form for richer company context.
- [x] Focused tests for role inference and builder integration.
- [ ] Persist role-gap events and generated operating-loop state.
- [ ] Add deeper owner-console views for operating model details.
- [ ] Deploy and validate the slice in hosted staging.

Exit criteria:

- Company Builder returns operating model, role specs, role backlog, adaptive loops,
  memory seeds, and capability gaps.
- Dynamic roles such as Memory Steward, Outbound Calling Specialist, Integration
  Architect, Compliance Sentinel, and Growth Experiment Designer are generated only when
  their triggers apply, except Memory Steward which is foundational.
- Existing API response remains backward compatible with `blueprint` and
  `instantiated_agents`.

### Phase 2: Role Gap Runtime Loop

Status: first persistent role-gap slice implemented; remaining work is deeper
Supervisor routing, approval-policy refinement, and loop-driven automatic gap review.

Deliverables:

- [x] Persistent role-gap events.
- [x] Supervisor/agent tool for reporting blocked work.
- [x] Company Builder deterministic proposal for unresolved gaps.
- [x] Owner-console view for pending role gaps and generated role proposals.
- [x] Owner-gated application of generated role proposals.
- [x] Automatic role-gap creation from missing agents, missing tools, unavailable
  integrations, and blocked execution language.
- [ ] Fine-grained approval requests for high-risk generated tool grants.
- [ ] Scheduled Supervisor review of open gaps from workflow failures and blocked tasks.

Exit criteria:

- A failed or blocked task can create a role-gap event.
- The system can propose a role, request owner approval when needed, and instantiate it.

### Phase 3: Memory Protocol and Memory Steward

Deliverables:

- Explicit memory write/read protocol for agent invocation.
- Memory namespace policy per company, role, workflow, and entity.
- Memory consolidation job.
- Conflict detection between memory and canonical records.
- UI memory timeline with provenance and importance.

Exit criteria:

- Agents consistently retrieve relevant context before execution.
- Completed work writes durable summaries.
- Memory conflicts are visible and resolvable.

### Phase 4: Adaptive Workflow Engine

Deliverables:

- Operating loops represented as workflow intents.
- Supervisor-driven routing from intent to agent tasks.
- Durable execution through Temporal where long-running work is needed.
- Human interruption and resume for sensitive steps.
- Workflow templates generated from role capabilities and business context.

Exit criteria:

- A business objective can become a governed multi-agent workflow without a static template.
- Workflow state survives restart and preserves approval history.

### Phase 5: Integration Activation

Deliverables:

- Integration Architect runtime checklist.
- Credential readiness and health checks for each provider.
- Email, calendar, CRM, accounting, docs, support, analytics, SMS, and voice connectors
  activated as the business requires them.
- Simulation mode for every external side effect.
- Idempotency and retries for external writes.

Exit criteria:

- Agents can detect a needed external system, request configuration, validate readiness,
  and use the connector through audited tools.

### Phase 6: Governance and Permissions

Deliverables:

- Policy matrix by role, tool, action type, data class, and environment.
- OpenFGA relationship model for company, owner, agent, role, workflow, and memory access.
- OPA policies for approval requirements and external side effects.
- Red-team tests for tool misuse, prompt injection, and authorization bypass.

Exit criteria:

- High-risk actions cannot execute without approval.
- Agents cannot access out-of-scope memory or tools.

### Phase 7: Owner Console v2

Deliverables:

- Operating model view.
- Role backlog and role-gap inbox.
- Agent trace and decision timeline.
- Memory graph browser.
- Integration readiness board.
- Workflow intent builder and live execution view.

Exit criteria:

- The owner can understand what the AI company is doing, why it is doing it, what it
  remembers, and what it needs next.

### Phase 8: Production Hardening

Deliverables:

- Hosted staging validation with real staging secrets.
- Backup and restore rehearsal.
- Load and soak tests for API, worker, memory, and tool execution.
- Dependency and image scanning in CI.
- Runbooks for deploy, rollback, incident response, provider outages, and memory recovery.

Exit criteria:

- Staging and production promotion have repeatable evidence.
- External effects are observable, idempotent, and recoverable.

## 5. Immediate Execution Plan

1. Implement Phase 1 dynamic Company Builder foundation.
2. Add tests for deterministic operating-model decisions.
3. Add tests for `AgentManager.run_company_builder` dynamic manifest creation and memory
   seeding.
4. Update the owner console Company Builder form and result view.
5. Run focused backend and frontend tests.
6. Deploy the foundation slice to staging after tests pass.

## 6. Current Slice Notes

The current Phase 2 slice turns role gaps into runtime events. Missing agent invocations,
missing tool execution, unavailable integrations, and explicit blocked-work language in
agent/chat responses create de-duplicated `role_gaps` records. The Role Gap Inbox can then
ask Company Builder for a deterministic role proposal and apply or dismiss the proposal.

The next Phase 2 slice should make high-risk generated tool grants create explicit approval
requests before role instantiation, rather than relying only on the existing owner-gated
apply action and per-tool approval policies.
