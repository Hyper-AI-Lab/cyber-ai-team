# Autonomy Outcome Closure Plan

## Purpose

This plan closes the verified gap between Cyber-Team's implemented control plane and the
original autonomous-company vision. The target is not a system that merely records agent
recommendations or keeps a readiness dashboard green. The target is a system in which
company evidence becomes bounded specialist work, authorized actions, measured outcomes,
and durable learning while the owner retains complete visibility and permanent control of
large-impact decisions.

The existing architecture remains authoritative: ERPNext is canonical business state,
PostgreSQL stores control state, Qdrant supports retrieval memory, Temporal owns durable
execution, OPA fails closed at action boundaries, the Chief Operating Agent manages the
portfolio, and the Observer independently critiques decisions. This milestone completes
the missing paths between those components.

## Verified Baseline Gaps

1. Evidence extraction may send inputs larger than the retained local model context,
   processes many signals sequentially while holding database state, and shares one
   inference slot with executive and domain reasoning.
2. Provider reachability is reported as model readiness without task-level evidence that
   the model can perform discovery, strategy, domain assessment, Observer critique, and
   tool-selection contracts.
3. Domain agents can create advisory work but cannot produce a typed, policy-mediated
   action candidate that becomes durable tool work.
4. The outcome assessor repeatedly selects already-assessed terminal work, leaving the
   remaining outcome backlog invisible and unprocessed.
5. Informational audit telemetry enters the business-event plane and creates high-volume
   no-action traffic.
6. The existing 24-hour soak proves health, login, release identity, and aggregate
   readiness, but not autonomous business behavior or outcome learning.

## Execution Contract

The stages below are executed in order. A stage is complete only after its focused tests
and append-only progress evidence pass. Later work may not weaken an earlier safety gate
to obtain a green test.

### Stage 1: Outcome Learning Integrity

- Select only terminal work that has no outcome assessment.
- Claim assessment work safely across concurrent cycles and advance through the complete
  backlog without starvation.
- Expose total, assessed, unassessed, stale, and oldest-unassessed measurements.
- Make a stale or growing outcome backlog an autonomy-readiness blocker.
- Preserve idempotency, operation-graph links, reflections, policy validation evidence,
  and outsourcing remediation.

Acceptance: newly completed work is assessed once, a backlog larger than one batch drains
across repeated calls, and readiness cannot report outcome learning as healthy while
terminal work is stale and unassessed.

### Stage 2: Bounded Evidence Intelligence

- Build allowlisted, signal-type-specific extraction envelopes for email and research.
- Enforce character and estimated-token budgets below the configured model context.
- Claim signals in a short transaction, perform inference outside database locks, and
  finalize results in a separate transaction.
- Add leases, retry scheduling, exponential backoff, and abandoned-lease recovery.
- Use strict structured-output schemas supported by the active provider.
- Separate evidence-processing health from global API availability. Low-trust evidence
  failure becomes explicit epistemic degradation; required-source backlog or evidence
  needed by an authorized decision remains blocking.

Acceptance: oversized evidence cannot reach inference, one slow signal cannot hold a
database transaction or starve unrelated work, and every signal reaches a supported,
insufficient, quarantined, deferred, or retry-scheduled state.

### Stage 3: Cognitive Capability Readiness

- Add durable model-capability evaluation records keyed by provider, model, prompt-contract
  version, and evaluation-suite version.
- Evaluate discovery extraction, evidence restraint, strategy generation, domain work,
  Observer criticism, prompt-injection resistance, and typed action selection.
- Distinguish `reachable`, `continuity_only`, `domain_capable`, and `executive_capable`.
- Gate executive and external autonomy by fresh task-level capability evidence, not the
  `/models` response alone.
- Keep zero-spend/FOSS policy intact. No automatic paid upgrade is permitted.

Acceptance: an unevaluated or under-capable fallback remains available for safe continuity
but cannot be represented as an executive-ready model.

### Stage 4: Typed Autonomous Action Plane

