'use client'

import { api } from '@/lib/api'
import { ShieldCheck, CheckCircle, XCircle, Clock } from 'lucide-react'

interface ApprovalsViewProps {
  approvals: any[]
  onRefresh: () => void
}

export default function ApprovalsView({ approvals, onRefresh }: ApprovalsViewProps) {
  const handleAction = async (id: string, action: 'approve' | 'reject') => {
    try {
      if (action === 'approve') {
        await api.approveAction(id, 'Approved via console')
      } else {
        await api.rejectAction(id, 'Rejected via console')
      }
      onRefresh()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold">Approvals</h2>
        <p className="text-slate-400 mt-1">Review and approve agent actions</p>
      </div>

      <div className="space-y-4">
        {approvals.map((approval: any) => (
          <div key={approval.id} className="card">
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-2">
                  <ShieldCheck className="w-5 h-5 text-yellow-400" />
                  <h4 className="font-medium">{approval.action_type}</h4>
                  <span className="badge-warning">Pending</span>
                </div>
                <p className="text-sm text-slate-300 mb-2">{approval.action_description}</p>
                <div className="flex items-center gap-4 text-xs text-slate-500">
                  <span>Agent: {approval.agent_id}</span>
                  <span>•</span>
                  <span>{new Date(approval.created_at).toLocaleString()}</span>
                </div>
              </div>
              <div className="flex gap-2 ml-4">
                <button
                  onClick={() => handleAction(approval.id, 'approve')}
                  className="btn-primary text-sm flex items-center gap-1"
                >
                  <CheckCircle className="w-4 h-4" />
                  Approve
                </button>
                <button
                  onClick={() => handleAction(approval.id, 'reject')}
                  className="btn-danger text-sm flex items-center gap-1"
                >
                  <XCircle className="w-4 h-4" />
                  Reject
                </button>
              </div>
            </div>
          </div>
        ))}
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
