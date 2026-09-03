'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  BriefcaseBusiness,
  Clock3,
  GitFork,
  Hand,
  Layers3,
  LockKeyhole,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import { api } from '@/lib/api'
import ActionPolicyValidationPanel from './ActionPolicyValidationPanel'
import OutcomeAutonomyPanel from './OutcomeAutonomyPanel'

interface Props {
  readiness?: any
  onChanged?: () => Promise<void> | void
  onNavigate?: (view: 'approvals') => void
}

const tone: Record<string, string> = {
  active: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  completed: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  ready: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  owner_controlled: 'border-blue-500/30 bg-blue-500/10 text-blue-200',
  takeover: 'border-blue-500/30 bg-blue-500/10 text-blue-200',
  paused: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  pending: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  blocked: 'border-red-500/30 bg-red-500/10 text-red-200',
  failed: 'border-red-500/30 bg-red-500/10 text-red-200',
  recovery_required: 'border-red-500/30 bg-red-500/10 text-red-200',
  backlog_saturated: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  bounded: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
}

function badge(value: string) {
  return tone[value] || 'border-slate-700 bg-slate-800 text-slate-300'
}

function when(value?: string | null) {
  return value ? new Date(value).toLocaleString() : 'not recorded'
}

export default function AutonomousCompanyPanel({ readiness, onChanged, onNavigate }: Props) {
  const [mandates, setMandates] = useState<any[]>([])
  const [events, setEvents] = useState<any[]>([])
  const [work, setWork] = useState<any[]>([])
  const [outcomes, setOutcomes] = useState<any[]>([])
  const [specifications, setSpecifications] = useState<any[]>([])
  const [controls, setControls] = useState<any[]>([])
  const [modelCapabilities, setModelCapabilities] = useState<any | null>(null)
  const [actionCandidates, setActionCandidates] = useState<any[]>([])
  const [operatingModel, setOperatingModel] = useState<any | null>(null)
  const [reconciliationRuns, setReconciliationRuns] = useState<any[]>([])
  const [lifecycleAssessments, setLifecycleAssessments] = useState<any[]>([])
  const [discoveryObligations, setDiscoveryObligations] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [reconciling, setReconciling] = useState(false)
  const [changingDomain, setChangingDomain] = useState<string | null>(null)
  const [retryingDiscovery, setRetryingDiscovery] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [
        nextMandates,
        nextEvents,
        nextWork,
        nextOutcomes,
        nextSpecs,
        nextControls,
        nextCapabilities,
        nextCandidates,
        nextOperatingModel,
        nextReconciliationRuns,
        nextLifecycleAssessments,
        nextDiscoveryObligations,
      ] = await Promise.all([
        api.listAgentMandates({ status: 'active', limit: 200 }),
        api.listBusinessEvents({ limit: 100 }),
        api.listBusinessWorkItems({ limit: 100 }),
        api.listOutcomeAssessments({ limit: 100 }),
        api.listWorkflowSpecifications({ limit: 100 }),
        api.listDomainAutonomyControls(),
        api.getModelCapabilities(),
        api.listAutonomousActionCandidates({ limit: 100 }),
        api.getOperatingModel(),
        api.listOperatingModelReconciliationRuns(50),
        api.listOperatingModelLifecycleAssessments({ limit: 100 }),
        api.listOperatingModelDiscoveryObligations({ limit: 100 }),
      ])
      setMandates(nextMandates.items || nextMandates || [])
      setEvents(nextEvents.items || nextEvents || [])
      setWork(nextWork.items || nextWork || [])
      setOutcomes(nextOutcomes.items || nextOutcomes || [])
      setSpecifications(nextSpecs.items || nextSpecs || [])
      setControls(nextControls.items || nextControls || [])
      setModelCapabilities(nextCapabilities)
      setActionCandidates(nextCandidates.items || nextCandidates || [])
      setOperatingModel(nextOperatingModel)
      setReconciliationRuns(nextReconciliationRuns.items || [])
      setLifecycleAssessments(nextLifecycleAssessments.items || [])
      setDiscoveryObligations(nextDiscoveryObligations.items || [])
    } catch (reason: any) {
      setError(reason.message || 'Autonomous company control plane is unavailable.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const runCycle = async () => {
    setRunning(true)
    setError(null)
    try {
      await api.runAutonomousCompanyCycle()
      await load()
      await onChanged?.()
    } catch (reason: any) {
      setError(reason.message || 'Autonomous company cycle failed.')
    } finally {
      setRunning(false)
    }
  }

  const previewReconciliation = async () => {
    setReconciling(true)
    setError(null)
    try {
      await api.reconcileOperatingModel(true)
      await load()
      await onChanged?.()
    } catch (reason: any) {
      setError(reason.message || 'Operating-model dry run failed.')
    } finally {
      setReconciling(false)
    }
  }

  const retryDiscovery = async (obligationId: string) => {
    setRetryingDiscovery(obligationId)
    setError(null)
    try {
      await api.retryOperatingModelDiscoveryObligation(obligationId, true)
      await load()
      await onChanged?.()
    } catch (reason: any) {
      setError(reason.message || 'The discovery obligation could not be retried.')
    } finally {
      setRetryingDiscovery(null)
    }
  }

  const changeDomain = async (domain: string, state: string) => {
    setChangingDomain(domain)
    setError(null)
    try {
      const reason = state === 'active'
        ? 'Owner returned this domain to autonomous operation.'
        : state === 'takeover'
          ? 'Owner has taken direct control of this domain.'
          : 'Owner paused autonomous work in this domain.'
      await api.updateDomainAutonomyControl(domain, state, reason)
      await load()
      await onChanged?.()
    } catch (failure: any) {
      setError(failure.message || `Could not update ${domain}.`)
    } finally {
      setChangingDomain(null)
    }
  }

  const section = readiness?.autonomous_company?.sections || {}
  const openWork = useMemo(
    () => work.filter((item) => !['completed', 'cancelled'].includes(item.status)),
    [work],
  )
  const pausedDomains = controls.filter((item) => item.state !== 'active').length
  const desiredDomains = operatingModel?.domain_keys || []
  const actualDomains = operatingModel?.actual_domains || []
  const effectiveDomains = actualDomains.filter(
    (item: any) => item.effective_state === 'active',
  )
  const shadowDomains = actualDomains.filter(
    (item: any) => item.lifecycle_state === 'shadow',
  )
  const ownerLocks = controls.filter((item) => item.owner_locked).length
  const ownerDiscovery = discoveryObligations.filter(
    (item) => item.status === 'owner_review',
  )
  const latestReconciliation = reconciliationRuns[0]
  const cleanupAssessments = lifecycleAssessments.filter(
    (item) => ['resolved', 'superseded'].includes(item.lifecycle_status),
  )

  return (
    <section className="border-y border-slate-800 py-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2"><Activity className="h-5 w-5 text-cyan-300" /><h3 className="text-lg font-semibold">Autonomous Company Control Plane</h3></div>
          <p className="mt-1 text-sm text-slate-400">Evidence, mandates, durable work, workflows, outcomes, and owner domain control.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={load} disabled={loading} className="btn-secondary flex items-center gap-2 text-sm" title="Refresh control plane"><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />Refresh</button>
          <button type="button" onClick={previewReconciliation} disabled={reconciling} className="btn-secondary flex items-center gap-2 text-sm" title="Preview desired versus actual changes without applying them"><GitFork className={`h-4 w-4 ${reconciling ? 'animate-pulse' : ''}`} />{reconciling ? 'Checking...' : 'Preview reconciliation'}</button>
          <button type="button" onClick={runCycle} disabled={running} className="btn-primary flex items-center gap-2 text-sm"><Play className={`h-4 w-4 ${running ? 'animate-pulse' : ''}`} />{running ? 'Running...' : 'Run cycle'}</button>
        </div>
      </div>

      {error && <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}

      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Metric label="Operating model" value={operatingModel?.status || 'not created'} detail={operatingModel?.id} />
        <Metric label="Desired domains" value={desiredDomains.length} />
        <Metric label="Effective domains" value={effectiveDomains.length} />
        <Metric label="In shadow" value={shadowDomains.length} />
        <Metric label="Mandates" value={`${mandates.length}`} detail={section.mandates?.status} />
        <Metric label="Open work" value={openWork.length} detail={section.work_portfolio?.status} />
        <Metric label="Discovery review" value={ownerDiscovery.length} />
        <Metric label="Owner locks" value={ownerLocks || pausedDomains} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_1fr]">
        <div>
          <div className="flex items-center gap-2"><Hand className="h-4 w-4 text-blue-300" /><h4 className="font-medium text-slate-200">Desired versus effective domains</h4></div>
          <div className="mt-3 divide-y divide-slate-800 border-y border-slate-800">
            {controls.map((control) => (
              <div key={control.domain} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium capitalize text-slate-200">{control.domain.replaceAll('_', ' ')}</p>
                    {control.owner_locked && <LockKeyhole className="h-3.5 w-3.5 text-blue-300" aria-label="Owner locked" />}
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500">{control.transition_reason || control.reason || 'Released to autonomous reconciliation.'}</p>
                  <DomainLifecycleDetails control={control} />
                  {control.source_revision && <p className="mt-1 truncate text-xs text-slate-700">source {control.source_revision}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <span className={`rounded-full border px-2 py-0.5 text-xs ${badge(control.lifecycle_state || control.state)}`}>{control.lifecycle_state || control.state}</span>
                  <select aria-label={`${control.domain} autonomy state`} value={control.state} disabled={changingDomain === control.domain} onChange={(event) => changeDomain(control.domain, event.target.value)} className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 focus:border-blue-500 focus:outline-none">
                    <option value="active">Release to autonomy</option><option value="paused">Paused</option><option value="takeover">Owner takeover</option>
                  </select>
                </div>
              </div>
            ))}
            {!controls.length && <Empty text={loading ? 'Loading domain controls...' : 'No domain controls are available.'} />}
          </div>
        </div>

        <div>
          <div className="flex items-center gap-2"><BriefcaseBusiness className="h-4 w-4 text-emerald-300" /><h4 className="font-medium text-slate-200">Work portfolio</h4></div>
          <div className="mt-3 divide-y divide-slate-800 border-y border-slate-800">
            {work.slice(0, 8).map((item) => (
              <div key={item.id} className="py-3">
                <div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-sm font-medium text-slate-200">{item.title}</p><p className="mt-0.5 text-xs text-slate-500">{item.work_type} · {item.assigned_agent_id || 'unassigned'}</p></div><span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${badge(item.status)}`}>{item.status}</span></div>
              </div>
            ))}
            {!work.length && <Empty text={loading ? 'Loading work portfolio...' : 'Nothing is waiting. Every observed signal is currently accounted for.'} />}
          </div>
        </div>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_1fr]">
        <div>
          <div className="flex items-center gap-2"><Search className="h-4 w-4 text-cyan-300" /><h4 className="font-medium text-slate-200">Discovery obligations</h4></div>
          <p className="mt-1 text-xs text-slate-500">Important unknowns stay visible until evidence resolves them or permitted sources are exhausted.</p>
          <div className="mt-3 divide-y divide-slate-800 border-y border-slate-800">
            {discoveryObligations.slice(0, 10).map((item) => (
              <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2"><p className="text-sm font-medium text-slate-200">{item.question}</p><span className={`rounded-full border px-2 py-0.5 text-xs ${badge(item.status)}`}>{item.status.replaceAll('_', ' ')}</span></div>
                  <p className="mt-1 text-xs text-slate-500">{item.predicate} · {item.attempts}/{item.max_attempts} attempts · {(item.source_types || []).join(', ') || 'no permitted source'}</p>
                  {item.last_error && <p className="mt-1 text-xs text-red-300">{item.last_error}</p>}
                  {item.next_attempt_at && <p className="mt-1 text-xs text-slate-600">next attempt {when(item.next_attempt_at)}</p>}
                </div>
                {!['resolved', 'superseded'].includes(item.status) && (
                  <button type="button" onClick={() => retryDiscovery(item.id)} disabled={retryingDiscovery !== null} className="btn-secondary flex items-center gap-2 text-sm" title="Retry permitted evidence sources now"><RotateCcw className={`h-4 w-4 ${retryingDiscovery === item.id ? 'animate-spin' : ''}`} />Retry</button>
                )}
              </div>
            ))}
            {!discoveryObligations.length && <Empty text={loading ? 'Loading discovery obligations...' : 'No unknown fact requires discovery. Current claims have a complete disposition.'} />}
          </div>
        </div>

        <div>
          <div className="flex items-center gap-2"><Layers3 className="h-4 w-4 text-violet-300" /><h4 className="font-medium text-slate-200">Lifecycle reconciliation</h4></div>
          <p className="mt-1 text-xs text-slate-500">Recent convergence and cleanup decisions remain linked to their operating-model revision.</p>
          <div className="mt-3 border-y border-slate-800 py-3">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <Evidence label="Latest run" value={latestReconciliation?.status || 'not recorded'} />
              <Evidence label="Run time" value={when(latestReconciliation?.completed_at || latestReconciliation?.created_at)} />
              <Evidence label="Assessments" value={String(lifecycleAssessments.length)} />
              <Evidence label="Resolved / superseded" value={String(cleanupAssessments.length)} />
            </div>
            {latestReconciliation?.errors?.length > 0 && <p className="mt-3 text-xs text-red-300">{latestReconciliation.errors.join('; ')}</p>}
            <div className="mt-3 divide-y divide-slate-800 border-t border-slate-800">
              {lifecycleAssessments.slice(0, 6).map((item) => (
                <div key={item.id} className="py-2.5">
                  <div className="flex items-center justify-between gap-2"><p className="truncate text-xs font-medium text-slate-300">{item.resource_type}: {item.resource_id}</p><span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${badge(item.lifecycle_status)}`}>{item.lifecycle_status}</span></div>
                  <p className="mt-1 text-xs text-slate-600">{item.reason}</p>
                </div>
              ))}
              {!lifecycleAssessments.length && <Empty text={loading ? 'Loading lifecycle decisions...' : 'No backlog cleanup decision has been required yet.'} />}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Health icon={ShieldCheck} title="Operating model" value={section.operating_model?.status || operatingModel?.status || 'unavailable'} detail={`${desiredDomains.length} desired · ${effectiveDomains.length} effective · ${shadowDomains.length} shadow`} />
        <Health icon={Search} title="Discovery closure" value={section.discovery_obligations?.status || 'unavailable'} detail={`${discoveryObligations.length} tracked · ${ownerDiscovery.length} require owner facts`} />
        <Health icon={GitFork} title="Workflow compiler" value={section.workflow_compiler?.status || 'unavailable'} detail={`${specifications.filter((item) => item.status === 'active').length} active immutable specifications`} />
        <Health icon={Clock3} title="Outcome learning" value={section.outcome_learning?.status || 'unavailable'} detail={`${section.outcome_learning?.unassessed_work || 0} unassessed · ${outcomes.filter((item) => item.recommendation === 'rollback').length} rollback recommendations · latest ${when(section.outcome_learning?.latest_assessment_at)}`} />
      </div>

      {events.some((item) => item.status === 'pending') && <div className="mt-4 flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100"><TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /><span>Pending events are waiting for outbox delivery or mandate routing. The next Temporal cycle will reconcile them.</span></div>}
      {section.work_portfolio?.blocking && <div className="mt-4 flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-100"><TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /><span>{section.work_portfolio.detail || 'The work portfolio requires bounded recovery before domain expansion.'}</span></div>}
      <OutcomeAutonomyPanel
        readiness={section}
        modelCapabilities={modelCapabilities}
        actionCandidates={actionCandidates}
        outcomes={outcomes}
        loading={loading}
        onNavigate={onNavigate}
      />
      <ActionPolicyValidationPanel onChanged={onChanged} />
    </section>
  )
}

function Metric({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  return <div className="rounded-md border border-slate-800 bg-slate-900/50 p-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 truncate text-lg font-semibold text-slate-100">{value}</p>{detail && <p className="mt-1 truncate text-xs text-slate-500">{detail}</p>}</div>
}

function Health({ icon: Icon, title, value, detail }: { icon: any; title: string; value: string; detail: string }) {
  return <div className="rounded-md border border-slate-800 bg-slate-900/50 p-4"><div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2"><Icon className="h-4 w-4 text-slate-400" /><span className="text-sm font-medium text-slate-200">{title}</span></div><span className={`rounded-full border px-2 py-0.5 text-xs ${badge(value)}`}>{value}</span></div><p className="mt-3 text-xs leading-5 text-slate-500">{detail}</p></div>
}

function Empty({ text }: { text: string }) {
  return <div className="py-8 text-center text-sm text-slate-500">{text}</div>
}

function Evidence({ label, value }: { label: string; value: string }) {
  return <div><p className="text-slate-600">{label}</p><p className="mt-1 break-words text-slate-300">{value}</p></div>
}

export function DomainLifecycleDetails({ control }: { control: any }) {
  return (
    <p className="mt-1 text-xs text-slate-600">
      desired {control.desired_state || 'active'} · effective {control.effective_state || control.state} · lifecycle {control.lifecycle_state || 'active'} · {control.nonterminal_work_items ?? 0}/{control.backlog_limit ?? '-'} queued
      {control.shadow_progress ? ` · shadow ${control.shadow_progress.successes}/${control.shadow_progress.required_successes}` : ''}
      {control.recovery_required ? ' · grounded recovery required' : ''}
    </p>
  )
}