- Add durable action candidates with source work, objective, evidence, agent, tool,
  parameters, expected effect, confidence, reversibility, impact, sensitivity, Observer
  result, policy result, approval binding, execution record, and outcome link.
- Let domain agents propose only allowlisted typed actions. Model output cannot grant tool
  authority or bypass deterministic validation.
- Validate tool grants, readiness, parameter schema, mandate authority, evidence freshness,
  model capability, Observer consensus, OPA, impact thresholds, daily limits, and action
  probation before execution.
- Compile allowed candidates into durable `tool_action` work. Create exact-bound approvals
  for gated candidates and resume only after a valid, unexpired, unconsumed decision.
- Record blocked, deferred, rejected, executed, compensated, and failed terminal states.

Acceptance: one below-threshold promoted ERPNext action executes autonomously from a real
business signal, one promoted test-recipient communication executes autonomously, and
large-impact, mismatched, stale, injection-derived, and permanent-gate actions fail closed.

### Stage 5: Signal And Portfolio Hygiene

- Keep successful informational audit telemetry in the audit system without creating
  company signals, business events, or domain work.
- Admit only actionable failures, policy findings, owner instructions, external evidence,
  canonical business changes, and measured outcome changes to the business-event plane.
- Add event amplification, no-action ratio, work-value, and duplicate-suppression metrics.
- Preserve append-only audit history; no historical records are deleted for cosmetic
  cleanup.

Acceptance: internal success telemetry cannot recursively generate company work, while
real failures and business changes still produce explicit dispositions.

### Stage 6: Owner Executive Visibility

- Show cognitive capability by task, evidence backlog and leases, action candidates,
  authorization decisions, autonomous executions, outcome backlog, KPI effects, and
  adaptation recommendations.
- Distinguish infrastructure health, epistemic health, cognitive capability, execution
  authority, and outcome-learning health.
- Add trace links from evidence through claim, objective, work, action, workflow, tool,
  outcome, reflection, and operation-graph memory.
- Preserve pause, domain takeover, owner instruction, approval, and permanent-gate controls.

Acceptance: the owner can explain why an action occurred or did not occur without querying
raw identifiers or the database.

### Stage 7: Outcome-Based Acceptance

- Replace health-only closure with a representative autonomy scenario suite and a
  long-running outcome soak.
- Continuously verify release identity and dependencies, but also inject or observe bounded
  staging-only email, ERPNext, research, unknown-fact, role-gap, and policy events.
- Verify discovery, assignment, decision, approval behavior, execution, cleanup,
  measurement, reflection, and memory/operation-graph linkage.
- Track useful outcomes, unsupported claims, deferred signals, action latency, policy
  blocks, side effects, rollback/cleanup, and unassessed outcomes.

Acceptance: the complete window contains no unexplained signal, untracked side effect,
stale outcome backlog, fabricated claim, policy bypass, recursive audit storm, or hidden
model-capability downgrade.

### Stage 8: Release And Closure

- Run backend tests, Ruff, compileall, Alembic offline SQL, fresh/legacy/representative
  PostgreSQL rehearsals, frontend tests/typecheck/build, dependency/license/secret/GCP
  isolation scans, Compose validation, image scans, ERPNext smoke, restore drill, and load
  gate.
- Back up staging, deploy immutable images, run focused live canaries, and start the new
  outcome soak only after every preflight passes.
- Push each verified implementation increment to the public repository and require green
  push, manual, and scheduled GitHub CI.

## Completion Definition

This milestone is complete only when evidence can drive useful authorized work through a
measured learning loop. A green dashboard, an available model endpoint, a completed
advisory response, or an elapsed soak duration is not sufficient evidence by itself.

The permanent owner gates remain unchanged: contracts, legal and tax filings, payments and
payroll, credentials and permissions, destructive deletion, and production deployment.
The Observer remains read-only. Generated code is never hot-loaded. Paid services remain
disallowed unless the owner explicitly changes the resource policy.
