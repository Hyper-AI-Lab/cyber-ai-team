'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Archive,
  Building2,
  CheckCircle2,
  Database,
  FlaskConical,
  GitCommitHorizontal,
  LockKeyhole,
  Pencil,
  RefreshCw,
  Search,
  ShieldAlert,
  Target,
  X,
} from 'lucide-react'
import { api } from '@/lib/api'

const states = ['all', 'verified', 'inferred', 'hypothesis', 'unknown', 'disputed']

function date(value?: string | null) {
  if (!value) return 'not recorded'
  return new Date(value).toLocaleString()
}

function statusTone(value?: string) {
  if (['active', 'ready', 'verified', 'completed', 'probation'].includes(value || '')) {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
  }
  if (['disputed', 'failed', 'blocked'].includes(value || '')) {
    return 'border-red-500/30 bg-red-500/10 text-red-200'
  }
  return 'border-amber-500/30 bg-amber-500/10 text-amber-200'
}

export default function CompanyView() {
  const [model, setModel] = useState<any | null>(null)
  const [sources, setSources] = useState<any[]>([])
  const [claims, setClaims] = useState<any[]>([])
  const [evidence, setEvidence] = useState<any[]>([])
  const [revisions, setRevisions] = useState<any[]>([])
  const [portfolio, setPortfolio] = useState<any | null>(null)
  const [kpis, setKpis] = useState<any[]>([])
  const [experiments, setExperiments] = useState<any[]>([])
  const [claimState, setClaimState] = useState('all')
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingClaim, setEditingClaim] = useState<any | null>(null)
  const [claimValue, setClaimValue] = useState('')
  const [claimReason, setClaimReason] = useState('')
  const [savingClaim, setSavingClaim] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const modelPromise = api.getLivingCompanyModel().catch((reason: any) => {
      if (`${reason.message}`.includes('404')) return null
      throw reason
    })
    try {
      const [nextModel, nextSources, nextClaims, nextEvidence, nextRevisions, nextPortfolio, nextKpis, nextExperiments] = await Promise.all([
        modelPromise,
        api.listCompanySources(),
        api.listCompanyClaims({ activeOnly: true, limit: 300 }),
        api.listCompanyEvidence(100),
        api.listCompanyModelRevisions(30),
        api.getCompanyStrategyPortfolio(),
        api.listCompanyKpiRevisions(100),
        api.listCompanyStrategyExperiments(undefined, 100),
      ])
      setModel(nextModel)
      setSources(Array.isArray(nextSources) ? nextSources : nextSources.items || [])
      setClaims(Array.isArray(nextClaims) ? nextClaims : nextClaims.items || [])
      setEvidence(Array.isArray(nextEvidence) ? nextEvidence : nextEvidence.items || [])
      setRevisions(Array.isArray(nextRevisions) ? nextRevisions : nextRevisions.items || [])
      setPortfolio(nextPortfolio)
      setKpis(Array.isArray(nextKpis) ? nextKpis : nextKpis.items || [])
      setExperiments(Array.isArray(nextExperiments) ? nextExperiments : nextExperiments.items || [])
    } catch (reason: any) {
      setError(reason.message || 'Company intelligence is unavailable.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const refreshModel = async () => {
    setRunning(true)
    setError(null)
    try {
      await api.discoverCompany({ acquire: true, activate_if_ready: true })
      await api.runCompanyStrategy()
      await load()
    } catch (reason: any) {
      setError(reason.message || 'Company discovery failed.')
    } finally {
      setRunning(false)
    }
  }

  const visibleClaims = useMemo(
    () => claims.filter((claim) => claimState === 'all' || claim.epistemic_state === claimState),
    [claims, claimState],
  )
  const company = model?.model || {}
  const objectives = portfolio?.objectives || []

  const openClaimEditor = (claim: any) => {
    setEditingClaim(claim)
    setClaimValue(JSON.stringify(claim.value, null, 2))
    setClaimReason('')
  }

  const saveClaimRevision = async () => {
    if (!editingClaim || !claimReason.trim()) return
    setSavingClaim(true)
    setError(null)
    try {
      const value = JSON.parse(claimValue)
      if (!value || Array.isArray(value) || typeof value !== 'object') {
        throw new Error('Claim value must be a JSON object.')
      }
      await api.reviseCompanyClaim(editingClaim.id, value, claimReason.trim())
      setEditingClaim(null)
      await load()
    } catch (reason: any) {
      setError(reason.message || 'Owner correction could not be recorded.')
    } finally {
      setSavingClaim(false)
    }
  }

  return (
    <div className="space-y-7">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <Building2 className="h-7 w-7 text-blue-400" />
            <h2 className="text-2xl font-bold">Company</h2>
          </div>
          <p className="mt-1 text-slate-400">{company.legal_name || company.name || 'Company identity not yet verified'}</p>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary flex items-center gap-2" onClick={load} disabled={loading} title="Refresh">
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button className="btn-primary flex items-center gap-2" onClick={refreshModel} disabled={running}>
            <Search className={`h-4 w-4 ${running ? 'animate-pulse' : ''}`} />
            {running ? 'Discovering...' : 'Discover'}
          </button>
        </div>
      </header>

      {error && <div className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}

      {model ? (
        <section className="border-y border-slate-800 py-5">
          <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
            <div>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className={`rounded-full border px-2.5 py-1 text-xs ${statusTone(model.status)}`}>{model.status}</span>
                <span className="text-xs text-slate-500">revision {model.revision}</span>
                <span className="text-xs text-slate-500">activated {date(model.activated_at)}</span>
              </div>
              <p className="max-w-3xl text-sm leading-6 text-slate-300">
                {company.business_description || 'Business description remains unknown.'}
              </p>
              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                <FactList title="Offerings" values={company.offerings} />
                <FactList title="Customer segments" values={company.customer_segments} />
                <FactList title="Channels" values={company.channels} />
                <FactList title="Jurisdictions" values={company.jurisdictions} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Metric label="Confidence" value={`${Math.round((model.confidence || 0) * 100)}%`} />
              <Metric label="Provenance" value={`${Math.round((model.provenance_coverage || 0) * 100)}%`} />
              <Metric label="Unknowns" value={model.unknowns?.length || 0} />
              <Metric label="Disputes" value={model.disputes?.length || 0} />
            </div>
          </div>
          {(model.unknowns?.length > 0 || model.disputes?.length > 0) && (
            <div className="mt-5 grid gap-4 md:grid-cols-2">
              <StateList icon={ShieldAlert} title="Unknowns" values={model.unknowns || []} tone="amber" />
              <StateList icon={GitCommitHorizontal} title="Disputes" values={model.disputes || []} tone="red" />
            </div>
          )}
        </section>
      ) : (
        <EmptyState title="No activated company model" detail="Evidence acquisition has not yet produced a model that passes provenance, confidence, schema, and Observer gates." />
      )}

      <section>
        <SectionTitle icon={Database} title="Evidence Sources" detail={`${sources.length} registered`} />
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {sources.map((source) => (
            <article key={source.id} className="rounded-md border border-slate-800 bg-slate-900/60 p-4">
              <div className="flex items-center justify-between gap-3">
                <h4 className="font-medium text-slate-200">{source.name}</h4>
                <span className={`rounded-full border px-2 py-0.5 text-xs ${statusTone(source.status)}`}>{source.status}</span>
              </div>
              <div className="mt-3 space-y-1 text-xs text-slate-400">
                <p>{source.source_type} · {source.trust_class}</p>
                <p>Last success: {date(source.last_success_at)}</p>
                {source.last_error && <p className="text-red-300">{source.last_error}</p>}
              </div>
            </article>
          ))}
          {!sources.length && <EmptyInline text="No company evidence sources are registered." />}
        </div>
      </section>

      <section>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <SectionTitle icon={CheckCircle2} title="Claims" detail={`${visibleClaims.length} visible`} />
          <div className="flex flex-wrap gap-1" aria-label="Claim state filter">
            {states.map((state) => (
              <button key={state} onClick={() => setClaimState(state)} className={`rounded-md px-2.5 py-1.5 text-xs ${claimState === state ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}>
                {state}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-3 overflow-x-auto rounded-md border border-slate-800">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-slate-900 text-xs uppercase text-slate-500"><tr><th className="px-4 py-3">Claim</th><th className="px-4 py-3">State</th><th className="px-4 py-3">Confidence</th><th className="px-4 py-3">Evidence</th><th className="px-4 py-3">Authority</th><th className="w-14 px-4 py-3"><span className="sr-only">Edit</span></th></tr></thead>
            <tbody className="divide-y divide-slate-800">
              {visibleClaims.map((claim) => (
                <tr key={claim.id} className="bg-slate-950/30">
                  <td className="px-4 py-3"><p className="font-medium text-slate-200">{claim.predicate}</p><p className="mt-1 max-w-lg truncate text-xs text-slate-500">{JSON.stringify(claim.value)}</p></td>
                  <td className="px-4 py-3"><span className={`rounded-full border px-2 py-1 text-xs ${statusTone(claim.epistemic_state)}`}>{claim.epistemic_state}</span></td>
                  <td className="px-4 py-3 text-slate-300">{Math.round((claim.confidence || 0) * 100)}%</td>
                  <td className="px-4 py-3 text-slate-400">{claim.evidence_ids?.length || 0}</td>
                  <td className="px-4 py-3 text-slate-400">{claim.owner_locked ? <span className="inline-flex items-center gap-1 text-blue-300"><LockKeyhole className="h-3.5 w-3.5" /> owner</span> : claim.trust_class}</td>
                  <td className="px-4 py-3"><button type="button" onClick={() => openClaimEditor(claim)} className="rounded-md p-2 text-slate-400 hover:bg-slate-800 hover:text-white" title="Create owner-locked correction" aria-label={`Correct ${claim.predicate}`}><Pencil className="h-4 w-4" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!visibleClaims.length && <div className="px-4 py-8 text-center text-sm text-slate-500">No claims match this epistemic state.</div>}
        </div>
      </section>

      <section>
        <SectionTitle icon={Target} title="Strategy Portfolio" detail={`${objectives.length} objectives`} />
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          {objectives.map((objective: any) => (
            <article key={objective.id} className="rounded-md border border-slate-800 bg-slate-900/60 p-4">
              <div className="flex items-start justify-between gap-3"><h4 className="font-medium text-slate-100">{objective.title}</h4><span className={`rounded-full border px-2 py-0.5 text-xs ${statusTone(objective.status)}`}>{objective.status}</span></div>
              <p className="mt-2 text-sm text-slate-400">{objective.description || objective.rationale}</p>
              <p className="mt-3 text-xs text-slate-500">confidence {Math.round((objective.confidence || 0) * 100)}% · priority {objective.priority}</p>
            </article>
          ))}
          {!objectives.length && <EmptyInline text="No evidence-backed objectives have entered probation." />}
        </div>
        <div className="mt-5 grid gap-5 xl:grid-cols-2">
          <ListBand icon={GitCommitHorizontal} title="KPI revisions" items={kpis} primary="formula" secondary="status" />
          <ListBand icon={FlaskConical} title="Experiments" items={experiments} primary="title" secondary="status" />
        </div>
      </section>

      <section>
        <SectionTitle icon={Archive} title="Revision History" detail={`${revisions.length} models · ${evidence.length} evidence artifacts`} />
        <div className="mt-3 divide-y divide-slate-800 border-y border-slate-800">
          {revisions.slice(0, 10).map((revision) => (
            <div key={revision.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
              <div><span className="font-medium text-slate-200">Revision {revision.revision}</span><span className="ml-2 text-slate-500">{revision.source_hash?.slice(0, 12)}</span></div>
              <div className="flex items-center gap-3 text-xs text-slate-400"><span className={`rounded-full border px-2 py-0.5 ${statusTone(revision.status)}`}>{revision.status}</span><span>{date(revision.created_at)}</span></div>
            </div>
          ))}
          {!revisions.length && <EmptyInline text="No company model revisions are recorded." />}
        </div>
      </section>

      {editingClaim && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-labelledby="claim-editor-title">
          <div className="w-full max-w-2xl rounded-md border border-slate-700 bg-slate-950 p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-3">
              <div><h3 id="claim-editor-title" className="text-lg font-semibold">Owner correction</h3><p className="mt-1 text-sm text-slate-400">Creates a new locked revision and preserves prior claim history.</p></div>
              <button type="button" onClick={() => setEditingClaim(null)} className="rounded-md p-2 text-slate-400 hover:bg-slate-800 hover:text-white" title="Close"><X className="h-4 w-4" /></button>
            </div>
            <label className="mt-5 block text-sm text-slate-300">Claim value<textarea value={claimValue} onChange={(event) => setClaimValue(event.target.value)} rows={9} className="mt-2 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-sm text-slate-200 focus:border-blue-500 focus:outline-none" spellCheck={false} /></label>
            <label className="mt-4 block text-sm text-slate-300">Correction evidence or reason<textarea value={claimReason} onChange={(event) => setClaimReason(event.target.value)} rows={3} className="mt-2 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 focus:border-blue-500 focus:outline-none" /></label>
            <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setEditingClaim(null)} className="btn-secondary">Cancel</button><button type="button" onClick={saveClaimRevision} disabled={savingClaim || !claimReason.trim()} className="btn-primary">{savingClaim ? 'Recording...' : 'Record correction'}</button></div>
          </div>
        </div>
      )}
    </div>
  )
}

function SectionTitle({ icon: Icon, title, detail }: { icon: any; title: string; detail: string }) {
  return <div className="flex items-center gap-2"><Icon className="h-5 w-5 text-slate-400" /><h3 className="text-lg font-semibold">{title}</h3><span className="text-xs text-slate-500">{detail}</span></div>
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded-md border border-slate-800 bg-slate-900/60 p-4"><p className="text-xs uppercase text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold text-slate-100">{value}</p></div>
}

function FactList({ title, values }: { title: string; values?: any[] }) {
  return <div><h4 className="text-xs uppercase text-slate-500">{title}</h4><div className="mt-2 flex flex-wrap gap-1.5">{values?.length ? values.map((value, index) => <span key={`${title}-${index}`} className="rounded-md bg-slate-800 px-2 py-1 text-xs text-slate-300">{typeof value === 'string' ? value : value.name || value.title || JSON.stringify(value)}</span>) : <span className="text-sm text-slate-500">Unknown</span>}</div></div>
}

function StateList({ icon: Icon, title, values, tone }: { icon: any; title: string; values: string[]; tone: 'amber' | 'red' }) {
  const style = tone === 'red' ? 'border-red-500/30 bg-red-500/10 text-red-200' : 'border-amber-500/30 bg-amber-500/10 text-amber-200'
  return <div className={`rounded-md border p-4 ${style}`}><div className="flex items-center gap-2 text-sm font-medium"><Icon className="h-4 w-4" />{title}</div><div className="mt-2 flex flex-wrap gap-1.5">{values.map((value) => <span key={value} className="rounded bg-black/10 px-2 py-1 text-xs">{value}</span>)}</div></div>
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="rounded-md border border-dashed border-slate-700 px-5 py-12 text-center"><h3 className="font-medium text-slate-300">{title}</h3><p className="mx-auto mt-2 max-w-xl text-sm text-slate-500">{detail}</p></div>
}

function EmptyInline({ text }: { text: string }) {
  return <div className="col-span-full px-4 py-8 text-center text-sm text-slate-500">{text}</div>
}

function ListBand({ icon: Icon, title, items, primary, secondary }: { icon: any; title: string; items: any[]; primary: string; secondary: string }) {
  return <div><SectionTitle icon={Icon} title={title} detail={`${items.length} recorded`} /><div className="mt-2 divide-y divide-slate-800 border-y border-slate-800">{items.slice(0, 8).map((item) => <div key={item.id} className="flex items-center justify-between gap-3 py-3 text-sm"><span className="truncate text-slate-300">{item[primary] || item.key || item.id}</span><span className={`rounded-full border px-2 py-0.5 text-xs ${statusTone(item[secondary])}`}>{item[secondary]}</span></div>)}{!items.length && <EmptyInline text={`No ${title.toLowerCase()} recorded.`} />}</div></div>
}
