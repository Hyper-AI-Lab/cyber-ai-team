'use client'

import { useEffect, useState } from 'react'
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
  const [companyStage, setCompanyStage] = useState('')
  const [companyProduct, setCompanyProduct] = useState('')
  const [companyCustomers, setCompanyCustomers] = useState('')
  const [companyChannels, setCompanyChannels] = useState('')
  const [companyGoals, setCompanyGoals] = useState('')
  const [companyJurisdictions, setCompanyJurisdictions] = useState('')
  const [builderResult, setBuilderResult] = useState<any | null>(null)
  const [building, setBuilding] = useState(false)
  const [roleGaps, setRoleGaps] = useState<any[]>([])
  const [loadingRoleGaps, setLoadingRoleGaps] = useState(false)
  const [reviewingRoleGaps, setReviewingRoleGaps] = useState(false)

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

  useEffect(() => {
    loadRoleGaps()
  }, [])

  const loadRoleGaps = async () => {
    setLoadingRoleGaps(true)
    try {
      const gaps = await api.listRoleGaps()
      setRoleGaps(gaps)
    } catch {
      setRoleGaps([])
    } finally {
      setLoadingRoleGaps(false)
    }
  }

  const handleSupervisorReview = async () => {
    setReviewingRoleGaps(true)
    try {
      const review = await api.runSupervisorRoleGapReview()
      await loadRoleGaps()
      alert(
        `Supervisor review complete: ${review.role_gaps_reviewed} gaps reviewed, ${review.role_gaps_proposed.length} proposals generated, ${review.workflow_failure_gaps.length} workflow failure gaps surfaced.`
      )
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setReviewingRoleGaps(false)
    }
  }

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
        stage: companyStage,
        product: companyProduct,
        target_customers: companyCustomers,
        channels: companyChannels,
        goals: companyGoals,
        jurisdictions: companyJurisdictions,
      })
      setBuilderResult(res)
      loadRoleGaps()
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

  const handleRoleGapAction = async (gapId: string, action: 'propose' | 'apply' | 'dismiss') => {
    try {
      if (action === 'propose') {
        await api.proposeRoleGap(gapId, { name: companyName })
      } else if (action === 'apply') {
        const res = await api.applyRoleGap(gapId, { name: companyName })
        if (res.approval_required) {
          alert(
            `Approval required before creating this role. Review approval ${res.approval_id} in Approvals, then click Create Role again.`
          )
        } else {
          onRefresh()
        }
      } else {
        await api.resolveRoleGap(gapId, 'dismissed', 'Dismissed from owner console')
      }
      await loadRoleGaps()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
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
            Give the builder enough company context to choose roles, surface gaps, seed memory, and start the adaptive operating loops.
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
            <div>
              <label className="block text-sm text-slate-400 mb-1">Stage</label>
              <input
                type="text"
                value={companyStage}
                onChange={(e) => setCompanyStage(e.target.value)}
                className={inputClassName}
                placeholder="Idea, MVP, revenue, growth..."
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Product / Offer</label>
              <input
                type="text"
                value={companyProduct}
                onChange={(e) => setCompanyProduct(e.target.value)}
                className={inputClassName}
                placeholder="AI operations platform, services, marketplace..."
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Customers</label>
              <input
                type="text"
                value={companyCustomers}
                onChange={(e) => setCompanyCustomers(e.target.value)}
                className={inputClassName}
                placeholder="B2B clients, founders, agencies..."
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Channels</label>
              <input
                type="text"
                value={companyChannels}
                onChange={(e) => setCompanyChannels(e.target.value)}
                className={inputClassName}
                placeholder="Email, phone, SMS, web, CRM..."
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Goals</label>
              <input
                type="text"
                value={companyGoals}
                onChange={(e) => setCompanyGoals(e.target.value)}
                className={inputClassName}
                placeholder="Launch, acquire clients, automate ops..."
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Jurisdictions</label>
              <input
                type="text"
                value={companyJurisdictions}
                onChange={(e) => setCompanyJurisdictions(e.target.value)}
                className={inputClassName}
                placeholder="US, EU, Germany..."
              />
            </div>
          </div>
          <button onClick={handleCompanyBuilder} disabled={building} className="btn-primary">
            {building ? 'Building operating model...' : 'Build Adaptive Company Team'}
          </button>

          {builderResult && (
            <div className="mt-6 rounded-lg border border-slate-700 bg-slate-900/60 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-white">Adaptive Team Provisioned</p>
                  <p className="text-xs text-slate-400">
                    {builderResult.instantiated_agents?.length ?? 0} agents booted,
                    {' '}
                    {builderResult.role_backlog?.length ?? 0} roles deferred
                  </p>
                </div>
                <span className="badge badge-success">Activated</span>
              </div>
              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="rounded border border-slate-700 bg-slate-800 px-3 py-2">
                  <p className="text-xs uppercase tracking-wide text-slate-500">Dynamic Roles</p>
                  <p className="mt-1 text-lg font-semibold text-white">
                    {builderResult.operating_model?.summary?.dynamic_role_count ?? 0}
                  </p>
                </div>
                <div className="rounded border border-slate-700 bg-slate-800 px-3 py-2">
                  <p className="text-xs uppercase tracking-wide text-slate-500">Gaps</p>
                  <p className="mt-1 text-lg font-semibold text-white">
                    {builderResult.capability_gaps?.length ?? 0}
                  </p>
                </div>
                <div className="rounded border border-slate-700 bg-slate-800 px-3 py-2">
                  <p className="text-xs uppercase tracking-wide text-slate-500">Loops</p>
                  <p className="mt-1 text-lg font-semibold text-white">
                    {builderResult.adaptive_loops?.length ?? 0}
                  </p>
                </div>
              </div>
              <div className="mt-3 grid gap-2">
                {builderResult.instantiated_agents?.map((agent: any) => (
                  <div
                    key={agent.agent_id}
                    className="rounded border border-slate-700 bg-slate-800 px-3 py-2 flex items-center justify-between"
                  >
                    <div>
                      <span className="text-sm text-white font-medium">{agent.role_name}</span>
                      <p className="text-xs text-slate-500">Family: {agent.role_family}</p>
                    </div>
                    <span className="badge badge-success">{agent.status}</span>
                  </div>
                ))}
              </div>
              {builderResult.capability_gaps?.length > 0 && (
                <div className="mt-4 rounded border border-amber-900/70 bg-amber-950/30 p-3">
                  <p className="text-sm font-medium text-amber-200">Capability Gaps</p>
                  <div className="mt-2 space-y-2">
                    {builderResult.capability_gaps.map((gap: any, index: number) => (
                      <div key={`${gap.type}-${index}`} className="text-xs text-amber-100/80">
                        <span className="font-semibold">{gap.type}</span>
                        {gap.role_name ? ` for ${gap.role_name}` : ''}
                        {gap.integration ? `: ${gap.integration}` : ''}
                      </div>
                    ))}
                  </div>
                </div>
              )}
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

      <div className="card">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-white">Role Gap Inbox</h3>
            <p className="text-sm text-slate-400 mt-1">
              Missing roles, tools, or skills reported by agents and operating loops
            </p>
          </div>
          <button onClick={loadRoleGaps} className="btn-secondary text-sm">
            {loadingRoleGaps ? 'Refreshing...' : 'Refresh'}
          </button>
          <button
            onClick={handleSupervisorReview}
            disabled={reviewingRoleGaps}
            className="btn-primary text-sm"
          >
            {reviewingRoleGaps ? 'Reviewing...' : 'Supervisor Review'}
          </button>
        </div>
        <div className="mt-4 space-y-3">
          {roleGaps.map((gap: any) => (
            <div key={gap.id} className="rounded-lg border border-slate-700 bg-slate-900/50 p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="font-medium text-white">{gap.title}</h4>
                    <span className="badge badge-info">{gap.status}</span>
                    <span className="badge-warning">{gap.severity}</span>
                  </div>
                  <p className="mt-2 text-sm text-slate-400">{gap.description}</p>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                    {gap.capability && <span>Capability: {gap.capability}</span>}
                    {gap.source_agent_id && <span>Reporter: {gap.source_agent_id}</span>}
                    {gap.requested_tools?.length > 0 && (
                      <span>Tools: {gap.requested_tools.join(', ')}</span>
                    )}
                  </div>
                  {gap.proposed_role?.manifest_payload && (
                    <div className="mt-3 rounded border border-slate-800 bg-slate-950/70 p-3 text-xs text-slate-300">
                      <span className="font-semibold text-slate-100">Proposal:</span>
                      {' '}
                      {gap.proposed_role.manifest_payload.name}
                      {' '}
                      ({gap.proposed_role.manifest_payload.family})
                    </div>
                  )}
                  {gap.resolution?.approval_required && (
                    <div className="mt-3 rounded border border-amber-900/70 bg-amber-950/30 p-3 text-xs text-amber-100">
                      <span className="font-semibold">Approval required:</span>
                      {' '}
                      {gap.resolution.high_risk_tools?.join(', ') || 'high-risk tool grant'}
                      {gap.resolution.pending_approval_id && (
                        <span className="ml-1 text-amber-200/80">
                          ({gap.resolution.pending_approval_id})
                        </span>
                      )}
                    </div>
                  )}
                  {gap.context?.supervisor_review && (
                    <div className="mt-3 rounded border border-blue-900/70 bg-blue-950/30 p-3 text-xs text-blue-100">
                      <span className="font-semibold">Supervisor:</span>
                      {' '}
                      {gap.context.supervisor_review.recommendation}
                      {' '}
                      <span className="text-blue-200/80">
                        ({gap.context.supervisor_review.priority})
                      </span>
                      <p className="mt-1 text-blue-100/80">
                        {gap.context.supervisor_review.reason}
                      </p>
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap justify-end gap-2">
                  {gap.status === 'open' && (
                    <button
                      onClick={() => handleRoleGapAction(gap.id, 'propose')}
                      className="btn-secondary text-sm"
                    >
                      Propose Role
                    </button>
                  )}
                  {gap.status === 'proposed' && (
                    <button
                      onClick={() => handleRoleGapAction(gap.id, 'apply')}
                      className="btn-primary text-sm"
                    >
                      {gap.resolution?.approval_required ? 'Create After Approval' : 'Create Role'}
                    </button>
                  )}
                  {['open', 'proposed'].includes(gap.status) && (
                    <button
                      onClick={() => handleRoleGapAction(gap.id, 'dismiss')}
                      className="btn-danger text-sm"
                    >
                      Dismiss
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
          {!loadingRoleGaps && roleGaps.length === 0 && (
            <div className="rounded-lg border border-dashed border-slate-700 p-6 text-center text-sm text-slate-500">
              No role gaps reported yet.
            </div>
          )}
        </div>
      </div>

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
