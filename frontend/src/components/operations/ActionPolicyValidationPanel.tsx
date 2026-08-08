'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  FlaskConical,
  Play,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  XCircle,
} from 'lucide-react'
import { api } from '@/lib/api'

const actionClasses = ['communications', 'erpnext'] as const

const statusTone: Record<string, string> = {
  active: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  validated: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  shadow: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  evaluated: 'border-blue-500/30 bg-blue-500/10 text-blue-200',
  failed: 'border-red-500/30 bg-red-500/10 text-red-200',
  awaiting_owner_approval: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  pending_owner_adjudication: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200',
}

function badge(value: string) {
  return statusTone[value] || 'border-slate-700 bg-slate-800 text-slate-300'
}

export default function ActionPolicyValidationPanel({ onChanged }: {
  onChanged?: () => Promise<void> | void
}) {
  const [policies, setPolicies] = useState<any[]>([])
  const [cases, setCases] = useState<Record<string, any[]>>({})
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [policyResponse, communications, erpnext] = await Promise.all([
        api.listActionClassPolicies(),
        api.listActionPolicyValidationCases('communications', { limit: 50 }),
        api.listActionPolicyValidationCases('erpnext', { limit: 50 }),
      ])
      setPolicies(policyResponse.items || [])
      setCases({
        communications: communications.items || [],
        erpnext: erpnext.items || [],
      })
    } catch (reason: any) {
      setError(reason.message || 'Action-policy evidence is unavailable.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const policiesByClass = useMemo(
    () => Object.fromEntries(policies.map((item) => [item.action_class, item])),
    [policies],
  )

  const generate = async (actionClass: string) => {
    setRunning(actionClass)
    setError(null)
    try {
      await api.generateActionPolicyShadowSuite(actionClass)
      await load()
      await onChanged?.()
    } catch (reason: any) {
      setError(reason.message || `Could not validate ${actionClass}.`)
    } finally {
      setRunning(null)
    }
  }

  const executeCanary = async (caseId: string) => {
    setRunning(`execute:${caseId}`)
    setError(null)
    try {
      await api.executeActionPolicyLiveCanary(caseId)
      await load()
      await onChanged?.()
    } catch (reason: any) {
      setError(reason.message || 'The live canary could not execute.')
    } finally {
      setRunning(null)
    }
  }

  const adjudicateCanary = async (caseId: string, compliant: boolean) => {
    const confirmed = window.confirm(
      compliant
        ? 'Confirm that the canary produced the expected external result?'
        : 'Mark this canary as failed policy evidence?',
    )
    if (!confirmed) return
    setRunning(`adjudicate:${caseId}`)
    setError(null)
    try {
      await api.adjudicateActionPolicyLiveCanary(caseId, {
        compliant,
        evaluator_score: compliant ? 1 : 0,
        note: compliant
          ? 'Owner confirmed the expected external result.'
          : 'Owner marked the live canary as failed.',
      })
      await load()
      await onChanged?.()
    } catch (reason: any) {
      setError(reason.message || 'The canary result could not be recorded.')
    } finally {
      setRunning(null)
    }
  }

  return (
    <section className="mt-6 border-t border-slate-800 pt-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-cyan-300" />
            <h4 className="font-medium text-slate-200">External action validation</h4>
          </div>
          <p className="mt-1 text-xs text-slate-500">Shadow evidence, live canaries, and promotion gates.</p>
        </div>
        <button type="button" onClick={load} disabled={loading} className="btn-secondary flex items-center gap-2 text-sm" title="Refresh action evidence">
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />Refresh
        </button>
      </div>

      {error && <div className="mt-4 flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-100"><TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}

      <div className="mt-4 divide-y divide-slate-800 border-y border-slate-800">
        {actionClasses.map((actionClass) => {
          const policy = policiesByClass[actionClass] || {}
          const items = cases[actionClass] || []
          const liveCases = items.filter((item) => item.mode === 'live_canary')
          const validated = items.filter((item) => item.status === 'validated').length
          const failed = items.filter((item) => item.status === 'failed').length
          return (
            <div key={actionClass} className="py-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-medium capitalize text-slate-200">{actionClass}</p>
                    <span className={`rounded-full border px-2 py-0.5 text-xs ${badge(policy.status || 'unavailable')}`}>{policy.status || 'unavailable'}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {policy.shadow_validated_cases || 0}/{policy.required_validated_cases || 10} shadow · {policy.live_canary_cases || 0}/{policy.required_live_canaries || 1} live · score {Number(policy.evaluator_score || 0).toFixed(2)}
                  </p>
                </div>
                <button type="button" onClick={() => generate(actionClass)} disabled={running !== null} className="btn-secondary flex items-center gap-2 text-sm">
                  <FlaskConical className={`h-4 w-4 ${running === actionClass ? 'animate-pulse' : ''}`} />
                  {running === actionClass ? 'Validating...' : 'Run shadow suite'}
                </button>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-3 text-xs">
                <EvidenceMetric label="Recorded" value={items.length} />
                <EvidenceMetric label="Validated" value={validated} />
                <EvidenceMetric label="Failed" value={failed} tone={failed ? 'text-red-300' : 'text-slate-200'} />
              </div>
              {items.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {items.slice(0, 10).map((item) => (
                    <span key={item.id} title={(item.policy_decision?.reasons || []).join(', ')} className={`rounded-full border px-2 py-1 text-xs ${badge(item.status)}`}>
                      {item.scenario_key.replaceAll('_', ' ')}
                    </span>
                  ))}
                </div>
              )}
              {liveCases.map((item) => (
                <div key={`live:${item.id}`} className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-slate-800 pt-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-slate-300">{item.scenario_key.replaceAll('_', ' ')}</p>
                    <p className="mt-1 text-xs text-slate-500">{item.status.replaceAll('_', ' ')} · approval {item.approval_id ? 'linked' : 'missing'}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {item.status === 'awaiting_owner_approval' && (
                      <button type="button" onClick={() => executeCanary(item.id)} disabled={running !== null} className="btn-secondary flex items-center gap-2 text-sm">
                        <Play className="h-4 w-4" />Execute approved canary
                      </button>
                    )}
                    {item.status === 'pending_owner_adjudication' && (
                      <>
                        <button type="button" onClick={() => adjudicateCanary(item.id, true)} disabled={running !== null} className="btn-secondary flex items-center gap-2 text-sm">
                          <CheckCircle2 className="h-4 w-4" />Confirm result
                        </button>
                        <button type="button" onClick={() => adjudicateCanary(item.id, false)} disabled={running !== null} className="btn-secondary flex items-center gap-2 text-sm text-red-200">
                          <XCircle className="h-4 w-4" />Mark failed
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))}
              {!loading && items.length === 0 && <p className="mt-3 text-xs text-slate-500">No validation evidence has been recorded.</p>}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function EvidenceMetric({ label, value, tone = 'text-slate-200' }: {
  label: string
  value: number
  tone?: string
}) {
  return <div><p className="text-slate-600">{label}</p><p className={`mt-1 font-medium ${tone}`}>{value}</p></div>
}
