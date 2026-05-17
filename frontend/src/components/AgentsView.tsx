'use client'

import { useState } from 'react'
import { api } from '@/lib/api'
import { Bot, Play, Plus } from 'lucide-react'

interface AgentsViewProps {
  agents: any[]
  onRefresh: () => void
}

export default function AgentsView({ agents, onRefresh }: AgentsViewProps) {
  const [invokeAgentId, setInvokeAgentId] = useState<string | null>(null)
  const [task, setTask] = useState('')
  const [result, setResult] = useState<string | null>(null)
  const [invoking, setInvoking] = useState(false)
  const [showBuilder, setShowBuilder] = useState(false)
  const [companyName, setCompanyName] = useState('')
  const [companyIndustry, setCompanyIndustry] = useState('')
  const [builderResult, setBuilderResult] = useState<any | null>(null)
  const inputClassName = [
    'w-full rounded-lg border border-slate-600 bg-slate-700',
    'px-3 py-2 text-white',
  ].join(' ')
  const textareaClassName = [
    'h-32 w-full resize-none rounded-lg border border-slate-600',
    'bg-slate-700 px-3 py-2 text-white',
  ].join(' ')
  const resultClassName = [
    'mt-4 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg',
    'bg-slate-900 p-4 font-mono text-sm',
  ].join(' ')

  const handleInvoke = async () => {
    if (!invokeAgentId || !task) return
    setInvoking(true)
    try {
      const res = await api.invokeAgent(invokeAgentId, task)
      setResult(res.result || JSON.stringify(res))
    } catch (e: any) {
      setResult(`Error: ${e.message}`)
    } finally {
      setInvoking(false)
    }
  }

  const handleCompanyBuilder = async () => {
    if (!companyName || !companyIndustry) return
    try {
      const res = await api.runCompanyBuilder({
        name: companyName,
        industry: companyIndustry,
      })
      setBuilderResult(res)
      onRefresh()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Agents</h2>
          <p className="text-slate-400 mt-1">Manage your AI team members</p>
        </div>
        <button
          onClick={() => setShowBuilder(!showBuilder)}
          className="btn-primary flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          Company Builder
        </button>
      </div>

      {/* Company Builder Panel */}
      {showBuilder && (
        <div className="card">
          <h3 className="text-lg font-semibold mb-4">Company Builder</h3>
          <p className="text-sm text-slate-400 mb-4">
            Describe your company and the builder will recommend the optimal AI team structure.
          </p>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm text-slate-400 mb-1">Company Name</label>
              <input
                type="text"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                className={inputClassName}
                placeholder="Acme Corp"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Industry</label>
              <input
                type="text"
                value={companyIndustry}
                onChange={(e) => setCompanyIndustry(e.target.value)}
                className={inputClassName}
                placeholder="SaaS, Fintech, E-commerce..."
              />
            </div>
          </div>
          <button onClick={handleCompanyBuilder} className="btn-primary">
            Generate Team Blueprint
          </button>
          {builderResult && (
            <div className="mt-4 rounded-lg border border-slate-700 bg-slate-900/60 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-white">Provisioning complete</p>
                  <p className="text-xs text-slate-400">
                    {builderResult.instantiated_agents?.length ?? 0} agents in blueprint
                  </p>
                </div>
                <span className="badge badge-success">Activated</span>
              </div>
              <div className="mt-3 grid gap-2">
                {builderResult.instantiated_agents?.map((agent: any) => (
                  <div
                    key={agent.agent_id}
                    className="rounded border border-slate-700 bg-slate-800 px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm text-white">{agent.role_name}</span>
                      <span className="text-xs text-slate-400">{agent.status}</span>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      Tools: {agent.tools?.length ?? 0}
                      {agent.unsupported_tools?.length
                        ? ` • Unsupported: ${agent.unsupported_tools.length}`
                        : ''}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Agent Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {agents.map((agent: any) => (
          <div key={agent.id} className="card hover:border-blue-600 transition-colors">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 bg-blue-900/50 rounded-lg flex items-center justify-center">
                <Bot className="w-5 h-5 text-blue-400" />
              </div>
              <div>
                <h4 className="font-medium">{agent.role_name}</h4>
                <p className="text-xs text-slate-500">{agent.role_family}</p>
              </div>
              <span
                className={`ml-auto badge ${
                  agent.status === 'active' ? 'badge-success' : 'badge-danger'
                }`}
              >
                {agent.status}
              </span>
            </div>
            <p className="text-sm text-slate-400 line-clamp-2 mb-3">
              {agent.instructions?.slice(0, 150)}...
            </p>
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span>Tools: {agent.tools?.length ?? 0}</span>
              <span>•</span>
              <span>Approval: {agent.approval_policy}</span>
            </div>
            <button
              onClick={() => setInvokeAgentId(agent.id)}
              className="mt-3 w-full btn-secondary text-sm flex items-center justify-center gap-2"
            >
              <Play className="w-4 h-4" />
              Invoke
            </button>
          </div>
        ))}
        {agents.length === 0 && (
          <div className="card col-span-full text-center py-12 text-slate-500">
            <Bot className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>No agents yet. Use the Company Builder to create your team.</p>
          </div>
        )}
      </div>

      {/* Invoke Modal */}
      {invokeAgentId && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="card w-full max-w-lg">
            <h3 className="text-lg font-semibold mb-4">
              Invoke: {agents.find((a: any) => a.id === invokeAgentId)?.role_name}
            </h3>
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              className={textareaClassName}
              placeholder="Describe the task for this agent..."
            />
            <div className="flex gap-3 mt-4">
              <button onClick={handleInvoke} disabled={invoking} className="btn-primary">
                {invoking ? 'Running...' : 'Execute'}
              </button>
              <button
                onClick={() => {
                  setInvokeAgentId(null)
                  setTask('')
                  setResult(null)
                }}
                className="btn-secondary"
              >
                Cancel
              </button>
            </div>
            {result && (
              <div className={resultClassName}>
                {result}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
