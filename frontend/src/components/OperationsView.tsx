'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/api'
import {
  Activity,
  AlertTriangle,
  Bell,
  Brain,
  CalendarClock,
  CheckCircle,
  Clock,
  GitBranch,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  XCircle,
} from 'lucide-react'

interface OperationsViewProps {
  cycles: any[]
  onRefresh: () => Promise<void> | void
  onNavigate?: (view: 'agents' | 'approvals' | 'operations') => void
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
  if (plan.source_type === 'company_context_snapshot') return 'Company context'
  if (plan.source_type === 'operating_cadence') return 'Operating cadence'
  if (plan.source_type === 'operating_cadence_follow_up') return 'Cadence follow-up'
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
  const followUp = plan.context?.follow_up || {}
  const signals = [
    ...(followUp.kind ? [`Follow-up: ${followUp.kind}`] : []),
    ...(followUp.recommended_action ? [`Action: ${followUp.recommended_action}`] : []),
    ...(policy.review_reasons || []),
    ...(readiness.missing_tools?.length
      ? [`Missing tools: ${readiness.missing_tools.join(', ')}`]
      : []),
  ]
  return signals.slice(0, 3)
}

function navigationTarget(targetView?: string): 'agents' | 'approvals' | 'operations' | null {
  if (targetView === 'agents' || targetView === 'approvals' || targetView === 'operations') {
    return targetView
  }
  return null
}

function attentionSlaClass(state?: string) {
  if (state === 'overdue') return 'bg-red-600/20 text-red-300'
  if (state === 'due_soon') return 'bg-amber-600/20 text-amber-300'
  if (state === 'resolved') return 'bg-green-600/20 text-green-300'
  return 'bg-blue-600/20 text-blue-300'
}

function attentionActionLabel(action?: string) {
  if (!action) return 'Review'
  return action.replace(/_/g, ' ')
}

