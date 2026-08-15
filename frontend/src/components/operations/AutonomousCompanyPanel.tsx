'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  BriefcaseBusiness,
  Clock3,
  GitFork,
  Hand,
  Pause,
  Play,
  RefreshCw,
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
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [changingDomain, setChangingDomain] = useState<string | null>(null)
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
      ] = await Promise.all([
        api.listAgentMandates({ status: 'active', limit: 200 }),
        api.listBusinessEvents({ limit: 100 }),
        api.listBusinessWorkItems({ limit: 100 }),
        api.listOutcomeAssessments({ limit: 100 }),
        api.listWorkflowSpecifications({ limit: 100 }),
        api.listDomainAutonomyControls(),
        api.getModelCapabilities(),
        api.listAutonomousActionCandidates({ limit: 100 }),
      ])
      setMandates(nextMandates.items || nextMandates || [])
      setEvents(nextEvents.items || nextEvents || [])
      setWork(nextWork.items || nextWork || [])
      setOutcomes(nextOutcomes.items || nextOutcomes || [])
      setSpecifications(nextSpecs.items || nextSpecs || [])
      setControls(nextControls.items || nextControls || [])
      setModelCapabilities(nextCapabilities)
      setActionCandidates(nextCandidates.items || nextCandidates || [])
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

  return (
    <section className="border-y border-slate-800 py-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2"><Activity className="h-5 w-5 text-cyan-300" /><h3 className="text-lg font-semibold">Autonomous Company Control Plane</h3></div>
          <p className="mt-1 text-sm text-slate-400">Evidence, mandates, durable work, workflows, outcomes, and owner domain control.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={load} disabled={loading} className="btn-secondary flex items-center gap-2 text-sm" title="Refresh control plane"><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />Refresh</button>
          <button type="button" onClick={runCycle} disabled={running} className="btn-primary flex items-center gap-2 text-sm"><Play className={`h-4 w-4 ${running ? 'animate-pulse' : ''}`} />{running ? 'Running...' : 'Run cycle'}</button>
        </div>
      </div>

      {error && <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}

      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Metric label="Model" value={section.company_model?.status || 'unavailable'} />
        <Metric label="Mandates" value={`${mandates.length}`} detail={section.mandates?.status} />
        <Metric label="Open work" value={openWork.length} detail={section.work_portfolio?.status} />
        <Metric label="Unexplained" value={section.business_events?.unexplained ?? '-'} />
        <Metric label="Workflows" value={specifications.length} />
        <Metric label="Outcomes" value={outcomes.length} />
        <Metric label="Owner control" value={pausedDomains} />
        <Metric label="Temporal" value={section.temporal_delivery?.status || 'unavailable'} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_1fr]">
        <div>
          <div className="flex items-center gap-2"><Hand className="h-4 w-4 text-blue-300" /><h4 className="font-medium text-slate-200">Domain authority</h4></div>
          <div className="mt-3 divide-y divide-slate-800 border-y border-slate-800">
            {controls.map((control) => (
              <div key={control.domain} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0"><p className="font-medium capitalize text-slate-200">{control.domain.replaceAll('_', ' ')}</p><p className="mt-0.5 truncate text-xs text-slate-500">{control.reason || 'Autonomous operation is active.'}</p><p className="mt-1 text-xs text-slate-600">{control.nonterminal_work_items ?? 0}/{control.backlog_limit ?? '-'} queued{control.recovery_required ? ' · grounded recovery required' : ''}</p></div>
                <div className="flex items-center gap-2">
                  <span className={`rounded-full border px-2 py-0.5 text-xs ${badge(control.state)}`}>{control.state}</span>
                  <select aria-label={`${control.domain} autonomy state`} value={control.state} disabled={changingDomain === control.domain} onChange={(event) => changeDomain(control.domain, event.target.value)} className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 focus:border-blue-500 focus:outline-none">
                    <option value="active">Autonomous</option><option value="paused">Paused</option><option value="takeover">Owner takeover</option>
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

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Health icon={ShieldCheck} title="Evidence and strategy" value={section.source_freshness?.status || 'unavailable'} detail={`${section.company_model?.critical_unknowns?.length || 0} critical unknowns · ${section.strategy?.status || 'strategy unavailable'}`} />
        <Health icon={GitFork} title="Workflow compiler" value={section.workflow_compiler?.status || 'unavailable'} detail={`${specifications.filter((item) => item.status === 'active').length} active immutable specifications`} />
        <Health icon={Clock3} title="Outcome learning" value={section.outcome_learning?.status || 'unavailable'} detail={`${section.outcome_learning?.unassessed_work || 0} unassessed · ${outcomes.filter((item) => item.recommendation === 'rollback').length} rollback recommendations · latest ${when(section.outcome_learning?.latest_assessment_at)}`} />
        <Health icon={BriefcaseBusiness} title="Portfolio bounds" value={section.work_portfolio?.status || 'unavailable'} detail={`${(section.work_portfolio?.saturated_domains || []).length} saturated · ${(section.work_portfolio?.recovery_required_domains || []).length} recovery required`} />
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
