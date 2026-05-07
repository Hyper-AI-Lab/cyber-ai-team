'use client'

import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { GitBranch, Play, Clock, CheckCircle, XCircle } from 'lucide-react'

export default function WorkflowsView() {
  const [workflows, setWorkflows] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      try {
        const res = await api.listWorkflows()
        setWorkflows(res)
      } catch (e) {
        console.error('Failed to load workflows:', e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  const statusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="w-4 h-4 text-green-400" />
      case 'failed':
        return <XCircle className="w-4 h-4 text-red-400" />
      case 'running':
        return <Clock className="w-4 h-4 text-yellow-400" />
      default:
        return <GitBranch className="w-4 h-4 text-slate-400" />
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold">Workflows</h2>
        <p className="text-slate-400 mt-1">Manage and monitor business process workflows</p>
      </div>

      <div className="grid grid-cols-1 gap-4">
        {workflows.map((wf: any) => (
          <div key={wf.id} className="card">
            <div className="flex items-center gap-3 mb-3">
              {statusIcon(wf.status)}
              <h4 className="font-medium">{wf.name}</h4>
              <span className={`badge ${wf.status === 'draft' ? 'badge-warning' : 'badge-info'}`}>
                {wf.status}
              </span>
              <span className="ml-auto text-xs text-slate-500">
                Trigger: {wf.trigger_type}
              </span>
            </div>
            {wf.description && (
              <p className="text-sm text-slate-400 mb-3">{wf.description}</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={async () => {
                  try {
                    await api.runWorkflow(wf.id)
                    alert('Workflow started!')
                  } catch (e: any) {
                    alert(`Error: ${e.message}`)
                  }
                }}
                className="btn-primary text-sm flex items-center gap-2"
              >
                <Play className="w-4 h-4" />
                Run
              </button>
            </div>
          </div>
        ))}
        {workflows.length === 0 && !loading && (
          <div className="card text-center py-12 text-slate-500">
            <GitBranch className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>No workflows defined yet.</p>
            <p className="text-sm mt-2">Create workflows through the API or Company Builder.</p>
          </div>
        )}
      </div>
    </div>
  )
}
