import { BrainCircuit, CheckCircle2, Route, ShieldAlert } from 'lucide-react'

interface Props {
  readiness?: any
  modelCapabilities?: any
  actionCandidates: any[]
  outcomes: any[]
  loading?: boolean
  onNavigate?: (view: 'approvals') => void
}

const tones: Record<string, string> = {
  ready: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  passed: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  executed: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  processing: 'border-blue-500/30 bg-blue-500/10 text-blue-200',
  active: 'border-blue-500/30 bg-blue-500/10 text-blue-200',
  approval_required: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  waiting_approval: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  retrying: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  blocked: 'border-red-500/30 bg-red-500/10 text-red-200',
  failed: 'border-red-500/30 bg-red-500/10 text-red-200',
  expired: 'border-red-500/30 bg-red-500/10 text-red-200',
  not_evaluated: 'border-slate-700 bg-slate-800 text-slate-300',
}

function badge(value?: string) {
  return tones[value || ''] || 'border-slate-700 bg-slate-800 text-slate-300'
}

function label(value?: string) {
  return (value || 'unknown').replaceAll('_', ' ')
}

export default function OutcomeAutonomyPanel({
  readiness,
  modelCapabilities,
  actionCandidates,
  outcomes,
  loading = false,
  onNavigate,
}: Props) {
  const signals = readiness?.company_signals || {}
  const extraction = readiness?.claim_extraction || {}
  const learning = readiness?.outcome_learning || {}
  const capabilitySummary = modelCapabilities?.summary || {}
  const capabilityItems = capabilitySummary.items || []
  const waitingApprovals = actionCandidates.filter(
    (item) => item.status === 'approval_required' || item.status === 'waiting_approval',
  )

  return (
    <div className="mt-6 border-t border-slate-800 pt-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Route className="h-4 w-4 text-cyan-300" />
            <h4 className="font-medium text-slate-200">Evidence-to-Outcome Integrity</h4>
          </div>
          <p className="mt-1 text-sm text-slate-400">
            Live signal dispositions, cognitive model contracts, governed actions, and measured outcomes.
          </p>
        </div>
        {waitingApprovals.length > 0 && onNavigate && (
          <button type="button" className="btn-secondary text-sm" onClick={() => onNavigate('approvals')}>
            Review {waitingApprovals.length} approval{waitingApprovals.length === 1 ? '' : 's'}
          </button>
        )}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <IntegrityCard
          icon={Route}
          title="Signals"
          status={signals.status || 'unavailable'}
          detail={`${signals.stale_pending || 0} stale · ${signals.undispositioned_processed || 0} undispositioned`}
        />
        <IntegrityCard
          icon={ShieldAlert}
          title="Evidence extraction"
          status={extraction.status || 'unavailable'}
          detail={`${extraction.expired_leases || 0} expired leases · ${extraction.scheduled_retries || 0} scheduled retries`}
        />
        <IntegrityCard
          icon={BrainCircuit}
          title="Model contracts"
          status={capabilitySummary.status || 'unavailable'}
          detail={`${capabilitySummary.qualified || 0}/${capabilitySummary.required || 0} task contracts qualified`}
        />
        <IntegrityCard
          icon={CheckCircle2}
          title="Outcome learning"
          status={learning.status || 'unavailable'}
          detail={`${learning.unassessed_work || 0} waiting · ${learning.stale_unassessed_work || 0} stale · ${outcomes.length} recent`}
        />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <div>
          <h5 className="text-sm font-medium text-slate-200">Cognitive task qualification</h5>
          <div className="mt-2 divide-y divide-slate-800 border-y border-slate-800">
            {capabilityItems.map((item: any) => (
              <div key={item.task_type} className="flex items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-200">{label(item.task_type)}</p>
                  <p className="mt-0.5 truncate text-xs text-slate-500">
                    {item.provider && item.model
                      ? `${item.provider} · ${item.model} · score ${item.score ?? '-'} / ${item.threshold ?? '-'}`
                      : 'No fresh capability evidence is recorded.'}
                  </p>
                </div>
                <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${badge(item.status)}`}>
                  {label(item.status)}
                </span>
              </div>
            ))}
            {!capabilityItems.length && (
              <Empty text={loading ? 'Loading model qualification evidence...' : 'No model capability evaluation has been recorded.'} />
            )}
          </div>
        </div>

        <div>
          <h5 className="text-sm font-medium text-slate-200">Governed action candidates</h5>
          <div className="mt-2 divide-y divide-slate-800 border-y border-slate-800">
            {actionCandidates.slice(0, 10).map((item) => (
              <div key={item.id} className="py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-200">{item.tool_name}</p>
                    <p className="mt-0.5 truncate text-xs text-slate-500">
                      {label(item.action_class)} · {item.agent_id} · confidence {Number(item.confidence || 0).toFixed(2)}
                    </p>
                  </div>
                  <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${badge(item.status)}`}>
                    {label(item.status)}
                  </span>
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  {(item.evidence_ids || []).length} evidence item(s) · Observer {item.observer_review_id ? 'reviewed' : 'pending'} · policy {item.policy_decision?.source || 'pending'}
                  {item.approval_id ? ` · approval ${item.approval_id}` : ''}
                </p>
                {item.error && <p className="mt-1 text-xs text-red-300">{item.error}</p>}
              </div>
            ))}
            {!actionCandidates.length && (
              <Empty text={loading ? 'Loading action candidates...' : 'No action is currently proposed. Domain agents have not found a justified action in the loaded evidence.'} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function IntegrityCard({ icon: Icon, title, status, detail }: {
  icon: any
  title: string
  status: string
  detail: string
}) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-900/50 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className="h-4 w-4 shrink-0 text-slate-400" />
          <span className="truncate text-sm font-medium text-slate-200">{title}</span>
        </div>
        <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${badge(status)}`}>
          {label(status)}
        </span>
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-500">{detail}</p>
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="py-8 text-center text-sm text-slate-500">{text}</div>
}