export default function OperationsView({ cycles, onRefresh, onNavigate }: OperationsViewProps) {
  const [localCycles, setLocalCycles] = useState<any[]>(cycles)
  const [plans, setPlans] = useState<any[]>([])
  const [readiness, setReadiness] = useState<any | null>(null)
  const [ownerAttention, setOwnerAttention] = useState<any | null>(null)
  const [operatingCadence, setOperatingCadence] = useState<any | null>(null)
  const [followUps, setFollowUps] = useState<any | null>(null)
  const [followUpStatus, setFollowUpStatus] = useState('active')
  const [followUpNotes, setFollowUpNotes] = useState<Record<string, string>>({})
  const [timeline, setTimeline] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [plansLoading, setPlansLoading] = useState(false)
  const [readinessLoading, setReadinessLoading] = useState(false)
  const [ownerAttentionLoading, setOwnerAttentionLoading] = useState(false)
  const [ownerAttentionNotifyLoading, setOwnerAttentionNotifyLoading] = useState(false)
  const [ownerAttentionNotifyResult, setOwnerAttentionNotifyResult] = useState<any | null>(null)
  const [operatingCadenceLoading, setOperatingCadenceLoading] = useState(false)
  const [followUpsLoading, setFollowUpsLoading] = useState(false)
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [driftScanning, setDriftScanning] = useState(false)
  const [cadenceScanning, setCadenceScanning] = useState(false)
  const [planAction, setPlanAction] = useState<string | null>(null)
  const [followUpAction, setFollowUpAction] = useState<string | null>(null)
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

  const loadOwnerAttention = async () => {
    setOwnerAttentionLoading(true)
    setError(null)
    try {
      const result = await api.getOwnerAttention({ status: 'active', limit: 25 })
      setOwnerAttention(result)
    } catch (e: any) {
      setError(e.message || 'Failed to load owner attention queue')
    } finally {
      setOwnerAttentionLoading(false)
    }
  }

  const notifyOwnerAttention = async () => {
    setOwnerAttentionNotifyLoading(true)
    setError(null)
    try {
      const result = await api.notifyOwnerAttention({ dryRun: false, limit: 25 })
      setOwnerAttentionNotifyResult(result)
      await Promise.all([loadOwnerAttention(), loadReadiness()])
    } catch (e: any) {
      setError(e.message || 'Failed to notify owner attention queue')
    } finally {
      setOwnerAttentionNotifyLoading(false)
    }
  }

  const loadOperatingCadence = async () => {
    setOperatingCadenceLoading(true)
    setError(null)
    try {
      const result = await api.getOperatingCadenceStatus({ limit: 200 })
      setOperatingCadence(result)
    } catch (e: any) {
      setError(e.message || 'Failed to load operating cadence status')
    } finally {
      setOperatingCadenceLoading(false)
    }
  }

  const loadFollowUps = useCallback(async (status: string = followUpStatus) => {
    setFollowUpsLoading(true)
    setError(null)
    try {
      const result = await api.getOperatingCadenceFollowUps({
        status,
        limit: 50,
      })
      setFollowUps(result)
    } catch (e: any) {
      setError(e.message || 'Failed to load cadence follow-ups')
    } finally {
      setFollowUpsLoading(false)
    }
  }, [followUpStatus])

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
    loadOwnerAttention()
    loadOperatingCadence()
    loadTimeline()
  }, [])

  useEffect(() => {
    loadFollowUps()
  }, [loadFollowUps])

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
      await loadOwnerAttention()
      await loadOperatingCadence()
      await loadFollowUps()
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
        include_company_context: true,
        auto_execute: autoExecutePlans,
        limit: remediationLimit,
      })
      await onRefresh()
      await loadCycles()
      await loadPlans()
      await loadReadiness()
      await loadOwnerAttention()
      await loadOperatingCadence()
      await loadFollowUps()
      await loadTimeline()
    } catch (e: any) {
      setError(e.message || 'Autonomous plan scan failed')
    } finally {
      setPlanAction(null)
    }
  }

  const scanCompanyContextDrift = async () => {
    setDriftScanning(true)
    setError(null)
    try {
      await api.scanCompanyContextDrift({
        dry_run: false,
        apply_low_risk: true,
        run_planner: true,
      })
      await onRefresh()
      await loadPlans()
      await loadReadiness()
      await loadOwnerAttention()
      await loadOperatingCadence()
      await loadFollowUps()
      await loadTimeline()
    } catch (e: any) {
      setError(e.message || 'ERPNext drift scan failed')
    } finally {
      setDriftScanning(false)
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
      await loadOwnerAttention()
      await loadOperatingCadence()
      await loadFollowUps()
      await loadTimeline()
    } catch (e: any) {
      setError(e.message || 'Autonomous plan execution failed')
    } finally {
      setPlanAction(null)
    }
  }

  const resolveFollowUp = async (
    planId: string,
    action: 'reviewed' | 'deferred' | 'dismissed'
  ) => {
    setFollowUpAction(`${planId}:${action}`)
    setError(null)
    try {
      await api.resolveOperatingCadenceFollowUp(
        planId,
        action,
        followUpNotes[planId] || '',
      )
      setFollowUpNotes((current) => {
        const next = { ...current }
        delete next[planId]
        return next
      })
      await onRefresh()
      await loadPlans()
      await loadReadiness()
      await loadOwnerAttention()
      await loadFollowUps()
      await loadTimeline()
    } catch (e: any) {
      setError(e.message || 'Cadence follow-up update failed')
    } finally {
      setFollowUpAction(null)
    }
  }

  const scanOperatingCadences = async () => {
    setCadenceScanning(true)
    setError(null)
    try {
      await api.scanOperatingCadences({
        auto_execute: autoExecutePlans,
        limit: 200,
      })
      await onRefresh()
      await loadCycles()
      await loadPlans()
      await loadReadiness()
      await loadOwnerAttention()
      await loadOperatingCadence()
      await loadFollowUps()
      await loadTimeline()
    } catch (e: any) {
      setError(e.message || 'Operating cadence scan failed')
    } finally {
      setCadenceScanning(false)
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
          <div className="flex flex-wrap gap-2">
            <button
              onClick={loadReadiness}
              disabled={readinessLoading}
              className="btn-secondary flex items-center gap-2 text-sm"
            >
              <RefreshCw className={`h-4 w-4 ${readinessLoading ? 'animate-spin' : ''}`} />
              Refresh Readiness
            </button>
            <button
              onClick={scanCompanyContextDrift}
              disabled={driftScanning}
              className="btn-primary flex items-center gap-2 text-sm"
            >
              <GitBranch className={`h-4 w-4 ${driftScanning ? 'animate-pulse' : ''}`} />
              {driftScanning ? 'Scanning Drift...' : 'Run Drift Scan'}
            </button>
          </div>
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
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-6">
              <ReadinessPanel
                title="Required Providers"
                value={(readiness.integrations?.required_providers || []).join(', ') || 'none'}
                detail={
                  readiness.integrations?.required_blockers?.length
                    ? `${readiness.integrations.required_blockers.length} blocking`
                    : 'all required providers clear'
                }
              />
              <ReadinessPanel
                title="ERPNext"
                value={readiness.integrations?.erpnext?.mode || 'unavailable'}
                detail={
                  readiness.integrations?.erpnext?.detail
                  || readiness.integrations?.erpnext?.site_url
                  || 'ERPNext status not reported'
                }
              />
              <ReadinessPanel
                title="Optional Disabled"
                value={String(readiness.integrations?.optional_disabled?.length || 0)}
                detail={
                  readiness.integrations?.optional_disabled?.length
                    ? readiness.integrations.optional_disabled
                        .slice(0, 3)
                        .map((item: any) => item.provider || item.channel)
                        .join(', ')
                    : 'no optional provider warnings'
                }
              />
              <ReadinessPanel
                title="Company Context"
                value={readiness.company_context?.status || 'unavailable'}
                detail={
                  readiness.company_context?.last_sync_at
                    ? `last sync ${formatDate(readiness.company_context.last_sync_at)}`
                    : readiness.company_context?.detail || 'no ERPNext context sync recorded'
                }
              />
              <ReadinessPanel
                title="ERPNext Drift"
                value={
                  readiness.company_context?.drift_detection?.latest_drift?.status
                  || (readiness.company_context?.drift_detection?.enabled ? 'waiting' : 'disabled')
                }
                detail={
                  readiness.company_context?.drift_detection?.latest_drift?.checked_at
                    ? `last scan ${formatDate(
                        readiness.company_context.drift_detection.latest_drift.checked_at,
                      )}`
                    : readiness.company_context?.drift_detection?.enabled
                      ? `every ${readiness.company_context.drift_detection.interval_seconds}s`
                      : 'scheduled scans are disabled'
                }
              />
              <ReadinessPanel
                title="Cadence Scheduler"
                value={readiness.operating_cadence_scheduler?.status || 'unavailable'}
                detail={
                  readiness.operating_cadence_scheduler?.last_completed_at
                    ? `last scan ${formatDate(
                        readiness.operating_cadence_scheduler.last_completed_at,
                      )}`
                    : readiness.operating_cadence_scheduler?.enabled
                      ? `every ${readiness.operating_cadence_scheduler.interval_seconds}s`
                      : readiness.operating_cadence_scheduler?.detail || 'scheduled scans are disabled'
                }
              />
              <ReadinessPanel
                title="Owner Attention"
                value={`${readiness.owner_attention?.counts?.active || 0} active`}
                detail={
                  readiness.owner_attention?.counts?.overdue
                    ? `${readiness.owner_attention.counts.overdue} overdue`
                    : `${readiness.owner_attention?.counts?.scheduler_created || 0} scheduler-created`
                }
              />
              <ReadinessPanel
                title="Owner Notify"
                value={readiness.owner_attention_notifications?.status || 'unavailable'}
                detail={
                  readiness.owner_attention_notifications?.runtime?.last_completed_at
                    ? `last run ${formatDate(
                        readiness.owner_attention_notifications.runtime.last_completed_at,
                      )}`
                    : readiness.owner_attention_notifications?.detail
                      || 'notification worker status unavailable'
                }
              />
              <ReadinessPanel
                title="Cadence Follow-Ups"
                value={`${readiness.operating_follow_ups?.counts?.active || 0} active`}
                detail={
                  readiness.operating_follow_ups?.status === 'ready'
                    ? `${readiness.operating_follow_ups?.counts?.completed || 0} completed`
                    : readiness.operating_follow_ups?.detail || 'follow-up queue unavailable'
                }
              />
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

      <section className="card">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <Bell className="h-5 w-5 text-amber-300" />
            <div>
              <h3 className="text-lg font-semibold">Owner Attention</h3>
              <p className="mt-1 text-sm text-slate-400">
                Scheduler-created operating plans and follow-ups that need owner review.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={loadOwnerAttention}
              disabled={ownerAttentionLoading}
              className="btn-secondary flex items-center gap-2 text-sm"
            >
              <RefreshCw className={`h-4 w-4 ${ownerAttentionLoading ? 'animate-spin' : ''}`} />
              Refresh Attention
            </button>
            <button
              onClick={notifyOwnerAttention}
              disabled={ownerAttentionNotifyLoading}
              className="btn-primary flex items-center gap-2 text-sm"
            >
              <Send className={`h-4 w-4 ${ownerAttentionNotifyLoading ? 'animate-pulse' : ''}`} />
              {ownerAttentionNotifyLoading ? 'Notifying...' : 'Notify Owner'}
            </button>
          </div>
        </div>

        {ownerAttention ? (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <Metric label="Active" value={ownerAttention.counts?.active || 0} />
              <Metric label="Overdue" value={ownerAttention.counts?.overdue || 0} />
              <Metric label="Due soon" value={ownerAttention.counts?.due_soon || 0} />
              <Metric
                label="Scheduler"
                value={ownerAttention.counts?.scheduler_created || 0}
              />
              <Metric label="Executable" value={ownerAttention.counts?.executable || 0} />
            </div>
            {ownerAttentionNotifyResult && (
              <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-sm text-blue-100">
                Notification run: {ownerAttentionNotifyResult.counts?.sent || 0} sent,{' '}
                {ownerAttentionNotifyResult.counts?.skipped || 0} skipped,{' '}
                {ownerAttentionNotifyResult.counts?.failed || 0} failed.
              </div>
            )}

            {ownerAttention.items?.length ? (
              <div className="space-y-3">
                {ownerAttention.items.slice(0, 8).map((item: any) => {
                  const target = navigationTarget(item.target_view)
                  return (
                    <div
                      key={item.plan_id}
                      className="rounded-lg border border-slate-800 bg-slate-900/30 p-3"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-white">{item.title}</span>
                            <span
                              className={`rounded-full px-2 py-1 text-xs ${
                                attentionSlaClass(item.sla_state)
                              }`}
                            >
                              {item.sla_state}
                            </span>
                            <span
                              className={`rounded-full px-2 py-1 text-xs ${
                                riskClass[item.attention_priority] || 'bg-slate-700 text-slate-300'
                              }`}
                            >
                              {item.attention_priority}
                            </span>
                          </div>
                          <div className="mt-1 line-clamp-2 text-sm text-slate-400">
                            {item.attention_reason}
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1.5 text-xs text-slate-500">
                            <span>{sourceLabel(item)}</span>
                            <span>{item.kind?.replace(/_/g, ' ')}</span>
                            <span>{item.completed_task_count}/{item.task_count} tasks</span>
                            {item.sla_due_at && <span>due {formatDate(item.sla_due_at)}</span>}
                          </div>
                        </div>
                        <div className="flex shrink-0 flex-wrap gap-2">
                          {item.can_execute && (
                            <button
                              onClick={() => executePlan(item.plan_id)}
                              disabled={planAction !== null}
                              className="btn-primary flex items-center gap-2 px-3 py-1.5 text-xs"
                            >
                              <Play className="h-3.5 w-3.5" />
                              {planAction === item.plan_id
                                ? 'Running...'
                                : attentionActionLabel(item.recommended_action)}
                            </button>
                          )}
                          {target && target !== 'operations' && onNavigate && (
                            <button
                              onClick={() => onNavigate(target)}
                              className="btn-secondary px-3 py-1.5 text-xs"
                            >
                              Open {target}
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="text-sm text-slate-500">
                No owner attention items are active. Scheduler-created reviews will appear here.
              </p>
            )}
          </div>
        ) : (
          <div className="py-8 text-center text-slate-500">
            {ownerAttentionLoading ? 'Loading owner attention...' : 'Owner attention data unavailable.'}
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

      <section className="card">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <CalendarClock className="h-5 w-5 text-blue-300" />
            <div>
              <h3 className="text-lg font-semibold">Operating Loops</h3>
              <p className="mt-1 text-sm text-slate-400">
                Due role cadences become durable, owner-visible plans without external side effects.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={loadOperatingCadence}
              disabled={operatingCadenceLoading}
              className="btn-secondary flex items-center gap-2 text-sm"
            >
              <RefreshCw
                className={`h-4 w-4 ${operatingCadenceLoading ? 'animate-spin' : ''}`}
              />
              Refresh Loops
            </button>
            <button
              onClick={scanOperatingCadences}
              disabled={cadenceScanning}
              className="btn-primary flex items-center gap-2 text-sm"
            >
              <Play className="h-4 w-4" />
              {cadenceScanning ? 'Scanning...' : 'Create Due Plans'}
            </button>
          </div>
        </div>

        {operatingCadence ? (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Metric label="Cadences" value={operatingCadence.counts?.cadences || 0} />
              <Metric label="Due" value={operatingCadence.counts?.due || 0} />
              <Metric label="Active plans" value={operatingCadence.counts?.active_plans || 0} />
              <Metric label="Fresh" value={operatingCadence.counts?.not_due || 0} />
            </div>
            {operatingCadence.items?.length ? (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {operatingCadence.items.slice(0, 6).map((item: any) => (
                  <div
                    key={item.cadence_id}
                    className="rounded-lg border border-slate-800 bg-slate-900/30 p-3"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <div className="font-medium text-white">{item.role_name}</div>
                        <div className="mt-1 text-xs text-slate-500">
                          {item.frequency} · {item.review_window}
                        </div>
                      </div>
                      <span
                        className={`rounded-full px-2 py-1 text-xs ${
                          item.state === 'due'
                            ? 'bg-amber-600/20 text-amber-300'
                            : item.state === 'active_plan'
                              ? 'bg-blue-600/20 text-blue-300'
                              : 'bg-green-600/20 text-green-300'
                        }`}
                      >
                        {item.state}
                      </span>
                    </div>
                    <div className="mt-2 line-clamp-2 text-xs text-slate-400">
                      {item.due_reason}
                    </div>
                    {item.next_due_at && (
                      <div className="mt-2 text-xs text-slate-500">
                        Next due {formatDate(item.next_due_at)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-500">
                No active role cadences are available yet. Activate recommended roles first.
              </p>
            )}
          </div>
        ) : (
          <div className="py-8 text-center text-slate-500">
            {operatingCadenceLoading ? 'Loading operating loops...' : 'Operating loop data unavailable.'}
          </div>
        )}
      </section>

      <section className="card">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-emerald-300" />
            <div>
              <h3 className="text-lg font-semibold">Cadence Follow-Ups</h3>
              <p className="mt-1 text-sm text-slate-400">
                Owner-review tasks generated by completed operating-loop plans.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {['active', 'waiting_approval', 'completed', 'all'].map((status) => (
              <button
                key={status}
                onClick={() => setFollowUpStatus(status)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                  followUpStatus === status
                    ? 'bg-blue-600 text-white'
                    : 'border border-slate-700 text-slate-300 hover:border-slate-500'
                }`}
              >
                {status.replace('_', ' ')}
              </button>
            ))}
            <button
              onClick={() => loadFollowUps()}
              disabled={followUpsLoading}
              className="btn-secondary flex items-center gap-2 text-sm"
            >
              <RefreshCw className={`h-4 w-4 ${followUpsLoading ? 'animate-spin' : ''}`} />
              Refresh Follow-Ups
            </button>
          </div>
        </div>

        {followUps ? (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Metric label="Visible" value={followUps.counts?.total || 0} />
              <Metric label="Active" value={followUps.counts?.active || 0} />
              <Metric label="Completed" value={followUps.counts?.completed || 0} />
              <Metric
                label="Medium+ risk"
                value={
                  (followUps.counts?.by_risk?.medium || 0)
                  + (followUps.counts?.by_risk?.high || 0)
                  + (followUps.counts?.by_risk?.critical || 0)
                }
              />
            </div>

            {followUps.items?.length ? (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {followUps.items.slice(0, 8).map((item: any) => {
                  const target = navigationTarget(item.target_view)
                  return (
                    <div
                      key={item.plan_id}
                      className="rounded-lg border border-slate-800 bg-slate-900/30 p-4"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="font-medium text-white">{item.title}</div>
                          <div className="mt-1 text-xs text-slate-500">
                            {item.role_name || item.agent_id || 'Unassigned role'} · {item.kind}
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          <span
                            className={`rounded-full px-2 py-1 text-xs ${
                              statusClass[item.status] || 'bg-slate-700 text-slate-300'
                            }`}
                          >
                            {item.status}
                          </span>
                          <span
                            className={`rounded-full px-2 py-1 text-xs ${
                              riskClass[item.risk_level] || 'bg-slate-700 text-slate-300'
                            }`}
                          >
                            {item.risk_level}
                          </span>
                        </div>
                      </div>
                      <p className="mt-3 line-clamp-2 text-sm text-slate-400">
                        {item.description}
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-400">
                        <span className="rounded-full border border-slate-700 px-2 py-0.5">
                          {item.target_view || 'operations'}
                        </span>
                        <span className="rounded-full border border-slate-700 px-2 py-0.5">
                          {String(item.recommended_action || 'review').replace(/_/g, ' ')}
                        </span>
                        <span className="rounded-full border border-slate-700 px-2 py-0.5">
                          tasks {item.completed_task_count}/{item.task_count}
                        </span>
                        {item.manual_only_side_effects && (
                          <span className="rounded-full border border-emerald-700/60 px-2 py-0.5 text-emerald-300">
                            manual-only side effects
                          </span>
                        )}
                        {item.resolution_action && (
                          <span className="rounded-full border border-green-700/60 px-2 py-0.5 text-green-300">
                            {String(item.resolution_action).replace(/_/g, ' ')}
                          </span>
                        )}
                      </div>
                      {item.active_task?.error && (
                        <div className="mt-3 rounded-lg border border-red-900 bg-red-950/30 px-3 py-2 text-xs text-red-200">
                          {item.active_task.error}
                        </div>
                      )}
                      {item.owner_resolution ? (
                        <div className="mt-3 rounded-lg border border-green-900/40 bg-green-950/20 px-3 py-2 text-xs text-green-100">
                          <div className="font-medium">
                            {String(item.owner_resolution.action || 'reviewed').replace(/_/g, ' ')}
                            {' '}
                            by {item.owner_resolution.resolver || 'owner'}
                          </div>
                          {item.owner_resolution.note && (
                            <div className="mt-1 text-green-100/75">
                              {item.owner_resolution.note}
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="mt-3 space-y-2">
                          <textarea
                            value={followUpNotes[item.plan_id] || ''}
                            onChange={(event) => setFollowUpNotes((current) => ({
                              ...current,
                              [item.plan_id]: event.target.value,
                            }))}
                            rows={2}
                            maxLength={2000}
                            placeholder="Owner note"
                            className="w-full resize-none rounded-lg border border-slate-700 bg-slate-950/50 px-3 py-2 text-sm text-slate-100 outline-none focus:border-blue-500"
                          />
                          <div className="flex flex-wrap gap-2">
                            <button
                              onClick={() => resolveFollowUp(item.plan_id, 'reviewed')}
                              disabled={followUpAction !== null}
                              className="btn-secondary px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {followUpAction === `${item.plan_id}:reviewed`
                                ? 'Saving...'
                                : 'Mark Reviewed'}
                            </button>
                            <button
                              onClick={() => resolveFollowUp(item.plan_id, 'deferred')}
                              disabled={followUpAction !== null}
                              className="btn-secondary px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {followUpAction === `${item.plan_id}:deferred`
                                ? 'Saving...'
                                : 'Defer'}
                            </button>
                            <button
                              onClick={() => resolveFollowUp(item.plan_id, 'dismissed')}
                              disabled={followUpAction !== null}
                              className="btn-secondary px-3 py-1.5 text-xs text-red-200 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {followUpAction === `${item.plan_id}:dismissed`
                                ? 'Saving...'
                                : 'Dismiss'}
                            </button>
                          </div>
                        </div>
                      )}
                      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                        <div className="text-xs text-slate-500">
                          Updated {formatDate(item.updated_at)}
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {target && onNavigate && (
                            <button
                              onClick={() => onNavigate(target)}
                              className="btn-secondary px-3 py-1.5 text-xs"
                            >
                              Open {target}
                            </button>
                          )}
                          <button
                            onClick={() => executePlan(item.plan_id)}
                            disabled={
                              !canExecutePlan({ status: item.status })
                              || planAction !== null
                              || followUpAction !== null
                            }
                            className="btn-primary flex items-center gap-2 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <Play className="h-3.5 w-3.5" />
                            {planAction === item.plan_id ? 'Executing...' : 'Run Review'}
                          </button>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="rounded-lg border border-slate-800 bg-slate-900/30 px-4 py-8 text-center text-sm text-slate-500">
                {followUpsLoading
                  ? 'Loading cadence follow-ups...'
                  : followUpStatus === 'active'
                    ? 'No active cadence follow-ups. Completed operating loops will add review work here when needed.'
                    : `No ${followUpStatus.replace('_', ' ')} cadence follow-ups found.`}
              </div>
            )}
          </div>
        ) : (
          <div className="py-8 text-center text-slate-500">
            {followUpsLoading ? 'Loading cadence follow-ups...' : 'Cadence follow-up queue unavailable.'}
          </div>
        )}
      </section>

      <section className="card overflow-hidden">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-emerald-300" />
            <div>
              <h3 className="text-lg font-semibold">Autonomous Plans</h3>
              <p className="mt-1 text-sm text-slate-400">
                Durable plans generated from role gaps, memory findings, company context, and operating loops.
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
                      <div className="flex flex-wrap gap-2">
                        <button
                          onClick={() => executePlan(plan.id)}
                          disabled={!canExecutePlan(plan) || planAction !== null}
                          className="btn-secondary flex items-center gap-2 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <Play className="h-3.5 w-3.5" />
                          {planAction === plan.id ? 'Executing...' : 'Execute'}
                        </button>
                        {plan.source_type === 'company_context_snapshot' && onNavigate && (
                          <button
                            onClick={() => onNavigate('agents')}
                            className="btn-secondary px-3 py-1.5 text-xs"
                          >
                            Review Roles
                          </button>
                        )}
                      </div>
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

function ReadinessPanel({
  title,
  value,
  detail,
}: {
  title: string
  value: string
  detail: string
}) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/30 px-3 py-3">
      <div className="text-xs text-slate-500">{title}</div>
      <div className="mt-1 break-words text-sm font-semibold text-slate-100">{value}</div>
      <div className="mt-1 text-xs text-slate-400">{detail}</div>
    </div>
  )
}
