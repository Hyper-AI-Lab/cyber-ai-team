'use client'

import { Activity, AlertTriangle, Bot, GitBranch, ShieldCheck } from 'lucide-react'

interface DashboardViewProps {
  kpis: any
  agents: any[]
  approvals: any[]
  autonomousCycles: any[]
  loading: boolean
}

export default function DashboardView({
  kpis,
  agents,
  approvals,
  autonomousCycles,
  loading,
}: DashboardViewProps) {
  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500" />
      </div>
    )
  }

  const stats = [
    {
      label: 'Active Agents',
      value: kpis?.total_agents ?? agents.length,
      icon: Bot,
      color: 'text-blue-400',
      bg: 'bg-blue-900/30',
    },
    {
      label: 'Pending Approvals',
      value: kpis?.pending_approvals ?? approvals.length,
      icon: ShieldCheck,
      color: 'text-yellow-400',
      bg: 'bg-yellow-900/30',
    },
    {
      label: 'Running Workflows',
      value: kpis?.running_workflows ?? 0,
      icon: GitBranch,
      color: 'text-green-400',
      bg: 'bg-green-900/30',
    },
    {
      label: 'Total Workflows',
      value: kpis?.total_workflows ?? 0,
      icon: AlertTriangle,
      color: 'text-purple-400',
      bg: 'bg-purple-900/30',
    },
  ]
  const latestCycle = autonomousCycles[0]
  const latestCycleMetadata = latestCycle?.metadata || {}
  const latestCycleCounts = latestCycleMetadata.counts || {}
  const latestDecisions = latestCycleMetadata.decisions || []

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold">Dashboard</h2>
        <p className="text-slate-400 mt-1">Overview of your AI company operations</p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {stats.map((stat) => (
          <div key={stat.label} className="card">
            <div className="flex items-center gap-4">
              <div className={`p-3 rounded-lg ${stat.bg}`}>
                <stat.icon className={`w-6 h-6 ${stat.color}`} />
              </div>
              <div>
                <p className="text-sm text-slate-400">{stat.label}</p>
                <p className="text-2xl font-bold">{stat.value}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Agent Status Grid */}
      <div>
        <h3 className="text-lg font-semibold mb-4">Autonomous Operations</h3>
        <div className="card">
          {latestCycle ? (
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex min-w-0 items-start gap-3">
                <div className="rounded-lg bg-blue-900/30 p-3">
                  <Activity className="h-6 w-6 text-blue-300" />
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-semibold">Latest cycle</p>
                    <span
                      className={`rounded-full px-2 py-1 text-xs ${
                        latestCycle.outcome === 'completed'
                          ? 'bg-green-600/20 text-green-300'
                          : latestCycle.outcome === 'failed'
                          ? 'bg-red-600/20 text-red-300'
                          : latestCycle.outcome === 'degraded'
                          ? 'bg-amber-600/20 text-amber-300'
                          : 'bg-slate-700 text-slate-300'
                      }`}
                    >
                      {latestCycle.outcome}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-slate-400">
                    {new Date(latestCycle.created_at).toLocaleString()}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-300">
                    {latestDecisions.slice(0, 3).map((decision: any, index: number) => (
                      <span
                        key={`${decision.step}-${decision.decision}-${index}`}
                        className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-1 text-blue-200"
                      >
                        {decision.decision}
                      </span>
                    ))}
                    {latestDecisions.length === 0 && (
                      <span className="text-slate-500">No decisions recorded</span>
                    )}
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3 text-center text-sm">
                <div>
                  <p className="text-lg font-bold text-white">
                    {(latestCycleCounts.memory_findings_created || 0)
                      + (latestCycleCounts.memory_findings_updated || 0)}
                  </p>
                  <p className="text-xs text-slate-400">Findings</p>
                </div>
                <div>
                  <p className="text-lg font-bold text-white">
                    {latestCycleCounts.memory_actions_applied || 0}
                  </p>
                  <p className="text-xs text-slate-400">Actions</p>
                </div>
                <div>
                  <p className="text-lg font-bold text-white">
                    {latestCycleCounts.role_gaps_proposed || 0}
                  </p>
                  <p className="text-xs text-slate-400">Roles</p>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center text-slate-500 py-8">
              No autonomous operation cycle has run yet.
            </div>
          )}
        </div>
      </div>

      {/* Agent Status Grid */}
      <div>
        <h3 className="text-lg font-semibold mb-4">Agent Status</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {agents.map((agent: any) => (
            <div key={agent.id} className="card">
              <div className="flex items-center justify-between mb-2">
                <h4 className="font-medium">{agent.role_name}</h4>
                <span
                  className={`badge ${
                    agent.status === 'active'
                      ? 'badge-success'
                      : agent.status === 'inactive'
                      ? 'badge-danger'
                      : 'badge-warning'
                  }`}
                >
                  {agent.status}
                </span>
              </div>
              <p className="text-sm text-slate-400">{agent.role_family}</p>
              <div className="mt-2 text-xs text-slate-500">
                Approval: {agent.approval_policy}
              </div>
            </div>
          ))}
          {agents.length === 0 && (
            <div className="card col-span-full text-center text-slate-500 py-8">
              No agents yet. Use the Company Builder to set up your team.
            </div>
          )}
        </div>
      </div>

      {/* Pending Approvals */}
      {approvals.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold mb-4">Pending Approvals</h3>
          <div className="space-y-3">
            {approvals.slice(0, 5).map((approval: any) => (
              <div key={approval.id} className="card flex items-center justify-between">
                <div>
                  <p className="font-medium">{approval.action_type}</p>
                  <p className="text-sm text-slate-400">{approval.action_description}</p>
                  <p className="text-xs text-slate-500 mt-1">Agent: {approval.agent_id}</p>
                </div>
                <span className="badge-warning">Pending</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
