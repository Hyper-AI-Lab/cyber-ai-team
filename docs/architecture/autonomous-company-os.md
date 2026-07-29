# Cyber-Team Autonomous Company OS Architecture

Cyber-Team is a single-owner, self-hosted company operating system for a digital-first startup. Its purpose is to let AI workers run as much of the company as possible while keeping the human owner able to see, steer, pause, override, and approve large-impact actions.

The v3 operating contract is evidence-to-outcome autonomy. The company must not wait for
the owner to author its business description, objectives, KPIs, role backlog, or routine
work. It continuously discovers evidence, distinguishes fact from hypothesis, builds and
revises a living company model, assigns outcome mandates to specialist agents, executes
authorized work, measures results, and learns. Owner input is reserved for unavailable
facts, preferences, permanent authority boundaries, and high-impact decisions.

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
- **Evidence before assertion:** business claims carry provenance, confidence, sensitivity,
  validity, and epistemic state. Missing data remains `unknown`; generic fallback text is
  never presented as company truth.
- **Outcome mandates, not prompt delegation:** every active specialist owns a durable
  mandate, KPI set, authority envelope, cadence, and work backlog. Agent collaboration is
  expressed through work dependencies and acceptance criteria.
- **Closed-loop adaptation:** objectives, targets, workflows, role manifests, and safe
  action policies are versioned, measured, reflected upon, and retained, revised, stopped,
  or rolled back from observed outcomes.

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

The executive loop coordinates a portfolio; it is not the only proactive loop. Every
domain agent runs the same bounded control cycle within its mandate:

1. Observe new company signals and assigned work.
2. Recall verified claims, relevant hypotheses, workflow state, and operation history.
3. Assess mandate objectives, KPIs, constraints, dependencies, and missing evidence.
4. Propose work with an expected outcome, acceptance tests, confidence, and impact.
5. Validate evidence and request independent critique where policy requires it.
6. Submit a structured action envelope to deterministic authorization policy.
7. Execute through a durable workflow or record an explicit blocked/deferred outcome.
8. Measure actual effects and guardrail changes.
9. Reflect, update memory, and recommend continuation, revision, rollback, or escalation.

## Epistemic Company Intelligence

Cyber-Team maintains a living company model assembled from ERPNext, company files,
inbound email, owner instructions, internal records, authorized repositories, company
websites, and public research. Raw source content is evidence, not an instruction to an
agent. Each normalized claim has one state:

- `verified`: directly supported by a canonical or independently validated source
- `inferred`: supported by evidence but not directly asserted by a canonical source
- `hypothesis`: a testable proposition used to create research or an experiment
- `unknown`: required information that available sources do not establish
- `disputed`: credible sources conflict and resolution work is required
- `superseded`: preserved history replaced by a newer valid claim

Source trust is explicit. Canonical records and owner-locked statements rank above
verified company documents, inbound counterpart messages, public web material, and
model-generated hypotheses. Untrusted text is isolated from system instructions and may
never directly select or execute a tool.

## Strategy And Measurement

The Company Discovery Agent produces versioned descriptions of the business, offerings,
customer segments, value propositions, channels, jurisdictions, resources, constraints,
risks, and unknowns. Domain specialists challenge the parts relevant to their expertise;
the Observer reviews evidence coverage and unresolved disagreement.

The Chief Operating Agent turns the active company model into an objective portfolio,
measurable KPIs, benchmarks, and experiments. KPI formulas use a constrained metric DSL
bound to allowlisted ERPNext and Cyber-Team measurements. Below-threshold strategy may
enter a 30-day Observer-approved probation. During one review cycle an automatically
managed target may move no more than 20 percent. Every revision preserves rationale,
evidence, expected effect, actual outcome, and rollback history.

## Work And Workflow Plane

Every signal has a terminal accounting state: accepted into work, deduplicated, deferred
with a reason and reconsideration time, escalated, or deliberately ignored with evidence.
Business work items declare an objective, accountable agent, dependencies, expected
outcome, acceptance criteria, deadline, authority class, and resource budget.

Agents may create declarative workflow specifications containing triggers, preconditions,
steps, tools, compensations, acceptance tests, and metrics. Specifications are immutable
once activated. Schema validation, cycle detection, tool readiness, policy evaluation,
sandbox execution, and Observer review occur before activation. Temporal owns durable
execution; LLM calls are activities and never part of deterministic workflow code.

## Execution Authority

OPA evaluates a complete action envelope at every execution boundary and fails closed.
Prompts and model output cannot grant authority. Contracts, legal or tax filings,
payments and payroll, credentials or permissions, destructive deletion, and production
deployment always require the owner. Other reversible external action classes may become
autonomous only after seven shadow days, at least ten validated examples, complete hard
policy compliance, an evaluator score of at least 0.80, and no high-severity Observer
finding. Existing financial, daily, recipient, confidence, backup, and consensus limits
still apply.

## Completion Contract

Production-grade completion does not mean pre-implementing every future provider or
regulatory procedure. It means every detected need enters a visible lifecycle as
executable, approval-gated, deferred, configuration-blocked, or outsourcing-required.
There are no generic company facts, fake-success executors, active agents without
mandates, unexplained signals, untracked actions, or silently unavailable capabilities.

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
