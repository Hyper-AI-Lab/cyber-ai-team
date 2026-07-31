# Cyber-Team Autonomous Company OS v3 Development Plan

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

The repository now has a working production-shaped staging foundation:

- FastAPI backend with auth, audit log, role catalog, agents, workflows, tools, memory,
  approvals, communications, integrations, and health checks.
- Next.js owner console with dashboard, agents, memory, workflows, chat, approvals,
  integrations, audit, operations readiness, role backlog, and executive-governor views.
- PostgreSQL, Redis, Qdrant, OPA, Langfuse, Temporal, and related services defined for
  Docker Compose deployment.
- ERPNext running as the canonical CRM/accounting/project/support/procurement system of
  record for staging.
- Staging deployment path through Caddy, release manifests, promotion records, backup
  artifacts, smoke tests, image scans, and restart helpers.
- Seed role manifests covering company builder, supervisor, finance, legal, sales,
  marketing, support, product, engineering, operations, HR, security, knowledge, and
  communications.
- Company-context sync, ERPNext drift detection, role backlog review, readiness evidence,
  Chief Operating Agent, Observer Agent, operation graph, benchmark/reflection records,
  outsourcing requests, and FOSS-first resource policy are implemented at v1/v2
  production-readiness depth.

Autonomous Company Operations v3 is implemented and accepted in staging. The immutable
`0.3.0-rc3` candidate passed the complete release gate, public push and manual CI, live
smokes, and an uninterrupted 24-hour soak with 289 successful samples and no failures.
The next operating phase is evidence-driven canary expansion: Knowledge/Research first,
then other internal domains, while permanent legal, financial, credential, destructive,
deployment, and high-impact customer gates remain in force.

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

Status: completed as the first production foundation. Ongoing improvements now belong to
role-backlog, governor, and owner-console phases.

Deliverables:

- [x] Deterministic operating-model builder.
- [x] Dynamic role specs and generated role manifests.
- [x] Company memory seed generation.
- [x] Capability gap detection from tool registry and profile signals.
- [x] Owner console builder form for richer company context.
- [x] Focused tests for role inference and builder integration.
- [x] Persist role-gap events and generated operating-loop state.
- [x] Add deeper owner-console views for operating model details.
- [x] Deploy and validate the slice in hosted staging.

Exit criteria:

- Company Builder returns operating model, role specs, role backlog, adaptive loops,
  memory seeds, and capability gaps.
- Dynamic roles such as Memory Steward, Outbound Calling Specialist, Integration
  Architect, Compliance Sentinel, and Growth Experiment Designer are generated only when
  their triggers apply, except Memory Steward which is foundational.
- Existing API response remains backward compatible with `blueprint` and
  `instantiated_agents`.

### Phase 2: Role Gap Runtime Loop

Status: production v1 complete. The role backlog now supports summary grouping,
traceability, approval regeneration, setup guidance, batch-safe UI actions, staging
application of low-risk roles, and explicit blocking for optional provider configuration.

Deliverables:

- [x] Persistent role-gap events.
- [x] Supervisor/agent tool for reporting blocked work.
- [x] Company Builder deterministic proposal for unresolved gaps.
- [x] Owner-console view for pending role gaps and generated role proposals.
- [x] Owner-gated application of generated role proposals.
- [x] Automatic role-gap creation from missing agents, missing tools, unavailable
  integrations, and blocked execution language.
- [x] Fine-grained approval requests for high-risk generated tool grants.
- [x] Scheduled Supervisor review of open/proposed role gaps, stale approvals, and repeated
  workflow failures.

Exit criteria:

- A failed or blocked task can create a role-gap event.
- The system can propose a role, request owner approval when needed, and instantiate it.

### Phase 3: Memory Protocol and Memory Steward

Status: production foundation implemented. Memory protocol, traces, steward findings,
operation-graph indexing, and ERPNext/company-context canonical conflict detection are
present. Remaining work is richer graph/timeline exploration and deeper
benchmark-driven memory-quality remediation.

Deliverables:

- [x] Explicit memory write/read protocol for agent invocation.
- [x] Memory namespace policy per company, role, workflow, and entity.
- [x] Memory consolidation/steward findings foundation.
- [x] Conflict detection workflow between memory and canonical records.
- [x] UI memory timeline with provenance and importance foundation.

Exit criteria:

- Agents consistently retrieve relevant context before execution.
- Completed work writes durable summaries.
- Memory conflicts are visible and resolvable.

### Phase 4: Adaptive Workflow Engine

Status: production v3 foundation. Autonomous plans, durable work items, business events,
agent mandates, Temporal schedules/workflows, dynamic workflow specifications, owner
approvals, ERPNext/company-context workflows, and generated intents from role capabilities
and business evidence are implemented. Optional calendar/docs/analytics signals activate
only when company evidence and configured integrations justify them.

Deliverables:

- [x] Operating loops represented as autonomous plans/cadence tasks.
- [x] Supervisor/governor-driven routing from signals to plan tasks.
- [x] Durable execution through Temporal where long-running work is needed.
- [x] Human interruption and resume for sensitive steps.
- [x] Workflow templates generated broadly from role capabilities and business context.

Exit criteria:

- A business objective can become a governed multi-agent workflow without a static template.
- Workflow state survives restart and preserves approval history.

### Phase 5: Integration Activation

