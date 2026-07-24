'use client'

import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/api'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Clock,
  Eye,
  GitBranch,
  Play,
  RotateCw,
  Sparkles,
  CheckCircle,
  XCircle,
} from 'lucide-react'

export default function WorkflowsView() {
  const [workflows, setWorkflows] = useState<any[]>([])
  const [runs, setRuns] = useState<Record<string, any[]>>({})
  const [expandedWorkflow, setExpandedWorkflow] = useState<string | null>(null)
  const [expandedRun, setExpandedRun] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState<string | null>(null)
  const [templates, setTemplates] = useState<any[]>([])
  const [instantiatingTemplate, setInstantiatingTemplate] = useState<string | null>(null)
  const [intents, setIntents] = useState<any[]>([])
  const [intentGroups, setIntentGroups] = useState<any[]>([])
  const [generatingIntents, setGeneratingIntents] = useState(false)
  const [intentAction, setIntentAction] = useState<string | null>(null)

  const loadWorkflows = useCallback(async () => {
    try {
      const [res, templateList, intentSummary] = await Promise.all([
        api.listWorkflows(),
        api.listWorkflowTemplates({ status: 'active', isCore: true }),
        api.listWorkflowIntents({ status: 'proposed,instantiated,blocked', limit: 100 }),
      ])
      setWorkflows(res)
      setTemplates(templateList)
      setIntents(intentSummary.items || [])
      setIntentGroups(intentSummary.groups || [])
    } catch (e) {
      console.error('Failed to load workflows:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadRuns = useCallback(async (workflowId: string) => {
    setRefreshing(workflowId)
    try {
      const runList = await api.listWorkflowRuns(workflowId)
      setRuns(prev => ({ ...prev, [workflowId]: runList }))
    } catch (e) {
      console.error(`Failed to load runs for workflow ${workflowId}:`, e)
    } finally {
      setRefreshing(null)
    }
  }, [])

  useEffect(() => {
    loadWorkflows()
  }, [loadWorkflows])

  useEffect(() => {
    if (expandedWorkflow) {
      loadRuns(expandedWorkflow)
      const interval = setInterval(() => loadRuns(expandedWorkflow), 8000)
      return () => clearInterval(interval)
    }
  }, [expandedWorkflow, loadRuns])

  const handleRunWorkflow = async (workflowId: string) => {
    try {
      const res = await api.runWorkflow(workflowId)
      alert(`Workflow Run started successfully! Run ID: ${res.id}`)
      if (expandedWorkflow === workflowId) {
        loadRuns(workflowId)
      } else {
        setExpandedWorkflow(workflowId)
      }
    } catch (e: any) {
      alert(`Error starting workflow: ${e.message}`)
    }
  }

  const handleResumeRun = async (workflowId: string, runId: string) => {
    try {
      await api.resumeWorkflowRun(runId)
      alert('Resumed signal sent successfully!')
      loadRuns(workflowId)
    } catch (e: any) {
      alert(`Error resuming workflow: ${e.message}`)
    }
  }

  const handleInstantiateTemplate = async (templateId: string) => {
    setInstantiatingTemplate(templateId)
    try {
      const workflow = await api.instantiateWorkflowTemplate(templateId)
      await loadWorkflows()
      setExpandedWorkflow(workflow.id)
    } catch (e: any) {
      alert(`Error creating workflow: ${e.message}`)
    } finally {
      setInstantiatingTemplate(null)
    }
  }

  const handleGenerateIntents = async () => {
    setGeneratingIntents(true)
    try {
      await api.generateWorkflowIntents({ instantiateLowRisk: false })
      await loadWorkflows()
    } catch (e: any) {
      alert(`Error generating workflow intents: ${e.message}`)
    } finally {
      setGeneratingIntents(false)
    }
  }

  const handleInstantiateIntent = async (intentId: string) => {
    setIntentAction(intentId)
    try {
      const workflow = await api.instantiateWorkflowIntent(intentId)
      await loadWorkflows()
      setExpandedWorkflow(workflow.id)
    } catch (e: any) {
      alert(`Error creating workflow from intent: ${e.message}`)
    } finally {
      setIntentAction(null)
    }
  }

  const handleDismissIntent = async (intentId: string) => {
    setIntentAction(intentId)
    try {
      await api.resolveWorkflowIntent(intentId, 'dismissed', 'Dismissed from Workflows view')
      await loadWorkflows()
    } catch (e: any) {
      alert(`Error dismissing workflow intent: ${e.message}`)
    } finally {
      setIntentAction(null)
    }
  }

  const statusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="w-5 h-5 text-green-500" />
      case 'failed':
        return <XCircle className="w-5 h-5 text-red-500" />
      case 'rejected':
        return <XCircle className="w-5 h-5 text-red-400" />
      case 'running':
        return <Clock className="w-5 h-5 text-yellow-500 animate-pulse" />
      case 'waiting_approval':
        return <Clock className="w-5 h-5 text-amber-500 animate-bounce" />
      default:
        return <GitBranch className="w-5 h-5 text-slate-400" />
    }
  }

  const readinessBadge = (status: string) => {
    switch (status) {
      case 'ready':
        return <span className="badge badge-success">Ready</span>
      case 'owner_review':
        return <span className="badge badge-warning">Owner Review</span>
      case 'configuration_required':
        return <span className="badge badge-warning border border-amber-700">Config Required</span>
      case 'blocked':
        return <span className="badge badge-danger">Blocked</span>
      default:
        return <span className="badge bg-slate-800 text-slate-400">{status || 'unknown'}</span>
    }
  }

  const statusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return <span className="badge badge-success">Completed</span>
      case 'failed':
        return <span className="badge badge-danger">Failed</span>
      case 'rejected':
        return <span className="badge badge-danger bg-red-950/40 border border-red-800">Rejected</span>
      case 'running':
        return <span className="badge badge-info animate-pulse">Running</span>
      case 'waiting_approval':
        return <span className="badge badge-warning animate-pulse border border-yellow-600">Waiting Approval</span>
      default:
        return <span className="badge bg-slate-800 text-slate-400">{status}</span>
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold">Workflows</h2>
        <p className="text-slate-400 mt-1">Design, execute, and monitor durable company automation workflows</p>
      </div>

      <div className="card border-slate-700 bg-slate-900/70">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-white">Generated Workflow Intents</h3>
            <p className="mt-1 text-sm text-slate-400">
              Review workflow proposals derived from ERPNext context, role capabilities, and role gaps
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={loadWorkflows} className="btn-secondary text-sm">
              Refresh
            </button>
            <button
              onClick={handleGenerateIntents}
              disabled={generatingIntents}
              className="btn-primary text-sm flex items-center gap-2"
            >
              <Sparkles className={`h-4 w-4 ${generatingIntents ? 'animate-pulse' : ''}`} />
              {generatingIntents ? 'Generating...' : 'Generate From Context'}
            </button>
          </div>
        </div>

        {intentGroups.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            {intentGroups.slice(0, 8).map((group: any) => (
              <span key={group.business_function} className="rounded-full border border-slate-700 px-3 py-1 text-slate-300">
                {group.business_function}: {group.count}
              </span>
            ))}
          </div>
        )}

        {intents.length > 0 ? (
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {intents.slice(0, 12).map((intent: any) => {
              const readiness = intent.readiness || {}
              const readinessStatus = readiness.status || 'unknown'
              const canInstantiate = ['ready', 'owner_review'].includes(readinessStatus) && !intent.workflow_id
              const blockedReason = (readiness.blockers || readiness.warnings || []).slice(0, 2).join(' ')
              const optionalDisabledTools = readiness.optional_disabled_tools || []
              const configurationRequiredTools = readiness.configuration_required_tools || []
              const approvalGatedTools = readiness.approval_gated_tools || []

              return (
                <div key={intent.id} className="rounded border border-slate-700 bg-slate-950/60 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h4 className="break-words text-sm font-medium text-white">{intent.title}</h4>
                      <p className="mt-1 line-clamp-2 text-xs text-slate-500">
                        {intent.description}
                      </p>
                    </div>
                    {readinessBadge(readinessStatus)}
                  </div>

                  <div className="mt-3 grid gap-2 text-xs text-slate-400 sm:grid-cols-3">
                    <span>Function: <span className="text-slate-200">{intent.business_function}</span></span>
                    <span>Source: <span className="text-slate-200">{intent.source_type}</span></span>
                    <span>Risk: <span className="text-slate-200">{intent.risk_level}</span></span>
                  </div>

                  {intent.requested_tools?.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1">
                      {intent.requested_tools.slice(0, 6).map((tool: string) => (
                        <span key={tool} className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                          {tool}
                        </span>
                      ))}
                    </div>
                  )}

                  {blockedReason && (
                    <div className="mt-3 flex items-start gap-2 rounded border border-amber-900/70 bg-amber-950/20 p-2 text-xs text-amber-200">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
                      <span className="line-clamp-2">{blockedReason}</span>
                    </div>
                  )}

                  {(optionalDisabledTools.length > 0 || configurationRequiredTools.length > 0 || approvalGatedTools.length > 0) && (
                    <div className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
                      {optionalDisabledTools.length > 0 && (
                        <div className="rounded border border-slate-700 bg-slate-900/70 p-2 text-slate-300">
                          <div className="font-medium text-slate-200">Optional Disabled</div>
                          <div className="mt-1 line-clamp-2">
                            {optionalDisabledTools.map((tool: any) => tool.tool_name).join(', ')}
                          </div>
                        </div>
                      )}
                      {configurationRequiredTools.length > 0 && (
                        <div className="rounded border border-amber-800 bg-amber-950/20 p-2 text-amber-200">
                          <div className="font-medium">Config Required</div>
                          <div className="mt-1 line-clamp-2">
                            {configurationRequiredTools.map((tool: any) => tool.tool_name).join(', ')}
                          </div>
                        </div>
                      )}
                      {approvalGatedTools.length > 0 && (
                        <div className="rounded border border-blue-800 bg-blue-950/20 p-2 text-blue-200">
                          <div className="font-medium">Approval Gated</div>
                          <div className="mt-1 line-clamp-2">
                            {approvalGatedTools.map((tool: any) => tool.tool_name).join(', ')}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      onClick={() => handleInstantiateIntent(intent.id)}
                      disabled={!canInstantiate || intentAction === intent.id}
                      className="btn-secondary text-sm"
                    >
                      {intent.workflow_id
                        ? 'Workflow Ready'
                        : intentAction === intent.id
                          ? 'Working...'
                          : 'Create Workflow'}
                    </button>
                    {!intent.workflow_id && (
                      <button
                        onClick={() => handleDismissIntent(intent.id)}
                        disabled={intentAction === intent.id}
                        className="btn-secondary text-sm text-slate-400"
                      >
                        Dismiss
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="mt-4 rounded border border-dashed border-slate-700 p-6 text-center text-sm text-slate-500">
            No generated workflow intents yet. Generate them after an ERPNext company-context sync.
          </div>
        )}
      </div>

      {templates.length > 0 && (
        <div className="card border-slate-700 bg-slate-900/70">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h3 className="text-lg font-semibold text-white">Core Workflow Templates</h3>
              <p className="mt-1 text-sm text-slate-400">
                {templates.length} safe templates available for company operations
              </p>
            </div>
            <button onClick={loadWorkflows} className="btn-secondary text-sm">
              Refresh
            </button>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {templates.map((template: any) => {
              const alreadyCreated = workflows.some(
                (workflow: any) => workflow.trigger_config?.template_id === template.id,
              )
              return (
                <div key={template.id} className="rounded border border-slate-700 bg-slate-950/60 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h4 className="text-sm font-medium text-white">{template.name}</h4>
                      <p className="mt-1 line-clamp-2 text-xs text-slate-500">
                        {template.description}
                      </p>
                    </div>
                    <span className="badge badge-info">{template.category}</span>
                  </div>
                  <button
                    onClick={() => handleInstantiateTemplate(template.id)}
                    disabled={alreadyCreated || instantiatingTemplate === template.id}
                    className="btn-secondary mt-3 w-full text-sm"
                  >
                    {alreadyCreated
                      ? 'Workflow Ready'
                      : instantiatingTemplate === template.id
                        ? 'Creating...'
                        : 'Create Workflow'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6">
        {workflows.map((wf: any) => {
          const isExpanded = expandedWorkflow === wf.id
          const wfRuns = runs[wf.id] || []

          return (
            <div key={wf.id} className={`card transition-all duration-300 border ${isExpanded ? 'border-blue-500 bg-slate-800/60' : 'border-slate-700/60 hover:border-slate-600'}`}>
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 bg-blue-900/30 rounded-lg flex items-center justify-center">
                    <GitBranch className="w-5 h-5 text-blue-400" />
                  </div>
                  <div>
                    <h4 className="font-semibold text-white text-lg">{wf.name}</h4>
                    <p className="text-xs text-slate-500">Trigger: <span className="text-slate-400 font-medium uppercase">{wf.trigger_type}</span></p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleRunWorkflow(wf.id)}
                    className="btn-primary text-sm flex items-center gap-2 px-3 py-1.5"
                  >
                    <Play className="w-4 h-4 fill-current" />
                    Execute Workflow
                  </button>
                  <button
                    onClick={() => setExpandedWorkflow(isExpanded ? null : wf.id)}
                    className="btn-secondary p-1.5 text-slate-400 hover:text-white"
                  >
                    {isExpanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
                  </button>
                </div>
              </div>

              {wf.description && (
                <p className="text-sm text-slate-400 mt-3 pl-13">{wf.description}</p>
              )}

              {isExpanded && (
                <div className="mt-6 border-t border-slate-700/60 pt-6 space-y-4">
                  <div className="flex items-center justify-between">
                    <h5 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
                      <Clock className="w-4 h-4" />
                      Execution Runs & History
                    </h5>
                    <button
                      onClick={() => loadRuns(wf.id)}
                      disabled={refreshing === wf.id}
                      className="text-xs text-slate-400 hover:text-white flex items-center gap-1.5"
                    >
                      <RotateCw className={`w-3.5 h-3.5 ${refreshing === wf.id ? 'animate-spin' : ''}`} />
                      Refresh
                    </button>
                  </div>

                  <div className="space-y-3">
                    {wfRuns.map((run: any) => {
                      const isRunExpanded = expandedRun === run.id
                      return (
                        <div key={run.id} className="rounded-lg border border-slate-700/65 bg-slate-900/40 p-4">
                          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                            <div className="flex items-start gap-2.5">
                              {statusIcon(run.status)}
                              <div>
                                <span className="font-mono text-xs text-slate-400 font-semibold block">{run.id}</span>
                                <div className="flex items-center gap-2 mt-1">
                                  {statusBadge(run.status)}
                                  {run.current_node && (
                                    <span className="text-xs text-slate-500">
                                      Active Node: <span className="text-slate-300 font-mono font-semibold">{run.current_node}</span>
                                    </span>
                                  )}
                                </div>
                              </div>
                            </div>

                            <div className="flex items-center gap-2 self-end sm:self-center">
                              {run.status === 'waiting_approval' && (
                                <button
                                  onClick={() => handleResumeRun(wf.id, run.id)}
                                  className="btn-primary bg-amber-600 hover:bg-amber-700 text-xs px-2.5 py-1"
                                >
                                  Resume Execution
                                </button>
                              )}
                              <button
                                onClick={() => setExpandedRun(isRunExpanded ? null : run.id)}
                                className="btn-secondary text-xs px-2.5 py-1 flex items-center gap-1"
                              >
                                <Eye className="w-3.5 h-3.5" />
                                Inspect State
                              </button>
                            </div>
                          </div>

                          {isRunExpanded && (
                            <div className="mt-3 border-t border-slate-800 pt-3">
                              <span className="text-xs text-slate-500 font-semibold block mb-1">State Dictionary (JSON):</span>
                              <pre className="text-xs text-slate-300 font-mono bg-slate-950/80 p-3 rounded-lg overflow-x-auto border border-slate-800 max-h-48">
                                {JSON.stringify(run.state, null, 2)}
                              </pre>
                              {run.error && (
                                <div className="mt-2 rounded border border-red-900 bg-red-950/30 p-2 text-xs text-red-400 font-mono">
                                  <strong>Error:</strong> {run.error}
                                </div>
                              )}
                              {run.result && (
                                <div className="mt-2">
                                  <span className="text-xs text-slate-500 font-semibold block mb-1">Execution Result:</span>
                                  <pre className="text-xs text-green-400 font-mono bg-slate-950/80 p-3 rounded-lg overflow-x-auto border border-slate-800 max-h-48">
                                    {JSON.stringify(run.result, null, 2)}
                                  </pre>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )
                    })}

                    {wfRuns.length === 0 && (
                      <div className="text-center py-6 text-slate-500 border border-dashed border-slate-700/60 rounded-lg text-sm">
                        No runs recorded for this workflow yet. Click &quot;Execute Workflow&quot; to launch.
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}

        {workflows.length === 0 && !loading && (
          <div className="card text-center py-12 text-slate-500 border-dashed border-slate-700">
            <GitBranch className="w-12 h-12 mx-auto mb-4 opacity-50 text-blue-400" />
            <p className="text-white font-medium">No workflows found</p>
            <p className="text-sm mt-1">Create workflows through the API or bootstrap via Company Builder.</p>
          </div>
        )}
      </div>
    </div>
  )
}
