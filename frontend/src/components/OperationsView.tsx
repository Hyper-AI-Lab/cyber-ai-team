'use client'

import { useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/api'
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle,
  Clock,
  GitBranch,
  Play,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from 'lucide-react'

interface OperationsViewProps {
  cycles: any[]
  onRefresh: () => Promise<void> | void
}

const statusClass: Record<string, string> = {
  ready: 'bg-green-600/20 text-green-300',
  planned: 'bg-blue-600/20 text-blue-300',
  completed: 'bg-green-600/20 text-green-300',
  degraded: 'bg-amber-600/20 text-amber-300',
  failed: 'bg-red-600/20 text-red-300',
  skipped: 'bg-slate-600/30 text-slate-300',
  running: 'bg-blue-600/20 text-blue-300',
  waiting_approval: 'bg-amber-600/20 text-amber-300',
  blocked: 'bg-red-600/20 text-red-300',
}

const riskClass: Record<string, string> = {
  low: 'bg-blue-600/20 text-blue-300',
  medium: 'bg-amber-600/20 text-amber-300',
  high: 'bg-red-600/20 text-red-300',
  critical: 'bg-red-700/40 text-red-100',
}

function formatDate(value?: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function cycleMetadata(event: any) {
  return event?.metadata || {}
}

function cycleCounts(event: any) {
  return cycleMetadata(event).counts || {}
}

function cycleStatus(event: any) {
  return event?.outcome || 'unknown'
}

function sourceLabel(plan: any) {
  if (plan.source_type === 'role_gap') return 'Role gap'
  if (plan.source_type === 'memory_steward_finding') return 'Memory'
  return plan.source_type || 'Unknown'
}

function taskSummary(plan: any) {
  const tasks = plan.tasks || []
  const completed = tasks.filter((task: any) => task.status === 'completed').length
  return `${completed}/${tasks.length || 0}`
}

function canExecutePlan(plan: any) {
  return ['planned', 'running', 'waiting_approval'].includes(plan.status)
}

function planPolicy(plan: any) {
  return plan.context?.policy || {}
}

function planRisk(plan: any) {
  return planPolicy(plan).max_risk || plan.priority || 'medium'
}

function planApproval(plan: any) {
  const waitingTask = (plan.tasks || []).find((task: any) => task.status === 'waiting_approval')
  return waitingTask?.approval_id || null
}

function planSignals(plan: any) {
  const policy = planPolicy(plan)
  const readiness = policy.tool_readiness || {}
  const signals = [
    ...(policy.review_reasons || []),
    ...(readiness.missing_tools?.length
      ? [`Missing tools: ${readiness.missing_tools.join(', ')}`]
      : []),
  ]
  return signals.slice(0, 3)
}

export default function OperationsView({ cycles, onRefresh }: OperationsViewProps) {
  const [localCycles, setLocalCycles] = useState<any[]>(cycles)
  const [plans, setPlans] = useState<any[]>([])
  const [readiness, setReadiness] = useState<any | null>(null)
  const [timeline, setTimeline] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [plansLoading, setPlansLoading] = useState(false)
  const [readinessLoading, setReadinessLoading] = useState(false)
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [planAction, setPlanAction] = useState<string | null>(null)
  const [runMemorySteward, setRunMemorySteward] = useState(true)
  const [runSupervisorReview, setRunSupervisorReview] = useState(true)
  const [runPlanner, setRunPlanner] = useState(true)
  const [applySafeMemoryActions, setApplySafeMemoryActions] = useState(true)
  const [requestMemoryApprovals, setRequestMemoryApprovals] = useState(true)
  const [autoExecutePlans, setAutoExecutePlans] = useState(true)
  const [remediationLimit, setRemediationLimit] = useState(100)
  const [lastRun, setLastRun] = useState<any | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLocalCycles(cycles)
  }, [cycles])

  const latestCycle = useMemo(() => localCycles[0], [localCycles])
  const latestCounts = cycleCounts(latestCycle)
  const latestMetadata = cycleMetadata(latestCycle)

  const loadPlans = async () => {
    setPlansLoading(true)
    setError(null)
    try {
      const result = await api.listAutonomousPlans({ limit: 25 })
      setPlans(result)
    } catch (e: any) {
      setError(e.message || 'Failed to load autonomous plans')
    } finally {
      setPlansLoading(false)
    }
  }

  const loadReadiness = async () => {
    setReadinessLoading(true)
    setError(null)
    try {
      const result = await api.getOperationsReadiness()
      setReadiness(result)
    } catch (e: any) {
      setError(e.message || 'Failed to load production readiness')
    } finally {
      setReadinessLoading(false)
    }
  }

  const loadTimeline = async () => {
    setTimelineLoading(true)
    setError(null)
    try {
      const result = await api.getDecisionTimeline(50)
      setTimeline(result)
    } catch (e: any) {
      setError(e.message || 'Failed to load decision timeline')
    } finally {
      setTimelineLoading(false)
    }
  }

  const loadCycles = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await api.listAutonomousCycles(25)
      setLocalCycles(result)
    } catch (e: any) {
      setError(e.message || 'Failed to load autonomous cycles')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadPlans()
    loadReadiness()
    loadTimeline()
  }, [])

  const runCycle = async () => {
    setRunning(true)
    setError(null)
    try {
      const result = await api.runAutonomousCycle({
        run_memory_steward: runMemorySteward,
        run_supervisor_review: runSupervisorReview,
        run_planner: runPlanner,
        apply_safe_memory_actions: applySafeMemoryActions,
        request_memory_action_approvals: requestMemoryApprovals,
        memory_remediation_limit: remediationLimit,
        auto_execute_plans: autoExecutePlans,
      })
      setLastRun(result)
      await onRefresh()
      await loadCycles()
      await loadPlans()
      await loadReadiness()
      await loadTimeline()
    } catch (e: any) {
      setError(e.message || 'Autonomous cycle failed')
    } finally {
      setRunning(false)
    }
  }

  const scanPlans = async () => {
    setPlanAction('scan')
    setError(null)
    try {
      await api.scanAutonomousPlans({
        include_role_gaps: true,
        include_memory_findings: true,
        auto_execute: autoExecutePlans,
        limit: remediationLimit,
      })
      await onRefresh()
      await loadCycles()
      await loadPlans()
      await loadReadiness()
      await loadTimeline()
    } catch (e: any) {
      setError(e.message || 'Autonomous plan scan failed')
    } finally {
      setPlanAction(null)
    }
  }

  const executePlan = async (planId: string) => {
    setPlanAction(planId)
    setError(null)
    try {
      await api.executeAutonomousPlan(planId)
      await onRefresh()
      await loadCycles()
      await loadPlans()
      await loadReadiness()
      await loadTimeline()
    } catch (e: any) {
      setError(e.message || 'Autonomous plan execution failed')
    } finally {
      setPlanAction(null)
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <Activity className="h-7 w-7 text-blue-400" />
            <h2 className="text-2xl font-bold">Operations</h2>
          </div>
          <p className="mt-1 text-slate-400">
            Autonomous cycle control, decisions, and health signals.
          </p>
        </div>
        <button
          onClick={loadCycles}
          disabled={loading}
          className="btn-secondary flex items-center gap-2"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      <section className="card">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-emerald-300" />
              <h3 className="text-lg font-semibold">Production Readiness</h3>
            </div>
            <p className="mt-1 text-sm text-slate-400">
              Tool readiness, autonomy policy, evidence, and integration blockers.
            </p>
          </div>
          <button
            onClick={loadReadiness}
            disabled={readinessLoading}
            className="btn-secondary flex items-center gap-2 text-sm"
          >
            <RefreshCw className={`h-4 w-4 ${readinessLoading ? 'animate-spin' : ''}`} />
            Refresh Readiness
          </button>
        </div>

        {readiness ? (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <Metric label="Status" value={readiness.status === 'ready' ? 1 : 0} />
              <Metric label="Live tools" value={readiness.tools?.counts_by_state?.live || 0} />
              <Metric
                label="Blocked tools"
                value={
                  (readiness.tools?.counts_by_state?.unavailable || 0)
                  + (readiness.tools?.counts_by_state?.configuration_required || 0)
                }
              />
              <Metric
                label="Trace errors"
                value={readiness.memory?.recent_trace_errors || 0}
              />
              <Metric
                label="Evidence"
                value={readiness.controls?.recent_evidence_count || 0}
              />
            </div>
            <div className="flex flex-wrap gap-2 text-xs">
              <span
                className={`rounded-full px-3 py-1 font-medium ${
                  statusClass[readiness.status] || 'bg-slate-700 text-slate-300'
                }`}
              >
                {readiness.status}
              </span>
              <span className="rounded-full border border-slate-700 px-3 py-1 text-slate-300">
                {readiness.environment}
              </span>
              <span className="rounded-full border border-slate-700 px-3 py-1 text-slate-300">
                autonomy: {readiness.autonomy?.side_effect_mode}
              </span>
              <span className="rounded-full border border-slate-700 px-3 py-1 text-slate-300">
                build: {readiness.version?.build_sha}
              </span>
            </div>
            {readiness.blockers?.length > 0 ? (
              <div className="space-y-2">
                <h4 className="text-sm font-medium text-amber-200">Readiness Blockers</h4>
                {readiness.blockers.slice(0, 8).map((blocker: any, index: number) => (
                  <div
                    key={`${blocker.tool_name || blocker.provider || blocker.channel}-${index}`}
                    className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100"
                  >
                    <span className="font-medium">
                      {blocker.tool_name || `${blocker.channel}:${blocker.provider}`}
                    </span>
                    <span className="ml-2 text-amber-200/80">
                      {blocker.state || blocker.mode}
                    </span>
                    {blocker.reason && (
                      <div className="mt-1 text-xs text-amber-100/70">{blocker.reason}</div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-400">
                No production readiness blockers reported by configured checks.
              </p>
            )}
          </div>
        ) : (
          <div className="py-8 text-center text-slate-500">
            {readinessLoading ? 'Loading readiness...' : 'Readiness data unavailable.'}
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(320px,420px)_1fr]">
        <section className="card space-y-5">
          <div>
            <h3 className="text-lg font-semibold">Run Cycle</h3>
            <p className="mt-1 text-sm text-slate-400">
              Start a coordinated memory, supervisor, and planner pass now.
            </p>
          </div>

          <div className="space-y-3 text-sm text-slate-300">
            <label className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 px-3 py-2">
              <span className="flex items-center gap-2">
                <Brain className="h-4 w-4 text-blue-300" />
                Memory steward
              </span>
              <input
                type="checkbox"
                checked={runMemorySteward}
                onChange={(event) => setRunMemorySteward(event.target.checked)}
                className="h-4 w-4 accent-blue-500"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 px-3 py-2">
              <span className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-amber-300" />
                Supervisor review
              </span>
              <input
                type="checkbox"
                checked={runSupervisorReview}
                onChange={(event) => setRunSupervisorReview(event.target.checked)}
                className="h-4 w-4 accent-blue-500"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 px-3 py-2">
              <span className="flex items-center gap-2">
                <GitBranch className="h-4 w-4 text-emerald-300" />
                Autonomous planner
              </span>
              <input
                type="checkbox"
                checked={runPlanner}
                onChange={(event) => setRunPlanner(event.target.checked)}
                className="h-4 w-4 accent-blue-500"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 px-3 py-2">
              <span>Apply safe memory actions</span>
              <input
                type="checkbox"
                checked={applySafeMemoryActions}
                onChange={(event) => setApplySafeMemoryActions(event.target.checked)}
                className="h-4 w-4 accent-blue-500"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 px-3 py-2">
              <span>Request approvals for risky actions</span>
              <input
                type="checkbox"
                checked={requestMemoryApprovals}
                onChange={(event) => setRequestMemoryApprovals(event.target.checked)}
                className="h-4 w-4 accent-blue-500"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded-lg border border-slate-700 px-3 py-2">
              <span>Auto-execute safe plans</span>
              <input
                type="checkbox"
                checked={autoExecutePlans}
                onChange={(event) => setAutoExecutePlans(event.target.checked)}
                className="h-4 w-4 accent-blue-500"
              />
            </label>
          </div>

          <label className="block text-sm text-slate-300" htmlFor="remediation-limit">
            Remediation limit
            <input
              id="remediation-limit"
              type="number"
              min={1}
              max={200}
              value={remediationLimit}
              onChange={(event) => setRemediationLimit(Number(event.target.value))}
              className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
            />
          </label>

          <button
            onClick={runCycle}
            disabled={running || (!runMemorySteward && !runSupervisorReview && !runPlanner)}
            className="btn-primary flex w-full items-center justify-center gap-2"
          >
            <Play className="h-4 w-4" />
            {running ? 'Running...' : 'Run Autonomous Cycle'}
          </button>
        </section>

        <section className="card">
          <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold">Latest Cycle</h3>
              <p className="mt-1 text-sm text-slate-400">
                {latestCycle ? formatDate(latestCycle.created_at) : 'No autonomous cycles yet'}
              </p>
            </div>
            {latestCycle && (
              <span
                className={`rounded-full px-3 py-1 text-xs font-medium ${
                  statusClass[cycleStatus(latestCycle)] || 'bg-slate-700 text-slate-300'
                }`}
              >
                {cycleStatus(latestCycle)}
              </span>
            )}
          </div>

          {latestCycle ? (
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Metric label="Memory findings" value={
                  (latestCounts.memory_findings_created || 0)
                  + (latestCounts.memory_findings_updated || 0)
                } />
                <Metric label="Actions applied" value={latestCounts.memory_actions_applied || 0} />
                <Metric label="Role proposals" value={latestCounts.role_gaps_proposed || 0} />
                <Metric
                  label="Plans completed"
                  value={latestCounts.autonomous_plans_completed || 0}
                />
              </div>

              <div>
                <h4 className="mb-2 text-sm font-medium text-slate-300">Decisions</h4>
                {latestMetadata.decisions?.length ? (
                  <div className="flex flex-wrap gap-2">
                    {latestMetadata.decisions.map((decision: any, index: number) => (
                      <span
                        key={`${decision.step}-${decision.decision}-${index}`}
                        className="rounded-full border border-blue-500/30 bg-blue-500/10 px-3 py-1 text-xs text-blue-200"
                      >
                        {decision.step}: {decision.decision}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-slate-500">No decisions recorded in the latest cycle.</p>
                )}
              </div>

              {latestMetadata.errors?.length > 0 && (
                <div>
                  <h4 className="mb-2 text-sm font-medium text-red-200">Errors</h4>
                  <div className="space-y-2">
                    {latestMetadata.errors.map((item: any, index: number) => (
                      <div
                        key={`${item.step}-${index}`}
                        className="rounded-lg border border-red-900 bg-red-950/30 px-3 py-2 text-sm text-red-200"
                      >
                        {item.step}: {item.message}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="py-10 text-center text-slate-500">
              <Clock className="mx-auto mb-3 h-10 w-10 opacity-60" />
              <p>No cycle history has been recorded.</p>
            </div>
          )}
        </section>
      </div>

      {lastRun && (
        <section className="card">
          <div className="mb-4 flex items-center gap-2">
            {lastRun.status === 'completed' ? (
              <CheckCircle className="h-5 w-5 text-green-300" />
            ) : lastRun.status === 'failed' ? (
              <XCircle className="h-5 w-5 text-red-300" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-amber-300" />
            )}
            <h3 className="text-lg font-semibold">Last Manual Run</h3>
            <span
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                statusClass[lastRun.status] || 'bg-slate-700 text-slate-300'
              }`}
            >
              {lastRun.status}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Metric label="Findings" value={
              (lastRun.counts?.memory_findings_created || 0)
              + (lastRun.counts?.memory_findings_updated || 0)
            } />
            <Metric label="Actions" value={lastRun.counts?.memory_actions_applied || 0} />
            <Metric label="Approvals" value={lastRun.counts?.memory_approvals_requested || 0} />
            <Metric label="Plans" value={lastRun.counts?.autonomous_plans_completed || 0} />
            <Metric label="Failures" value={lastRun.errors?.length || 0} />
          </div>
        </section>
      )}

      <section className="card overflow-hidden">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-emerald-300" />
            <div>
              <h3 className="text-lg font-semibold">Autonomous Plans</h3>
              <p className="mt-1 text-sm text-slate-400">
                Durable plans generated from role gaps and memory findings.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={loadPlans}
              disabled={plansLoading}
              className="btn-secondary flex items-center gap-2 text-sm"
            >
              <RefreshCw className={`h-4 w-4 ${plansLoading ? 'animate-spin' : ''}`} />
              Refresh Plans
            </button>
            <button
              onClick={scanPlans}
              disabled={planAction !== null}
              className="btn-primary flex items-center gap-2 text-sm"
            >
              <Play className="h-4 w-4" />
              {planAction === 'scan' ? 'Scanning...' : 'Scan and Execute'}
            </button>
          </div>
        </div>

        {plans.length === 0 ? (
          <div className="py-10 text-center text-slate-500">
            {plansLoading ? 'Loading plans...' : 'No autonomous plans recorded yet.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="py-3 pr-4">Plan</th>
                  <th className="py-3 pr-4">Source</th>
                  <th className="py-3 pr-4">Status</th>
                  <th className="py-3 pr-4">Risk</th>
                  <th className="py-3 pr-4">Tasks</th>
                  <th className="py-3 pr-4">Approval</th>
                  <th className="py-3 pr-4">Updated</th>
                  <th className="py-3 pr-4">Action</th>
                </tr>
              </thead>
              <tbody>
                {plans.map((plan) => (
                  <tr key={plan.id} className="border-b border-slate-800 text-slate-200">
                    <td className="min-w-64 py-3 pr-4">
                      <div className="font-medium text-white">{plan.title}</div>
                      <div className="mt-1 line-clamp-2 text-xs text-slate-500">
                        {plan.objective}
                      </div>
                      {planSignals(plan).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {planSignals(plan).map((signal) => (
                            <span
                              key={`${plan.id}-${signal}`}
                              className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-400"
                            >
                              {signal}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="py-3 pr-4">{sourceLabel(plan)}</td>
                    <td className="py-3 pr-4">
                      <span
                        className={`rounded-full px-2 py-1 text-xs ${
                          statusClass[plan.status] || 'bg-slate-700 text-slate-300'
                        }`}
                      >
                        {plan.status}
                      </span>
                    </td>
                    <td className="py-3 pr-4">
                      <span
                        className={`rounded-full px-2 py-1 text-xs ${
                          riskClass[planRisk(plan)] || 'bg-slate-700 text-slate-300'
                        }`}
                      >
                        {planRisk(plan)}
                      </span>
                    </td>
                    <td className="py-3 pr-4">
                      <div>{taskSummary(plan)}</div>
                      <div className="mt-1 flex max-w-48 flex-wrap gap-1">
                        {(plan.tasks || []).slice(0, 5).map((task: any) => (
                          <span
                            key={task.id}
                            title={`${task.title} (${task.task_type})`}
                            className={`rounded-full px-1.5 py-0.5 text-[10px] ${
                              statusClass[task.status] || 'bg-slate-700 text-slate-300'
                            }`}
                          >
                            {task.risk_level}:{task.status}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-3 pr-4">
                      {planApproval(plan) ? (
                        <span className="rounded-full bg-amber-600/20 px-2 py-1 text-xs text-amber-300">
                          {planApproval(plan)}
                        </span>
                      ) : (
                        <span className="text-slate-500">-</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap py-3 pr-4 text-slate-400">
                      {formatDate(plan.updated_at)}
                    </td>
                    <td className="py-3 pr-4">
                      <button
                        onClick={() => executePlan(plan.id)}
                        disabled={!canExecutePlan(plan) || planAction !== null}
                        className="btn-secondary flex items-center gap-2 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Play className="h-3.5 w-3.5" />
                        {planAction === plan.id ? 'Executing...' : 'Execute'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card overflow-hidden">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-blue-300" />
            <div>
              <h3 className="text-lg font-semibold">Decision Timeline</h3>
              <p className="mt-1 text-sm text-slate-400">
                Memory traces, tool calls, approvals, workflow steps, and audit events.
              </p>
            </div>
          </div>
          <button
            onClick={loadTimeline}
            disabled={timelineLoading}
            className="btn-secondary flex items-center gap-2 text-sm"
          >
            <RefreshCw className={`h-4 w-4 ${timelineLoading ? 'animate-spin' : ''}`} />
            Refresh Timeline
          </button>
        </div>
        {timeline.length === 0 ? (
          <div className="py-10 text-center text-slate-500">
            {timelineLoading ? 'Loading timeline...' : 'No decision timeline events recorded.'}
          </div>
        ) : (
          <div className="space-y-3">
            {timeline.slice(0, 20).map((item) => (
              <div key={`${item.kind}-${item.id}`} className="rounded-lg border border-slate-800 bg-slate-900/30 p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300">
                    {item.kind}
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 ${
                      item.status === 'success' || item.status === 'recorded'
                        ? 'bg-green-600/20 text-green-300'
                        : item.status === 'blocked' || item.status === 'error'
                          ? 'bg-red-600/20 text-red-300'
                          : 'bg-slate-700 text-slate-300'
                    }`}
                  >
                    {item.status || 'unknown'}
                  </span>
                  {item.agent_id && <span className="text-slate-400">{item.agent_id}</span>}
                  {item.tool_name && <span className="text-slate-400">{item.tool_name}</span>}
                  {item.workflow_run_id && (
                    <span className="break-all text-slate-500">{item.workflow_run_id}</span>
                  )}
                  {item.coverage && (
                    <span className="rounded-full border border-blue-500/30 px-2 py-0.5 text-blue-200">
                      {item.coverage}
                    </span>
                  )}
                  <span className="ml-auto text-slate-500">{formatDate(item.created_at)}</span>
                </div>
                <div className="mt-2 text-sm text-slate-200">{item.title}</div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card overflow-hidden">
        <div className="mb-4 flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-blue-300" />
          <h3 className="text-lg font-semibold">Cycle History</h3>
        </div>

        {localCycles.length === 0 ? (
          <div className="py-10 text-center text-slate-500">
            {loading ? 'Loading cycles...' : 'No autonomous operation cycles recorded yet.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="py-3 pr-4">Time</th>
                  <th className="py-3 pr-4">Actor</th>
                  <th className="py-3 pr-4">Outcome</th>
                  <th className="py-3 pr-4">Findings</th>
                  <th className="py-3 pr-4">Actions</th>
                  <th className="py-3 pr-4">Role Gaps</th>
                  <th className="py-3 pr-4">Plans</th>
                  <th className="py-3 pr-4">Errors</th>
                </tr>
              </thead>
              <tbody>
                {localCycles.map((cycle) => {
                  const counts = cycleCounts(cycle)
                  const metadata = cycleMetadata(cycle)
                  return (
                    <tr key={cycle.id} className="border-b border-slate-800 text-slate-200">
                      <td className="whitespace-nowrap py-3 pr-4 text-slate-400">
                        {formatDate(cycle.created_at)}
                      </td>
                      <td className="py-3 pr-4">{cycle.actor}</td>
                      <td className="py-3 pr-4">
                        <span
                          className={`rounded-full px-2 py-1 text-xs ${
                            statusClass[cycleStatus(cycle)] || 'bg-slate-700 text-slate-300'
                          }`}
                        >
                          {cycleStatus(cycle)}
                        </span>
                      </td>
                      <td className="py-3 pr-4">
                        {(counts.memory_findings_created || 0)
                          + (counts.memory_findings_updated || 0)}
                      </td>
                      <td className="py-3 pr-4">{counts.memory_actions_applied || 0}</td>
                      <td className="py-3 pr-4">{counts.role_gaps_proposed || 0}</td>
                      <td className="py-3 pr-4">{counts.autonomous_plans_completed || 0}</td>
                      <td className="py-3 pr-4">{metadata.errors?.length || 0}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-3">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-bold text-white">{value}</div>
    </div>
  )
}
