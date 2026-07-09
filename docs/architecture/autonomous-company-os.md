# Cyber-Team Autonomous Company OS Architecture

Cyber-Team is a single-owner, self-hosted company operating system for a digital-first startup. Its purpose is to let AI workers run as much of the company as possible while keeping the human owner able to see, steer, pause, override, and approve large-impact actions.

## Operating Principles

- **Autonomous by default:** the Chief Operating Agent should observe the company, decide what matters, delegate work, update records, and execute below-threshold actions without waiting for the owner.
- **Owner-visible and owner-controllable:** every decision, action, benchmark, critique, approval, workflow, and memory write must be visible from the owner console with enough context to take over.
- **Owner-informed by default:** the owner console is the source of truth, while scheduled executive email briefs summarize objectives, KPIs, benchmarks, Observer state, blocked actions, approvals, outsourcing requests, and readiness so the owner does not need to manually poll the cockpit.
- **Large-impact gates:** autonomy is aggressive, but actions above configured financial, customer-visible, irreversible, low-confidence, or unresolved-consensus thresholds require owner approval.
- **FOSS-first resource policy:** new tools, modules, and services must be free and open-source or self-hosted using current infrastructure. Paid or SaaS-only resources are future options, not readiness requirements.
- **No fake success:** unavailable tools, missing credentials, generated-code drafts, and outsourced work must be reported as blocked, proposed, or outsourcing-required rather than successful.
- **ERPNext is canonical business state:** CRM, accounting, project, support, procurement, and business records remain in ERPNext. Cyber-Team reads, summarizes, and acts through governed integrations.
- **Memory is operational infrastructure:** agents do not rely on raw context windows. They recall company memory, write durable summaries, and index their own decision/action graph so future runs can understand what happened.
- **Independent critique:** the Observer Agent is separate from the Chief Operating Agent. It reviews decisions, detects drift or weak evidence, and forces consensus or owner escalation when something is off.

## Executive Control Loop

The Chief Operating Agent runs an executive loop:

1. Observe readiness, ERPNext context, agents, role gaps, plans, workflows, tools, approvals, memory traces, audit evidence, and owner instructions.
2. Recall relevant operation graph and memory entries.
3. Assess active company objectives and KPIs.
4. Refresh or create benchmark observations.
5. Propose actions and estimate impact, confidence, reversibility, and resource policy compliance.
6. Ask the Observer Agent for critique.
7. Execute actions that pass policy and consensus.
8. Create owner approvals or attention items for large-impact or disputed actions.
9. Create outsourcing requests for work too complex or unsafe for internal agents.
10. Send a deduplicated executive brief through the required email channel when the daily owner digest is due.
11. Write reflections, memory entries, audit evidence, and operation graph nodes/edges.

## Chief Operating Agent

The Chief Operating Agent owns day-to-day AI-company operations. It can create internal plans, trigger workflows, seed memory, update low-impact ERPNext records, notify the owner, create tool proposals, and request outsourced implementation work. It cannot hot-load generated code or bypass readiness, resource policy, audit, or approval gates.

The scheduled executive brief is summary-only by design: it reports operating state, watch items, and owner-console links, but it does not embed secrets, raw credentials, or large sensitive payloads.

## Observer Agent

The Observer Agent is read-only. It reviews the Chief Operating Agent's proposed actions, recent decisions, memory coverage, benchmark evidence, and operation graph. It looks for:

- goal drift
- repeated failed actions
- weak or stale evidence
- missing memory context
- unsafe assumptions
- prompt-injection-style instructions
- FOSS/resource-policy violations
- large-impact actions disguised as low-risk work

If the Observer agrees, below-threshold actions may proceed. If it disagrees, Cyber-Team records a consensus discussion. Unresolved disagreement blocks execution and escalates to the owner.

## Operation Graph Memory

Every important operating event becomes a graph node or edge: governor runs, decisions, benchmark results, reflections, workflow runs, tool calls, approvals, owner instructions, Observer reviews, and outsourcing requests. Nodes are tagged by objective, agent, workflow, tool, risk, confidence, impact score, source system, and memory namespace.

The graph is also indexed into memory. This lets agents ask questions such as "what happened last time we tried to fix onboarding?", "which workflows repeatedly fail?", or "why did the Observer block this action?" without scanning raw database tables.

## Benchmarks and KPIs

Benchmarks are not static dashboards only. The system maintains core benchmarks for readiness, owner-attention SLA, memory coverage, workflow reliability, role backlog, ERPNext freshness, tool readiness, and agent productivity. The Chief Operating Agent can propose additional benchmarks when it sees a new recurring business process or failure pattern.

Benchmark activation must be auditable and FOSS-compatible. Failed or stale benchmarks become decision inputs for the executive loop.

## Outsourcing Loop

When a task is too complex for internal agents, especially complex code/tool implementation, the system creates an outsourcing request instead of pretending it can complete the task. The request includes a redacted context pack, task specification, relevant files/modules, FOSS constraints, security constraints, acceptance tests, expected artifact, and replay instructions for the owner or an external standalone coding tool.

The owner can resolve the request after supplying an artifact or decision. Cyber-Team verifies acceptance criteria before treating the work as complete.

## Autonomy Policy Defaults

Default aggressive-autonomy thresholds:

- financial exposure: approval above USD 500 per action
- daily financial exposure: approval above USD 2,000 per day
- customer-visible bulk actions: approval above 25 recipients per day
- model/decision confidence: approval below 0.72
- irreversible mutation: approval unless a fresh backup/evidence exists
- Observer unresolved objection: approval required
- stale memory or benchmark evidence: approval or remediation required

These values are configurable in environment settings and the owner console.

## Resource Policy

All new dependencies, tool proposals, and integration proposals must declare:

- license
- cost model
- self-hostability
- hosted-service dependency
- data-sharing risk
- free-tier limitations

Readiness treats paid/SaaS-only proposals as non-blocking future work unless the owner explicitly changes policy after the company can afford them.
