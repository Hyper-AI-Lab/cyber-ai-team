'use client'

import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/api'
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle,
  Clock,
  Database,
  GitBranch,
  Play,
  RefreshCw,
  Search,
  Tag,
} from 'lucide-react'

export default function MemoryView() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [searching, setSearching] = useState(false)
  const [traces, setTraces] = useState<any[]>([])
  const [loadingTraces, setLoadingTraces] = useState(false)
  const [traceSourceType, setTraceSourceType] = useState('')
  const [traceCoverage, setTraceCoverage] = useState('')
  const [traceAgentId, setTraceAgentId] = useState('')
  const [traceConversationId, setTraceConversationId] = useState('')
  const [traceWorkflowRunId, setTraceWorkflowRunId] = useState('')
  const [traceToolName, setTraceToolName] = useState('')
  const [traceNamespace, setTraceNamespace] = useState('')
  const [findings, setFindings] = useState<any[]>([])
  const [loadingFindings, setLoadingFindings] = useState(false)
  const [runningSteward, setRunningSteward] = useState(false)
  const [planningRemediations, setPlanningRemediations] = useState(false)
  const [runningAction, setRunningAction] = useState<string | null>(null)

  const handleSearch = async () => {
    if (!query) return
    setSearching(true)
    try {
      const res = await api.recallMemory(query, undefined, 20)
      setResults(res)
    } catch (e: any) {
      console.error('Memory search failed:', e)
    } finally {
      setSearching(false)
    }
  }

  const loadTraces = useCallback(async () => {
    setLoadingTraces(true)
    try {
      const res = await api.listMemoryTraces({
        agentId: traceAgentId || undefined,
        sourceType: traceSourceType || undefined,
        conversationId: traceConversationId || undefined,
        workflowRunId: traceWorkflowRunId || undefined,
        toolName: traceToolName || undefined,
        memoryNamespace: traceNamespace || undefined,
        coverage: traceCoverage || undefined,
        limit: 25,
      })
      setTraces(res)
    } catch (e: any) {
      console.error('Memory trace fetch failed:', e)
    } finally {
      setLoadingTraces(false)
    }
  }, [
    traceAgentId,
    traceConversationId,
    traceCoverage,
    traceNamespace,
    traceSourceType,
    traceToolName,
    traceWorkflowRunId,
  ])

  const loadFindings = useCallback(async () => {
    setLoadingFindings(true)
    try {
      const res = await api.listMemoryStewardFindings('open', 25)
      setFindings(res)
    } catch (e: any) {
      console.error('Memory steward finding fetch failed:', e)
    } finally {
      setLoadingFindings(false)
    }
  }, [])

  useEffect(() => {
    void loadTraces()
    void loadFindings()
  }, [loadFindings, loadTraces])

  const runSteward = async () => {
    setRunningSteward(true)
    try {
      await api.runMemorySteward()
      await loadFindings()
      await loadTraces()
    } catch (e: any) {
      console.error('Memory steward run failed:', e)
    } finally {
      setRunningSteward(false)
    }
  }

  const planRemediations = async () => {
    setPlanningRemediations(true)
    try {
      await api.planMemorySteward(true, true, 100)
      await loadFindings()
      await loadTraces()
    } catch (e: any) {
      console.error('Memory steward planning failed:', e)
    } finally {
      setPlanningRemediations(false)
    }
  }

  const resolveFinding = async (findingId: string) => {
    try {
      await api.resolveMemoryStewardFinding(findingId, 'resolved', 'Reviewed in owner console')
      await loadFindings()
    } catch (e: any) {
      console.error('Memory steward finding resolution failed:', e)
    }
  }

  const executeFindingAction = async (findingId: string, actionType: string) => {
    const actionKey = `${findingId}:${actionType}`
    setRunningAction(actionKey)
    try {
      await api.executeMemoryStewardAction(
        findingId,
        actionType as 'seed_memory' | 'report_role_gap',
      )
      await loadFindings()
      await loadTraces()
    } catch (e: any) {
      console.error('Memory steward action failed:', e)
    } finally {
      setRunningAction(null)
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-3">
          <Brain className="w-7 h-7 text-blue-400" />
          <h2 className="text-2xl font-bold">Memory</h2>
        </div>
        <p className="text-slate-400 mt-1">Search and browse the company&apos;s collective memory</p>
      </div>

      {/* Search */}
      <div className="card">
        <div className="flex gap-3">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="w-full bg-slate-700 border border-slate-600 rounded-lg pl-10 pr-3 py-2.5 text-white"
              placeholder="Search memories..."
            />
          </div>
          <button onClick={handleSearch} disabled={searching} className="btn-primary">
            {searching ? 'Searching...' : 'Search'}
          </button>
        </div>
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold">Search Results</h3>
          {results.map((r: any) => (
            <div key={r.id} className="card">
              <div className="flex items-center gap-2 mb-2">
                <Tag className="w-4 h-4 text-blue-400" />
                <span className="badge-info">{r.memory_type}</span>
                <span className="text-xs text-slate-500">{r.namespace}</span>
                {r.score !== undefined && (
                  <span className="ml-auto text-xs text-slate-500">Score: {r.score.toFixed(3)}</span>
                )}
              </div>
              <p className="text-sm text-slate-300">{r.content}</p>
            </div>
          ))}
        </div>
      )}

      {results.length === 0 && query && !searching && (
        <div className="text-center text-slate-500 py-8">
          No results found. Try a different query.
        </div>
      )}

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold">Memory Steward</h3>
            <p className="text-sm text-slate-400 mt-1">Trace-driven memory health findings</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={loadFindings}
              disabled={loadingFindings}
              className="btn-secondary flex items-center gap-2"
            >
              <RefreshCw className={`w-4 h-4 ${loadingFindings ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <button
              onClick={runSteward}
              disabled={runningSteward}
              className="btn-primary flex items-center gap-2"
            >
              <Play className="w-4 h-4" />
              {runningSteward ? 'Running...' : 'Run Review'}
            </button>
            <button
              onClick={planRemediations}
              disabled={planningRemediations}
              className="btn-primary flex items-center gap-2"
            >
              <GitBranch className="w-4 h-4" />
              {planningRemediations ? 'Planning...' : 'Plan Actions'}
            </button>
          </div>
        </div>

        {findings.length > 0 ? (
          <div className="space-y-3">
            {findings.map((finding: any) => (
              <div key={finding.id} className="card">
                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <AlertTriangle className="w-4 h-4 text-amber-300" />
                  <span className={
                    finding.severity === 'high' || finding.severity === 'critical'
                      ? 'badge-danger'
                      : 'badge-warning'
                  }>
                    {finding.severity}
                  </span>
                  <span className="badge-info">{finding.finding_type}</span>
                  {finding.agent_id && (
                    <span className="text-xs text-slate-400">{finding.agent_id}</span>
                  )}
                  {finding.company_namespace && (
                    <span className="text-xs text-slate-500">{finding.company_namespace}</span>
                  )}
                </div>
                <h4 className="font-semibold text-slate-100">{finding.title}</h4>
                <p className="mt-2 text-sm text-slate-300">{finding.description}</p>
                <p className="mt-2 text-sm text-slate-400">{finding.recommendation}</p>
                {finding.metadata?.remediation_plan && (
                  <div className="mt-3 rounded border border-slate-700 bg-slate-900/40 p-3 text-xs text-slate-300">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="badge-info">
                        {finding.metadata.remediation_plan.action_type}
                      </span>
                      <span className="rounded border border-slate-700 px-2 py-1 text-slate-400">
                        {finding.metadata.remediation_plan.status}
                      </span>
                      <span className="rounded border border-slate-700 px-2 py-1 text-slate-400">
                        {finding.metadata.remediation_plan.priority}
                      </span>
                      {finding.metadata.remediation_plan.approval_id && (
                        <span className="break-all rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-amber-200">
                          Approval {finding.metadata.remediation_plan.approval_id}
                        </span>
                      )}
                    </div>
                    <div className="mt-2 text-slate-400">
                      {finding.metadata.remediation_plan.reason}
                    </div>
                  </div>
                )}
                {finding.metadata?.last_action && (
                  <div className="mt-3 text-xs text-emerald-300">
                    Last action: {finding.metadata.last_action.action_type} - {finding.metadata.last_action.status}
                  </div>
                )}
                <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-400">
                  <span className="rounded border border-slate-700 px-2 py-1">
                    Traces {finding.trace_ids.length}
                  </span>
                  {finding.memory_namespace && (
                    <span className="rounded border border-slate-700 px-2 py-1">
                      {finding.memory_namespace}
                    </span>
                  )}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {(finding.available_actions || []).map((action: any) => {
                    const actionKey = `${finding.id}:${action.type}`
                    const isRunning = runningAction === actionKey
                    const Icon = action.type === 'seed_memory' ? Database : GitBranch
                    return (
                      <button
                        key={action.type}
                        onClick={() => executeFindingAction(finding.id, action.type)}
                        disabled={Boolean(runningAction)}
                        className="btn-secondary text-xs flex items-center gap-1"
                        title={action.description}
                      >
                        <Icon className="w-3.5 h-3.5" />
                        {isRunning ? 'Applying...' : action.label}
                      </button>
                    )
                  })}
                  <button
                    onClick={() => resolveFinding(finding.id)}
                    className="btn-secondary text-xs flex items-center gap-1"
                  >
                    <CheckCircle className="w-3.5 h-3.5" />
                    Resolve
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="card text-center text-slate-500">
            {loadingFindings ? 'Loading findings...' : 'No open memory steward findings.'}
          </div>
        )}
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold">Invocation Memory Traces</h3>
            <p className="text-sm text-slate-400 mt-1">Recent agent memory reads and writes</p>
          </div>
          <button
            onClick={loadTraces}
            disabled={loadingTraces}
            className="btn-secondary flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${loadingTraces ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>

        <div className="grid gap-3 rounded-lg border border-slate-800 bg-slate-900/30 p-3 text-sm md:grid-cols-3 xl:grid-cols-7">
          <select
            value={traceSourceType}
            onChange={(event) => setTraceSourceType(event.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"
          >
            <option value="">All sources</option>
            <option value="agent_invocation">Agent</option>
            <option value="chat">Chat</option>
            <option value="tool_execution">Tool</option>
            <option value="workflow_agent_activity">Workflow agent</option>
            <option value="workflow_tool_activity">Workflow tool</option>
            <option value="workflow_memory_write">Workflow memory</option>
          </select>
          <select
            value={traceCoverage}
            onChange={(event) => setTraceCoverage(event.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"
          >
            <option value="">All coverage</option>
            <option value="empty">Empty</option>
            <option value="read">Read</option>
            <option value="write">Write</option>
            <option value="read_write">Read/write</option>
            <option value="metadata_only">Metadata only</option>
            <option value="error">Error</option>
          </select>
          <input
            value={traceAgentId}
            onChange={(event) => setTraceAgentId(event.target.value)}
            placeholder="Agent"
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"
          />
          <input
            value={traceNamespace}
            onChange={(event) => setTraceNamespace(event.target.value)}
            placeholder="Namespace"
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"
          />
          <input
            value={traceConversationId}
            onChange={(event) => setTraceConversationId(event.target.value)}
            placeholder="Conversation"
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"
          />
          <input
            value={traceWorkflowRunId}
            onChange={(event) => setTraceWorkflowRunId(event.target.value)}
            placeholder="Workflow run"
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"
          />
          <input
            value={traceToolName}
            onChange={(event) => setTraceToolName(event.target.value)}
            placeholder="Tool"
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"
          />
        </div>

        {traces.length > 0 ? (
          <div className="space-y-3">
            {traces.map((trace: any) => (
              <div key={trace.id} className="card">
                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <Activity className="w-4 h-4 text-emerald-400" />
                  <span className="badge-info">{trace.source_type}</span>
                  {trace.agent_id && (
                    <span className="text-xs text-slate-400">{trace.agent_id}</span>
                  )}
                  {trace.memory_namespace && (
                    <span className="text-xs text-slate-500">{trace.memory_namespace}</span>
                  )}
                  {(trace.metadata?.coverage || trace.metadata?.memory_coverage) && (
                    <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-200">
                      {trace.metadata.coverage || trace.metadata.memory_coverage}
                    </span>
                  )}
                  {trace.metadata?.tool_name && (
                    <span className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-300">
                      {trace.metadata.tool_name}
                    </span>
                  )}
                  {trace.metadata?.workflow_run_id && (
                    <span className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-300">
                      {trace.metadata.workflow_run_id}
                    </span>
                  )}
                  <span className="ml-auto flex items-center gap-1 text-xs text-slate-500">
                    <Clock className="w-3.5 h-3.5" />
                    {new Date(trace.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="text-sm text-slate-300">{trace.task_excerpt}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  <span className="rounded border border-slate-700 px-2 py-1 text-slate-300">
                    Recalled {trace.recall_count}
                  </span>
                  <span className="rounded border border-slate-700 px-2 py-1 text-slate-300">
                    Wrote {trace.write_count}
                  </span>
                  {trace.errors.length > 0 && (
                    <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-amber-200">
                      Errors {trace.errors.length}
                    </span>
                  )}
                </div>
                <details className="mt-3 text-xs text-slate-400">
                  <summary className="cursor-pointer text-slate-300">Trace details</summary>
                  <div className="mt-2 space-y-3">
                    {trace.read_policy?.scope_results?.length > 0 && (
                      <div>
                        <div className="mb-1 font-medium text-slate-300">Scopes</div>
                        <div className="grid gap-2 md:grid-cols-2">
                          {trace.read_policy.scope_results.map((scope: any) => (
                            <div
                              key={`${trace.id}-${scope.name}-${scope.namespace}`}
                              className="rounded border border-slate-700 p-2"
                            >
                              <div className="font-medium text-slate-300">{scope.name}</div>
                              <div className="break-all text-slate-500">{scope.namespace}</div>
                              <div className="mt-1 text-slate-400">
                                Returned {scope.returned ?? 0}
                                {scope.added !== undefined && `, added ${scope.added}`}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="grid gap-2 md:grid-cols-2">
                      <div>
                        <div className="mb-1 font-medium text-slate-300">Recalled</div>
                        <div className="break-all">
                          {trace.recalled_memory_ids.length > 0
                            ? trace.recalled_memory_ids.join(', ')
                            : 'None'}
                        </div>
                      </div>
                      <div>
                        <div className="mb-1 font-medium text-slate-300">Written</div>
                        <div className="break-all">
                          {trace.written_memory_ids.length > 0
                            ? trace.written_memory_ids.join(', ')
                            : 'None'}
                        </div>
                      </div>
                    </div>
                  </div>
                </details>
              </div>
            ))}
          </div>
        ) : (
          <div className="card text-center text-slate-500">
            {loadingTraces ? 'Loading traces...' : 'No invocation memory traces yet.'}
          </div>
        )}
      </div>
    </div>
  )
}
