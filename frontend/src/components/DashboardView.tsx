'use client'

import { Bot, ShieldCheck, GitBranch, AlertTriangle } from 'lucide-react'

interface DashboardViewProps {
  kpis: any
  agents: any[]
  approvals: any[]
  loading: boolean
}

export default function DashboardView({ kpis, agents, approvals, loading }: DashboardViewProps) {
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
