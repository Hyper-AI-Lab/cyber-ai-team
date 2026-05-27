'use client'

import { useState } from 'react'
import { api } from '@/lib/api'
import { Bot, Play, Plus, Cpu, Shield, HelpCircle, Check, X } from 'lucide-react'

interface AgentsViewProps {
  agents: any[]
  onRefresh: () => void
}

export default function AgentsView({ agents, onRefresh }: AgentsViewProps) {
  const [invokeAgentId, setInvokeAgentId] = useState<string | null>(null)
  const [task, setTask] = useState('')
  const [result, setResult] = useState<string | null>(null)
  const [invoking, setInvoking] = useState(false)

  // Creation States
  const [creationMode, setCreationMode] = useState<'builder' | 'custom' | null>(null)

  // 1. Company Builder State
  const [companyName, setCompanyName] = useState('')
  const [companyIndustry, setCompanyIndustry] = useState('')
  const [builderResult, setBuilderResult] = useState<any | null>(null)
  const [building, setBuilding] = useState(false)

  // 2. Custom Role Provisioning State
  const [roleFamily, setRoleFamily] = useState('engineering')
  const [roleName, setRoleName] = useState('')
  const [roleDescription, setRoleDescription] = useState('')
  const [roleInstructions, setRoleInstructions] = useState('')
  const [selectedTools, setSelectedTools] = useState<string[]>(['web_search'])
  const [approvalPolicy, setApprovalPolicy] = useState('auto')
  const [provisioning, setProvisioning] = useState(false)

  const inputClassName = 'w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white placeholder-slate-400 focus:outline-none focus:border-blue-500'
  const textareaClassName = 'h-32 w-full resize-none rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white placeholder-slate-400 focus:outline-none focus:border-blue-500'
  const selectClassName = 'w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white focus:outline-none focus:border-blue-500'

  const toggleTool = (tool: string) => {
    if (selectedTools.includes(tool)) {
      setSelectedTools(selectedTools.filter(t => t !== tool))
    } else {
      setSelectedTools([...selectedTools, tool])
    }
  }

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
    setBuilding(true)
    try {
      const res = await api.runCompanyBuilder({
        name: companyName,
        industry: companyIndustry,
      })
      setBuilderResult(res)
      onRefresh()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setBuilding(false)
    }
  }

  const handleProvisionRole = async () => {
    if (!roleName || !roleDescription || !roleInstructions) {
      alert('Please fill out all required fields.')
      return
    }
    setProvisioning(true)
    try {
      await api.provisionRole({
        family: roleFamily,
        name: roleName,
        description: roleDescription,
        instructions_template: roleInstructions,
        default_tools: selectedTools,
        approval_policy: approvalPolicy,
        memory_namespace: `${roleFamily}:${roleName.toLowerCase().replace(/\s+/g, '_')}`,
        success_metrics: [],
        is_core: true,
        config: {}
      })
      alert(`Successfully provisioned role: ${roleName}!`)
      setCreationMode(null)
      // Reset form fields
      setRoleName('')
      setRoleDescription('')
      setRoleInstructions('')
      setSelectedTools(['web_search'])
      setApprovalPolicy('auto')
      onRefresh()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setProvisioning(false)
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Agents</h2>
          <p className="text-slate-400 mt-1">Manage and provision your dynamic AI team members</p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => setCreationMode(creationMode === 'builder' ? null : 'builder')}
            className={`btn-secondary flex items-center gap-2 ${creationMode === 'builder' ? 'ring-2 ring-blue-500' : ''}`}
          >
            <Cpu className="w-4 h-4" />
            Company Builder
          </button>
          <button
            onClick={() => setCreationMode(creationMode === 'custom' ? null : 'custom')}
            className={`btn-primary flex items-center gap-2 ${creationMode === 'custom' ? 'ring-2 ring-white' : ''}`}
          >
            <Plus className="w-4 h-4" />
            Provision Custom Role
          </button>
        </div>
      </div>

      {/* 1. Company Builder Panel */}
      {creationMode === 'builder' && (
        <div className="card border-blue-500 bg-slate-800/80 backdrop-blur-sm transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              <Cpu className="w-5 h-5 text-blue-400" />
              AI Company Builder Blueprint
            </h3>
            <button onClick={() => setCreationMode(null)} className="text-slate-400 hover:text-white">
              <X className="w-5 h-5" />
            </button>
          </div>
          <p className="text-sm text-slate-400 mb-6">
            Describe your startup, name, and industry. Our system will generate and instantiate a tailored multi-agent squad to bootstrap your corporate execution.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
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
              <label className="block text-sm text-slate-400 mb-1">Industry Sector</label>
              <input
                type="text"
                value={companyIndustry}
                onChange={(e) => setCompanyIndustry(e.target.value)}
                className={inputClassName}
                placeholder="SaaS, E-commerce, Fintech..."
              />
            </div>
          </div>
          <button onClick={handleCompanyBuilder} disabled={building} className="btn-primary">
            {building ? 'Synthesizing blueprint...' : 'Generate and Instantiate Squad'}
          </button>

          {builderResult && (
            <div className="mt-6 rounded-lg border border-slate-700 bg-slate-900/60 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-white">Squad Provisioned & Instantiated</p>
                  <p className="text-xs text-slate-400">
                    {builderResult.instantiated_agents?.length ?? 0} agents booted in blueprint
                  </p>
                </div>
                <span className="badge badge-success">Activated</span>
              </div>
              <div className="mt-3 grid gap-2">
                {builderResult.instantiated_agents?.map((agent: any) => (
                  <div
                    key={agent.agent_id}
                    className="rounded border border-slate-700 bg-slate-800 px-3 py-2 flex items-center justify-between"
                  >
                    <div>
                      <span className="text-sm text-white font-medium">{agent.role_name}</span>
                      <p className="text-xs text-slate-500">Family: {agent.family}</p>
                    </div>
                    <span className="badge badge-success">Active</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 2. Custom Role Provisioner Panel */}
      {creationMode === 'custom' && (
        <div className="card border-emerald-500 bg-slate-800/80 backdrop-blur-sm transition-all duration-300">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-white flex items-center gap-2">
              <Bot className="w-5 h-5 text-emerald-400" />
              Provision Custom Corporate Role
            </h3>
            <button onClick={() => setCreationMode(null)} className="text-slate-400 hover:text-white">
              <X className="w-5 h-5" />
            </button>
          </div>
          <p className="text-sm text-slate-400 mb-6">
            Instantly provision a brand new AI agent role manifest. The manifest will be registered in PostgreSQL and the agent instantiated immediately.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm text-slate-400 mb-1">Role Family</label>
              <select
                value={roleFamily}
                onChange={(e) => setRoleFamily(e.target.value)}
                className={selectClassName}
              >
                <option value="engineering">Engineering & Product</option>
                <option value="legal">Legal & Compliance</option>
                <option value="finance">Finance & Treasury</option>
                <option value="marketing">Marketing & Content</option>
                <option value="sales">Sales & Outreach</option>
                <option value="operations">Operations & HR</option>
              </select>
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Role Name</label>
              <input
                type="text"
                value={roleName}
                onChange={(e) => setRoleName(e.target.value)}
                className={inputClassName}
                placeholder="e.g. Legal Officer, Backend Engineer"
              />
            </div>
          </div>

          <div className="mb-4">
            <label className="block text-sm text-slate-400 mb-1">Description</label>
            <input
              type="text"
              value={roleDescription}
              onChange={(e) => setRoleDescription(e.target.value)}
              className={inputClassName}
              placeholder="High-level description of what this agent does..."
            />
          </div>

          <div className="mb-4">
            <label className="block text-sm text-slate-400 mb-1">System Instructions Template</label>
            <textarea
              value={roleInstructions}
              onChange={(e) => setRoleInstructions(e.target.value)}
              className={textareaClassName}
              placeholder="Provide exact directives, prompt templates, and execution scope..."
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            <div>
              <label className="block text-sm text-slate-400 mb-2">Default Enabled Tools</label>
              <div className="space-y-2">
                {[
                  { id: 'contract_draft', name: 'Contract Drafting (Legal)' },
                  { id: 'policy_draft', name: 'Corporate Policies (Compliance)' },
                  { id: 'web_search', name: 'Web Search Engine (SearXNG)' },
                ].map(tool => (
                  <label key={tool.id} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedTools.includes(tool.id)}
                      onChange={() => toggleTool(tool.id)}
                      className="rounded bg-slate-700 border-slate-600 text-blue-500 focus:ring-blue-500"
                    />
                    {tool.name}
                  </label>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-sm text-slate-400 mb-2 flex items-center gap-1.5">
                <Shield className="w-4 h-4 text-yellow-400" />
                Human-in-the-loop Approval Policy
              </label>
              <div className="space-y-2">
                {[
                  { id: 'auto', name: 'Automatic (No manual review)' },
                  { id: 'sensitive', name: 'Sensitive Only (High risk tools)' },
                  { id: 'always', name: 'Always (Full manual gate)' },
                ].map(policy => (
                  <label key={policy.id} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                    <input
                      type="radio"
                      name="approvalPolicy"
                      checked={approvalPolicy === policy.id}
                      onChange={() => setApprovalPolicy(policy.id)}
                      className="bg-slate-700 border-slate-600 text-blue-500 focus:ring-blue-500"
                    />
                    {policy.name}
                  </label>
                ))}
              </div>
            </div>
          </div>

          <button onClick={handleProvisionRole} disabled={provisioning} className="btn-primary bg-emerald-600 hover:bg-emerald-700">
            {provisioning ? 'Provisioning manifest...' : 'Provision and Boot Agent'}
          </button>
        </div>
      )}

      {/* Agent Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {agents.map((agent: any) => (
          <div key={agent.id} className="card hover:border-blue-600 transition-colors flex flex-col justify-between h-full">
            <div>
              <div className="flex items-center gap-3 mb-3">
                <div className="w-10 h-10 bg-blue-900/50 rounded-lg flex items-center justify-center">
                  <Bot className="w-5 h-5 text-blue-400" />
                </div>
                <div>
                  <h4 className="font-medium text-white">{agent.role_name}</h4>
                  <p className="text-xs text-slate-500 uppercase tracking-wider">{agent.role_family}</p>
                </div>
                <span
                  className={`ml-auto badge ${
                    agent.status === 'active' ? 'badge-success' : 'badge-danger'
                  }`}
                >
                  {agent.status}
                </span>
              </div>
              <p className="text-sm text-slate-400 line-clamp-3 mb-4">
                {agent.instructions}
              </p>
            </div>

            <div className="mt-auto">
              <div className="border-t border-slate-700/60 pt-3 flex items-center justify-between text-xs text-slate-500">
                <span className="flex items-center gap-1">
                  Tools: <span className="text-slate-300 font-semibold">{agent.tools?.length ?? 0}</span>
                </span>
                <span className="flex items-center gap-1">
                  Approval: <span className="text-slate-300 font-semibold uppercase">{agent.approval_policy}</span>
                </span>
              </div>

              <button
                onClick={() => setInvokeAgentId(agent.id)}
                className="mt-3 w-full btn-secondary text-sm flex items-center justify-center gap-2"
              >
                <Play className="w-4 h-4" />
                Invoke Agent
              </button>
            </div>
          </div>
        ))}
        {agents.length === 0 && (
          <div className="card col-span-full text-center py-12 text-slate-500">
            <Bot className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>No agents provisioned yet.</p>
            <p className="text-sm mt-1 text-slate-600">
              Use &quot;Company Builder&quot; or &quot;Provision Custom Role&quot; to create agent roles.
            </p>
          </div>
        )}
      </div>

      {/* Invoke Modal */}
      {invokeAgentId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="card w-full max-w-lg border-blue-500">
            <h3 className="text-lg font-semibold text-white mb-4">
              Invoke Agent: {agents.find((a: any) => a.id === invokeAgentId)?.role_name}
            </h3>
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              className={textareaClassName}
              placeholder="Describe the task for this agent..."
            />
            <div className="flex gap-3 mt-4">
              <button onClick={handleInvoke} disabled={invoking} className="btn-primary">
                {invoking ? 'Running invocation...' : 'Execute Task'}
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
              <div className="mt-4 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-950 border border-slate-800 p-4 font-mono text-sm text-slate-300">
                {result}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