Status: ERPNext plus email are live in staging. SMS, voice, WhatsApp, Slack, and Telegram
remain optional-disabled/configuration-required until the business needs them. The current
policy is to show unavailable providers clearly without degrading readiness.

Deliverables:

- [x] Integration Architect runtime checklist foundation.
- [x] Credential readiness and health checks for each provider.
- [x] Email, CRM, accounting, project, support, procurement, and ERPNext-backed business
  connectors activated for staging.
- [ ] Calendar, docs, analytics, SMS, voice, and messaging connectors activated only when
  explicitly required and configured.
- [x] Simulation/configuration-required modes for external side effects.
- [x] Idempotency and retries for external writes.

Exit criteria:

- Agents can detect a needed external system, request configuration, validate readiness,
  and use the connector through audited tools.

### Phase 6: Governance and Permissions

Status: production safety foundation implemented for the single-owner model. Approval
target matching, expiry, consumed-state protection, policy-gated side effects, permanent
action gates, audit evidence, prompt-injection quarantine, Observer review, and
FOSS/resource policy checks are in place. Reversible external action classes remain in
shadow probation until their evidence thresholds pass. OpenFGA/Keycloak remain optional
profiles rather than required single-owner staging blockers.

Deliverables:

- [x] Policy matrix by role, tool, action type, data class, and environment.
- [ ] OpenFGA relationship model for company, owner, agent, role, workflow, and memory
  access as a production-required path.
- [x] OPA/local policies for approval requirements and external side effects.
- [x] Red-team tests for tool misuse, prompt injection, and authorization bypass.

Exit criteria:

- High-risk actions cannot execute without approval.
- Agents cannot access out-of-scope memory or tools.

### Phase 7: Owner Console v2

Status: production foundation implemented. The owner can inspect dashboard state, agents,
memory, workflows, chat, approvals, integrations, audit events, readiness, company
context, role backlog, governor/observer activity, objectives, benchmarks, outsourcing
requests, and the executive operating cadence that ties scheduled governor, Observer,
owner-attention, operating-loop, and daily-brief work together.

Deliverables:

- [x] Operating model view.
- [x] Role backlog and role-gap inbox.
- [x] Agent trace and decision timeline foundation.
- [x] Memory graph/timeline foundation.
- [x] Integration readiness board.
- [x] Workflow intent builder and live execution view.

Exit criteria:

- The owner can understand what the AI company is doing, why it is doing it, what it
  remembers, and what it needs next.

### Phase 8: Production Hardening

Status: staging production-readiness acceptance complete. GitHub CI, local release gate,
staging deploy, smoke, backup/restore evidence, load/business workflow smokes, credential
inventory, alert proof, image scanning, restart helper, runbooks, and a strict 24-hour
soak have passed. Production cutover remains intentionally out of scope until real
production secrets and a formal owner approval ceremony are supplied.

Deliverables:

- [x] Hosted staging validation with real staging secrets.
- [x] Backup and restore rehearsal.
- [x] Conservative load and business workflow smoke tests for staging.
- [x] Dependency and image scanning in CI/release gates.
- [x] Runbooks for deploy, rollback, provider outages, data retention, ERPNext, and
  backup/restore.

Exit criteria:

- Staging and production promotion have repeatable evidence.
- External effects are observable, idempotent, and recoverable.

## 5. Immediate Execution Plan

1. Publish the soak-accepted runtime as stable `v0.3.0` using the same v3 code path and
   repeat the immutable release, migration, scan, promotion, and live-smoke gates.
2. Keep staging pinned to its latest promotion record with
   `START_STAGING_DRY_RUN=0 ./scripts/start-staging-current.sh`.
3. Start a policy-gated Knowledge/Research canary and collect mandate, work-item,
   Observer, outcome, and operation-graph evidence.
4. Expand to other internal-only domains after canary evidence is healthy; do not relax
   permanent action gates.
5. Leave optional communications/provider gaps disabled until business evidence makes
   them necessary and credentials are deliberately configured.
6. Treat production promotion as a separate owner-approved operational ceremony.

## 6. Current Slice Notes

The current live staging slice is the ERPNext-backed Autonomous Company Operations v3
foundation. It includes epistemic company intelligence, evidence acquisition, strategy
and KPI revisioning, durable mandates/work portfolios, dynamic workflow compilation,
policy-gated action envelopes, outcome learning, capability expansion, and the
Governor/Observer executive loop.

The app was intentionally stopped for several days and restarted on 2026-07-21. Direct
Compose restart initially exposed a restart drift risk because Compose defaults can fall
back to `latest` images and `BUILD_SHA=local`; `scripts/start-staging-current.sh` now reads
the latest promotion record and starts the staging/ERP stack with the exact promoted image
tags and build metadata.

Executive Operating Cadence v1 is deployed and verified in staging. `GET
/api/operations/executive-cadence` consolidates scheduler runtime state, durable audit
history, latest executive runs, Observer reviews, brief cooldown/idempotency, and
low-risk remediation counts. The Owner Console Executive Cockpit renders those loops as
an operating-cadence history panel.

The v3 release candidate completed its strict 24-hour staging soak on 2026-07-30 with
289/289 successful samples, zero failures, 75.17 ms mean latency, and 600.9 ms maximum
latency. The active milestone is stable-release publication followed by the
Knowledge/Research canary. No customer-visible, financial, legal, credential,
destructive, or deployment authority is widened by that canary.
