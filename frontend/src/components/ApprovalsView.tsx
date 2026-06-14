'use client'

import { useState } from 'react'
import { api } from '@/lib/api'
import { ShieldCheck, CheckCircle, XCircle, Clock } from 'lucide-react'

interface ApprovalsViewProps {
  approvals: any[]
  onRefresh: () => void
}

export default function ApprovalsView({ approvals, onRefresh }: ApprovalsViewProps) {
  const [runningApprovalId, setRunningApprovalId] = useState<string | null>(null)

  const isExpired = (approval: any) => {
    return Boolean(
      approval.expires_at &&
        approval.status === 'pending' &&
        new Date(approval.expires_at).getTime() <= Date.now()
    )
  }

  const toolReplay = (approval: any) => {
    const payload = approval.action_payload || {}
    const replayBody = payload.replay_instructions?.body || {}
    const toolName = payload.tool_name || replayBody.tool_name
    const params = payload.params || replayBody.params
    if (!toolName || !params) {
      return null
    }
    return { toolName, params }
  }

  const handleAction = async (id: string, action: 'approve' | 'reject') => {
    const approval = approvals.find((item) => item.id === id)
    if (approval && isExpired(approval)) {
      alert('This approval has expired. Regenerate the request from its source workflow or plan.')
      onRefresh()
      return
    }
    try {
      setRunningApprovalId(id)
      if (action === 'approve') {
        await api.approveAction(id, 'Approved via console')
        const replay = approval ? toolReplay(approval) : null
        if (replay) {
          const result = await api.executeTool(replay.toolName, {
            ...replay.params,
            _approval_id: id,
          })
          if (result?.success === false) {
            throw new Error(result.error || 'Approved tool execution failed')
          }
        }
      } else {
        await api.rejectAction(id, 'Rejected via console')
      }
      onRefresh()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setRunningApprovalId(null)
    }
  }

  const riskClass: Record<string, string> = {
    low: 'bg-blue-600/20 text-blue-300',
    medium: 'bg-amber-600/20 text-amber-300',
    high: 'bg-red-600/20 text-red-300',
    critical: 'bg-red-700/40 text-red-100',
  }

  const approvalDetails = (approval: any) => {
    const payload = approval.action_payload || {}
    const policy = payload.policy || {}
    const readiness = policy.tool_readiness || {}
    const rows = [
      ['Requester', approval.requester],
      ['Target', [approval.target_type, approval.target_id].filter(Boolean).join(':')],
      ['Plan', payload.plan_id],
      ['Task', payload.review_title || payload.review_for],
      ['Source', [payload.source_type, payload.source_id].filter(Boolean).join(':')],
      ['Reason', (payload.review_reasons || policy.review_reasons || []).join(', ')],
      ['Tools', (readiness.requested_tools || payload.default_tools || []).join(', ')],
      [
        'Approval-gated tools',
        (readiness.approval_gated_tools || payload.high_risk_tools || []).join(', '),
      ],
    ].filter(([, value]) => value)

    return rows.slice(0, 8)
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold">Approvals</h2>
        <p className="text-slate-400 mt-1">Review and approve agent actions</p>
      </div>

      <div className="space-y-4">
        {approvals.map((approval: any) => {
          const expired = isExpired(approval)
          return (
            <div key={approval.id} className="card">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <ShieldCheck className="w-5 h-5 text-yellow-400" />
                    <h4 className="font-medium">{approval.action_type}</h4>
                    <span className={expired ? 'badge-danger' : 'badge-warning'}>
                      {expired ? 'Expired' : 'Pending'}
                    </span>
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        riskClass[approval.risk_level] || 'bg-slate-700 text-slate-300'
                      }`}
                    >
                      {approval.risk_level || 'medium'} risk
                    </span>
                  </div>
                  <p className="text-sm text-slate-300 mb-2">{approval.action_description}</p>
                  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                    <span>Agent: {approval.agent_id || 'system'}</span>
                    <span>Requester: {approval.requester || 'system'}</span>
                    <span>{new Date(approval.created_at).toLocaleString()}</span>
                    {approval.expires_at && (
                      <span>
                        {expired ? 'Expired' : 'Expires'}:{' '}
                        {new Date(approval.expires_at).toLocaleString()}
                      </span>
                    )}
                  </div>
                  {expired && (
                    <div className="mt-3 rounded-lg border border-red-700/50 bg-red-950/30 px-3 py-2 text-sm text-red-200">
                      This request is no longer actionable. Regenerate it from the related workflow,
                      autonomous plan, memory finding, or role gap.
                    </div>
                  )}
                  {approvalDetails(approval).length > 0 && (
                    <div className="mt-4 grid gap-2 text-xs text-slate-300 md:grid-cols-2">
                      {approvalDetails(approval).map(([label, value]) => (
                        <div
                          key={`${approval.id}-${label}`}
                          className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2"
                        >
                          <div className="text-slate-500">{label}</div>
                          <div className="mt-1 break-words">{value}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  {approval.action_payload && (
                    <details className="mt-3 text-xs text-slate-400">
                      <summary className="cursor-pointer text-slate-300">Payload</summary>
                      <pre className="mt-2 max-h-56 overflow-auto rounded-lg border border-slate-700 bg-slate-950 p-3">
                        {JSON.stringify(approval.action_payload, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleAction(approval.id, 'approve')}
                    disabled={expired || runningApprovalId === approval.id}
                    className="btn-primary text-sm flex items-center gap-1"
                  >
                    {runningApprovalId === approval.id ? (
                      <Clock className="w-4 h-4 animate-pulse" />
                    ) : (
                      <CheckCircle className="w-4 h-4" />
                    )}
                    Approve
                  </button>
                  <button
                    onClick={() => handleAction(approval.id, 'reject')}
                    disabled={expired || runningApprovalId === approval.id}
                    className="btn-danger text-sm flex items-center gap-1"
                  >
                    <XCircle className="w-4 h-4" />
                    Reject
                  </button>
                </div>
              </div>
            </div>
          )
        })}
        {approvals.length === 0 && (
          <div className="card text-center py-12 text-slate-500">
            <ShieldCheck className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>No pending approvals</p>
            <p className="text-sm mt-2">All agent actions have been reviewed.</p>
          </div>
        )}
      </div>
    </div>
  )
}
