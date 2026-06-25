'use client'

import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Bot, Play, Plus, Cpu, Shield, HelpCircle, Check, X, RefreshCw, Database, CalendarClock } from 'lucide-react'

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
  const [roleBacklog, setRoleBacklog] = useState<any | null>(null)
  const [loadingRoleGaps, setLoadingRoleGaps] = useState(false)
  const [reviewingRoleGaps, setReviewingRoleGaps] = useState(false)
  const [roleGapStatusFilter, setRoleGapStatusFilter] = useState('open,proposed')
  const [roleGapSourceFilter, setRoleGapSourceFilter] = useState('company_context_snapshot')
  const [selectedRoleGapIds, setSelectedRoleGapIds] = useState<string[]>([])
  const [batchProcessing, setBatchProcessing] = useState(false)
  const [companyContext, setCompanyContext] = useState<any | null>(null)
  const [syncingCompanyContext, setSyncingCompanyContext] = useState(false)
  const [companyContextError, setCompanyContextError] = useState<string | null>(null)
  const [operatingCadence, setOperatingCadence] = useState<any | null>(null)
  const [operatingCadenceError, setOperatingCadenceError] = useState<string | null>(null)
  const [teamActivation, setTeamActivation] = useState<any | null>(null)
  const [activatingTeam, setActivatingTeam] = useState(false)
  const [agentGrantCounts, setAgentGrantCounts] = useState<Record<string, any>>({})

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
  const roleBacklogItems = roleBacklog?.items || roleGaps

  const loadOperatingCadence = useCallback(async (companyNamespace?: string) => {
    setOperatingCadenceError(null)
    try {
      const cadence = await api.getRoleOperatingCadence(companyNamespace)
      setOperatingCadence(cadence)
    } catch (e: any) {
      setOperatingCadence(null)
      setOperatingCadenceError(e.message || 'Failed to load operating cadence')
    }
  }, [])

  const loadCompanyContext = useCallback(async () => {
    setCompanyContextError(null)
    try {
      const context = await api.getCompanyContext()
      setCompanyContext(context)
      await loadOperatingCadence(context?.snapshot?.company_namespace)
      const profile = context?.normalized_company_profile
      if (profile) {
        setCompanyName((current) => current || profile.name || profile.company_name || '')
        setCompanyIndustry((current) => current || profile.industry || '')
        setCompanyStage((current) => current || profile.stage || '')
        setCompanyProduct((current) => current || profile.product || '')
        setCompanyCustomers((current) => current || profile.target_customers || '')
        setCompanyChannels((current) => current || profile.channels || '')
        setCompanyGoals((current) => current || profile.goals || '')
        setCompanyJurisdictions((current) => current || profile.jurisdictions || '')
      }
    } catch (e: any) {
      setCompanyContext(null)
      setCompanyContextError(e.message || 'Failed to load company context')
      await loadOperatingCadence()
    }
  }, [loadOperatingCadence])

  useEffect(() => {
    loadCompanyContext()
  }, [loadCompanyContext])

  const loadRoleGaps = useCallback(async () => {
    setLoadingRoleGaps(true)
    try {
      const summary = await api.getRoleGapSummary({
        status: roleGapStatusFilter,
        source_type: roleGapSourceFilter || undefined,
        limit: 200,
      })
      setRoleBacklog(summary)
      setRoleGaps(summary.items || [])
      setSelectedRoleGapIds((current) => {
        const visibleIds = new Set((summary.items || []).map((item: any) => item.gap_id || item.id))
        return current.filter((id) => visibleIds.has(id))
      })
    } catch {
      try {
        const gaps = await api.listRoleGaps()
        setRoleBacklog(null)
        setRoleGaps(gaps)
        setSelectedRoleGapIds((current) => {
          const visibleIds = new Set((gaps || []).map((item: any) => item.gap_id || item.id))
          return current.filter((id) => visibleIds.has(id))
        })
      } catch {
        setRoleBacklog(null)
        setRoleGaps([])
        setSelectedRoleGapIds([])
      }
    } finally {
      setLoadingRoleGaps(false)
    }
  }, [roleGapSourceFilter, roleGapStatusFilter])

  useEffect(() => {
    loadRoleGaps()
  }, [loadRoleGaps])

  const loadTeamActivation = useCallback(async () => {
    try {
      const coverage = await api.getTeamActivationCoverage()
      setTeamActivation(coverage)
    } catch {
      setTeamActivation(null)
    }
  }, [])

  useEffect(() => {
    loadTeamActivation()
  }, [loadTeamActivation])

  useEffect(() => {
    let cancelled = false
    const loadGrantCounts = async () => {
      const entries = await Promise.all(
        agents.map(async (agent: any) => {
          try {
            const grants = await api.listAgentCapabilityGrants(agent.id)
            return [
              agent.id,
              {
                active: grants.filter((grant: any) => grant.state === 'active').length,
                pending: grants.filter((grant: any) => grant.state !== 'active').length,
              },
            ]
          } catch {
            return [agent.id, { active: 0, pending: 0 }]
          }
        }),
      )
      if (!cancelled) {
        setAgentGrantCounts(Object.fromEntries(entries))
      }
    }
    if (agents.length) {
      loadGrantCounts()
    } else {
      setAgentGrantCounts({})
    }
    return () => {
      cancelled = true
    }
  }, [agents])

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

  const handleSyncCompanyContext = async () => {
    setSyncingCompanyContext(true)
    setCompanyContextError(null)
    try {
      const result = await api.syncCompanyContext({
        dry_run: false,
        apply_low_risk: true,
        run_planner: true,
        source: 'erpnext',
      })
      setBuilderResult(result.snapshot ? {
        instantiated_agents: (result.snapshot.agent_ids || []).map((agentId: string) => ({
          agent_id: agentId,
          status: 'synced',
        })),
        role_backlog: result.apply_result?.skipped_role_specs || [],
        capability_gaps: result.snapshot.operating_model?.capability_gaps || [],
        operating_model: result.snapshot.operating_model,
      } : null)
      await loadCompanyContext()
      await loadRoleGaps()
      await loadOperatingCadence(result.snapshot?.company_namespace)
      await loadTeamActivation()
      onRefresh()
    } catch (e: any) {
      setCompanyContextError(e.message || 'ERPNext company-context sync failed')
    } finally {
      setSyncingCompanyContext(false)
    }
  }

  const handleTeamActivation = async () => {
    setActivatingTeam(true)
    try {
      const result = await api.runTeamActivation({
        dryRun: false,
        applySafeRoles: true,
        requestHighRiskGrants: true,
        sourceSnapshotId: companyContext?.snapshot?.id,
      })
      setTeamActivation({
        status: result.status === 'completed' ? 'active' : result.status,
        latest_run: result,
        active_agent_count: agents.length + (result.counts?.agents_created || 0),
        active_grant_count: result.counts?.safe_grants_active || 0,
        pending_or_blocked_grant_count:
          (result.counts?.grants_pending_approval || 0)
          + (result.counts?.grants_configuration_required || 0)
          + (result.counts?.grants_blocked || 0),
      })
      await loadRoleGaps()
      await loadOperatingCadence(companyContext?.snapshot?.company_namespace)
      await loadTeamActivation()
      onRefresh()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setActivatingTeam(false)
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

  const companyProfileForRoleGap = () => ({
    name: companyName || companyContext?.normalized_company_profile?.name || '',
    company_name: companyName || companyContext?.normalized_company_profile?.name || '',
    company_namespace: companyContext?.snapshot?.company_namespace,
  })

  const recommendedActionLabel = (action: string) => {
    const labels: Record<string, string> = {
      propose_role: 'Propose Role',
      create_role: 'Create Role',
      request_approval: 'Request Approval',
      await_approval: 'Awaiting Approval',
      regenerate_approval: 'Regenerate Approval',
      create_after_approval: 'Create After Approval',
      configure_tools: 'Setup Required',
      completed: 'Completed',
      deferred: 'Deferred',
      dismissed: 'Dismissed',
      stale: 'Stale Snapshot',
    }
    return labels[action] || action
  }

  const readinessBadgeClass = (item: any) => {
    if (item?.tool_readiness?.all_ready) return 'badge-success'
    if (item?.recommended_action === 'configure_tools') return 'badge-danger'
    return 'badge-warning'
  }

  const selectableRoleGapIds = roleBacklogItems
    .filter((item: any) => ['open', 'proposed'].includes(item.status))
    .map((item: any) => item.gap_id || item.id)

  const toggleRoleGapSelection = (gapId: string) => {
    setSelectedRoleGapIds((current) => (
      current.includes(gapId)
        ? current.filter((id) => id !== gapId)
        : [...current, gapId]
    ))
  }

  const toggleAllVisibleRoleGaps = () => {
    const allSelected = selectableRoleGapIds.every((id: string) => selectedRoleGapIds.includes(id))
    setSelectedRoleGapIds(allSelected ? [] : selectableRoleGapIds)
  }

  const handleBatchRoleGapAction = async (
    action: 'propose' | 'apply' | 'regenerate_approval' | 'defer' | 'dismiss'
  ) => {
    if (selectedRoleGapIds.length === 0) return
    setBatchProcessing(true)
    try {
      const approvalIds = roleBacklogItems.reduce((acc: Record<string, string>, item: any) => {
        const gapId = item.gap_id || item.id
        if (selectedRoleGapIds.includes(gapId) && item.approval?.state === 'approved') {
          acc[gapId] = item.approval.approval_id
        }
        return acc
      }, {})
      const result = await api.batchRoleGapAction({
        gap_ids: selectedRoleGapIds,
        action,
        company_profile: companyProfileForRoleGap(),
        approval_ids: approvalIds,
        note: `Batch ${action} from owner console`,
      })
      setSelectedRoleGapIds([])
      await loadRoleGaps()
      await loadOperatingCadence(companyContext?.snapshot?.company_namespace)
      onRefresh()
      if (result.failed_count) {
        alert(`Batch ${action} completed with ${result.succeeded_count} success and ${result.failed_count} failure.`)
      }
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setBatchProcessing(false)
    }
  }

  const handleRoleGapAction = async (
    gapId: string,
    action: 'propose' | 'apply' | 'regenerate' | 'defer' | 'dismiss'
  ) => {
    try {
      if (action === 'propose') {
        await api.proposeRoleGap(gapId, companyProfileForRoleGap())
      } else if (action === 'apply') {
        const res = await api.applyRoleGap(gapId, companyProfileForRoleGap())
        if (res.approval_required) {
          alert(
            `Approval required before creating this role. Review approval ${res.approval_id} in Approvals, then click Create Role again.`
          )
        } else {
          onRefresh()
        }
      } else if (action === 'regenerate') {
        const res = await api.regenerateRoleGapApproval(gapId, companyProfileForRoleGap())
        alert(`Approval ${res.approval_id} is ready for review in Approvals.`)
      } else if (action === 'defer') {
        await api.resolveRoleGap(gapId, 'deferred', 'Deferred from owner console')
      } else {
        await api.resolveRoleGap(gapId, 'dismissed', 'Dismissed from owner console')
      }
      await loadRoleGaps()
      await loadOperatingCadence(companyContext?.snapshot?.company_namespace)
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
            onClick={handleSyncCompanyContext}
            disabled={syncingCompanyContext}
            className="btn-secondary flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${syncingCompanyContext ? 'animate-spin' : ''}`} />
            Sync from ERPNext
          </button>
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

      {(companyContext || companyContextError) && (
        <div className="card border-slate-700 bg-slate-900/70">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <Database className="mt-1 h-5 w-5 text-emerald-300" />
              <div>
                <h3 className="font-semibold">ERPNext Company Context</h3>
                {companyContextError ? (
                  <p className="mt-1 text-sm text-red-300">{companyContextError}</p>
                ) : (
                  <p className="mt-1 text-sm text-slate-400">
                    {companyContext?.normalized_company_profile?.name || 'Company context'} ·{' '}
                    {companyContext?.freshness?.status || 'unknown'} ·{' '}
                    {companyContext?.freshness?.last_sync_at
                      ? new Date(companyContext.freshness.last_sync_at).toLocaleString()
                      : 'never synced'}
                  </p>
                )}
              </div>
            </div>
            {companyContext?.snapshot && (
              <div className="grid grid-cols-3 gap-3 text-right text-sm">
                <div>
                  <p className="text-slate-500">Agents</p>
                  <p className="text-slate-100">{companyContext.snapshot.agent_ids?.length || 0}</p>
                </div>
                <div>
                  <p className="text-slate-500">Plans</p>
                  <p className="text-slate-100">{companyContext.pending_plans?.length || 0}</p>
                </div>
                <div>
                  <p className="text-slate-500">Records</p>
                  <p className="text-slate-100">
                    {Object.values(companyContext.snapshot.erpnext_summary?.counts || {})
                      .reduce((total: number, value: any) => total + Number(value || 0), 0)}
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {(operatingCadence || operatingCadenceError) && (
        <div className="card border-slate-700 bg-slate-900/70">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <CalendarClock className="mt-1 h-5 w-5 text-blue-300" />
              <div>
                <h3 className="font-semibold">Operating Cadence</h3>
                {operatingCadenceError ? (
                  <p className="mt-1 text-sm text-red-300">{operatingCadenceError}</p>
                ) : (
                  <p className="mt-1 text-sm text-slate-400">
                    {operatingCadence?.counts?.cadences || 0} agent cadences ·{' '}
                    {operatingCadence?.counts?.active_role_gaps || 0} active role recommendations ·{' '}
                    {operatingCadence?.counts?.stale_role_gaps || 0} stale recommendations
                  </p>
                )}
              </div>
            </div>
            <button
              onClick={() => loadOperatingCadence(companyContext?.snapshot?.company_namespace)}
              className="btn-secondary text-sm"
            >
              Refresh Cadence
            </button>
          </div>
          {operatingCadence?.recommended_owner_actions?.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {operatingCadence.recommended_owner_actions.map((action: any) => (
                <span
                  key={action.action}
                  title={action.reason}
                  className="rounded-full bg-amber-600/20 px-2 py-1 text-xs text-amber-200"
                >
                  {action.action.replace(/_/g, ' ')}
                </span>
              ))}
            </div>
          )}
          {operatingCadence?.cadences?.length > 0 && (
            <div className="mt-4 grid gap-2 md:grid-cols-3">
              {operatingCadence.cadences.slice(0, 6).map((item: any) => (
                <div
                  key={`${item.agent_id}-${item.cadence?.cadence_id}`}
                  className="rounded border border-slate-700 bg-slate-950/60 px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-white">{item.role_name}</span>
                    <span className="text-xs text-blue-200">{item.cadence?.frequency}</span>
                  </div>
                  <p className="mt-1 truncate text-xs text-slate-500">{item.cadence?.review_window}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="card border-slate-700 bg-slate-900/70">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <Shield className="mt-1 h-5 w-5 text-emerald-300" />
            <div>
              <h3 className="font-semibold">Safe Team Activation</h3>
              <p className="mt-1 text-sm text-slate-400">
                {teamActivation?.latest_run
                  ? `${teamActivation.status} · ${teamActivation.active_agent_count || 0} active agents · ${teamActivation.active_grant_count || 0} active grants`
                  : 'No activation run recorded yet'}
              </p>
            </div>
          </div>
          <button
            onClick={handleTeamActivation}
            disabled={activatingTeam}
            className="btn-primary flex items-center gap-2 text-sm"
          >
            <Check className="h-4 w-4" />
            {activatingTeam ? 'Activating...' : 'Activate Safe Team'}
          </button>
        </div>
        {teamActivation?.latest_run && (
          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <div className="rounded border border-slate-700 bg-slate-950/60 px-3 py-2">
              <p className="text-xs uppercase tracking-wide text-slate-500">Last Run</p>
              <p className="mt-1 text-sm font-medium text-white">
                {teamActivation.latest_run.completed_at
                  ? new Date(teamActivation.latest_run.completed_at).toLocaleString()
                  : teamActivation.latest_run.status}
              </p>
            </div>
            <div className="rounded border border-slate-700 bg-slate-950/60 px-3 py-2">
              <p className="text-xs uppercase tracking-wide text-slate-500">Created</p>
              <p className="mt-1 text-lg font-semibold text-white">
                {teamActivation.latest_run.counts?.agents_created || 0}
              </p>
            </div>
            <div className="rounded border border-slate-700 bg-slate-950/60 px-3 py-2">
              <p className="text-xs uppercase tracking-wide text-slate-500">Pending Grants</p>
              <p className="mt-1 text-lg font-semibold text-white">
                {teamActivation.pending_or_blocked_grant_count || 0}
              </p>
            </div>
            <div className="rounded border border-slate-700 bg-slate-950/60 px-3 py-2">
              <p className="text-xs uppercase tracking-wide text-slate-500">Status</p>
              <p className="mt-1 text-sm font-semibold text-white">
                {teamActivation.status}
              </p>
            </div>
          </div>
        )}
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
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-white">Recommended Roles</h3>
            <p className="text-sm text-slate-400 mt-1">
              Owner-reviewed role backlog from company context, supervisors, and operating loops
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
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
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-4">
          <div>
            <label className="mb-1 block text-xs text-slate-500">Status</label>
            <select
              value={roleGapStatusFilter}
              onChange={(event) => setRoleGapStatusFilter(event.target.value)}
              className={selectClassName}
            >
              <option value="open,proposed">Active</option>
              <option value="open">Open</option>
              <option value="proposed">Proposed</option>
              <option value="stale">Stale</option>
              <option value="deferred">Deferred</option>
              <option value="resolved">Resolved</option>
              <option value="dismissed">Dismissed</option>
              <option value="all">All</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-500">Source</label>
            <select
              value={roleGapSourceFilter}
              onChange={(event) => setRoleGapSourceFilter(event.target.value)}
              className={selectClassName}
            >
              <option value="company_context_snapshot">ERPNext context</option>
              <option value="">All sources</option>
              <option value="agent">Agents</option>
              <option value="system">System</option>
              <option value="owner">Owner</option>
            </select>
          </div>
          <div className="rounded border border-slate-700 bg-slate-900/60 px-3 py-2">
            <p className="text-xs uppercase tracking-wide text-slate-500">Review Items</p>
            <p className="mt-1 text-xl font-semibold text-white">
              {roleBacklog?.counts?.total ?? roleBacklogItems.length}
            </p>
          </div>
          <div className="rounded border border-slate-700 bg-slate-900/60 px-3 py-2">
            <p className="text-xs uppercase tracking-wide text-slate-500">Needs Approval</p>
            <p className="mt-1 text-xl font-semibold text-white">
              {roleBacklog?.approval_count ?? 0}
            </p>
          </div>
        </div>

        {selectableRoleGapIds.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2 rounded border border-slate-700 bg-slate-950/60 px-3 py-3">
            <button onClick={toggleAllVisibleRoleGaps} className="btn-secondary text-sm">
              {selectableRoleGapIds.every((id: string) => selectedRoleGapIds.includes(id))
                ? 'Clear Selection'
                : 'Select Visible'}
            </button>
            <span className="text-sm text-slate-400">{selectedRoleGapIds.length} selected</span>
            <div className="ml-auto flex flex-wrap gap-2">
              <button
                onClick={() => handleBatchRoleGapAction('propose')}
                disabled={batchProcessing || selectedRoleGapIds.length === 0}
                className="btn-secondary text-sm"
              >
                Propose Selected
              </button>
              <button
                onClick={() => handleBatchRoleGapAction('regenerate_approval')}
                disabled={batchProcessing || selectedRoleGapIds.length === 0}
                className="btn-primary text-sm"
              >
                Request Approvals
              </button>
              <button
                onClick={() => handleBatchRoleGapAction('apply')}
                disabled={batchProcessing || selectedRoleGapIds.length === 0}
                className="btn-primary text-sm"
              >
                Create Approved
              </button>
              <button
                onClick={() => handleBatchRoleGapAction('defer')}
                disabled={batchProcessing || selectedRoleGapIds.length === 0}
                className="btn-secondary text-sm"
              >
                Defer
              </button>
              <button
                onClick={() => handleBatchRoleGapAction('dismiss')}
                disabled={batchProcessing || selectedRoleGapIds.length === 0}
                className="btn-danger text-sm"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {roleBacklog?.groups?.length > 0 && (
          <div className="mt-4 grid gap-2 md:grid-cols-3">
            {roleBacklog.groups.map((group: any) => (
              <div
                key={group.business_function}
                className="rounded border border-slate-700 bg-slate-900/50 px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-white">{group.business_function}</span>
                  <span className="text-xs text-slate-400">{group.count}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1 text-xs text-slate-500">
                  {group.requested_tools.slice(0, 4).map((tool: string) => (
                    <span key={`${group.business_function}-${tool}`}>{tool}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="mt-4 space-y-3">
          {roleBacklogItems.map((item: any) => {
            const gapId = item.gap_id || item.id
            const proposal = item.proposed_role?.manifest_payload || item.proposed_role?.manifest_payload
            const readinessItems = item.tool_readiness?.items || []
            const action = item.recommended_action || (item.status === 'open' ? 'propose_role' : 'create_role')
            return (
              <div key={gapId} className="rounded-lg border border-slate-700 bg-slate-900/50 p-4">
                <div className="flex items-start justify-between gap-4">
                  {['open', 'proposed'].includes(item.status) && (
                    <input
                      type="checkbox"
                      checked={selectedRoleGapIds.includes(gapId)}
                      onChange={() => toggleRoleGapSelection(gapId)}
                      className="mt-1 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500"
                      aria-label={`Select ${item.title}`}
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="font-medium text-white">{item.title}</h4>
                      <span className="badge badge-info">{item.status}</span>
                      <span className="badge-warning">{item.risk_level || item.severity}</span>
                      <span className={readinessBadgeClass(item)}>
                        {recommendedActionLabel(action)}
                      </span>
                    </div>
                    <p className="mt-2 text-sm text-slate-400">{item.description}</p>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                      {item.business_function && <span>Function: {item.business_function}</span>}
                      {item.source_snapshot_id && <span>Snapshot: {item.source_snapshot_id}</span>}
                      {item.source_plan_id && <span>Plan: {item.source_plan_id}</span>}
                      {item.source_task_id && <span>Task: {item.source_task_id}</span>}
                    </div>
                    {proposal && (
                      <div className="mt-3 rounded border border-slate-800 bg-slate-950/70 p-3 text-xs text-slate-300">
                        <span className="font-semibold text-slate-100">Proposal:</span>{' '}
                        {proposal.name} ({proposal.family}) ·{' '}
                        {(proposal.default_tools || []).join(', ') || 'no tools'}
                      </div>
                    )}
                    {readinessItems.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2 text-xs">
                        {readinessItems.map((tool: any) => (
                          <span
                            key={`${gapId}-${tool.tool_name}`}
                            title={tool.reason}
                            className={`rounded-full px-2 py-1 ${
                              tool.executable
                                ? 'bg-emerald-600/20 text-emerald-300'
                                : 'bg-red-600/20 text-red-300'
                            }`}
                          >
                            {tool.tool_name}: {tool.state}
                          </span>
                        ))}
                      </div>
                    )}
                    {item.approval?.required && (
                      <div className="mt-3 rounded border border-amber-900/70 bg-amber-950/30 p-3 text-xs text-amber-100">
                        <span className="font-semibold">Approval:</span>{' '}
                        {item.approval.state}
                        {item.approval.approval_id && (
                          <span className="ml-1 text-amber-200/80">
                            ({item.approval.approval_id})
                          </span>
                        )}
                        {item.approval.expires_at && (
                          <span className="ml-2 text-amber-200/70">
                            Expires {new Date(item.approval.expires_at).toLocaleString()}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-wrap justify-end gap-2">
                    {action === 'propose_role' && (
                      <button
                        onClick={() => handleRoleGapAction(gapId, 'propose')}
                        className="btn-secondary text-sm"
                      >
                        Propose
                      </button>
                    )}
                    {['create_role', 'create_after_approval'].includes(action) && (
                      <button
                        onClick={() => handleRoleGapAction(gapId, 'apply')}
                        className="btn-primary text-sm"
                      >
                        Create
                      </button>
                    )}
                    {['request_approval', 'regenerate_approval'].includes(action) && (
                      <button
                        onClick={() => handleRoleGapAction(gapId, 'regenerate')}
                        className="btn-primary text-sm"
                      >
                        Regenerate Approval
                      </button>
                    )}
                    {action === 'await_approval' && (
                      <button disabled className="btn-secondary text-sm opacity-60">
                        Awaiting Approval
                      </button>
                    )}
                    {['open', 'proposed'].includes(item.status) && (
                      <button
                        onClick={() => handleRoleGapAction(gapId, 'defer')}
                        className="btn-secondary text-sm"
                      >
                        Defer
                      </button>
                    )}
                    {['open', 'proposed', 'deferred'].includes(item.status) && (
                      <button
                        onClick={() => handleRoleGapAction(gapId, 'dismiss')}
                        className="btn-danger text-sm"
                      >
                        Dismiss
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
          {!loadingRoleGaps && roleBacklogItems.length === 0 && (
            <div className="rounded-lg border border-dashed border-slate-700 p-6 text-center text-sm text-slate-500">
              {roleGapStatusFilter === 'open,proposed'
                ? 'No active role recommendations for this source.'
                : 'No role recommendations match the selected filters.'}
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
              <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
                <span>
                  Active grants:{' '}
                  <span className="font-semibold text-emerald-300">
                    {agentGrantCounts[agent.id]?.active || 0}
                  </span>
                </span>
                <span>
                  Pending:{' '}
                  <span className="font-semibold text-amber-300">
                    {agentGrantCounts[agent.id]?.pending || 0}
                  </span>
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
