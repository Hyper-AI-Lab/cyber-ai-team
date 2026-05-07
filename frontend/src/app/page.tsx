'use client'

import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/api'
import Sidebar from '@/components/Sidebar'
import DashboardView from '@/components/DashboardView'
import AgentsView from '@/components/AgentsView'
import MemoryView from '@/components/MemoryView'
import WorkflowsView from '@/components/WorkflowsView'
import ChatView from '@/components/ChatView'
import ApprovalsView from '@/components/ApprovalsView'
import AuditView from '@/components/AuditView'

export type ViewName = 'dashboard' | 'agents' | 'memory' | 'workflows' | 'chat' | 'approvals' | 'audit'

export default function Home() {
  const [activeView, setActiveView] = useState<ViewName>('dashboard')
  const [kpis, setKpis] = useState<any>(null)
  const [agents, setAgents] = useState<any[]>([])
  const [approvals, setApprovals] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const refreshData = useCallback(async () => {
    try {
      const [k, a, ap] = await Promise.all([
        api.getKpis().catch(() => null),
        api.listAgents().catch(() => []),
        api.getApprovalQueue().catch(() => []),
      ])
      setKpis(k)
      setAgents(a)
      setApprovals(ap)
    } catch (e) {
      console.error('Failed to refresh data:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refreshData()
    const interval = setInterval(refreshData, 30000)
    return () => clearInterval(interval)
  }, [refreshData])

  const renderView = () => {
    switch (activeView) {
      case 'dashboard':
        return <DashboardView kpis={kpis} agents={agents} approvals={approvals} loading={loading} />
      case 'agents':
        return <AgentsView agents={agents} onRefresh={refreshData} />
      case 'memory':
        return <MemoryView />
      case 'workflows':
        return <WorkflowsView />
      case 'chat':
        return <ChatView agents={agents} />
      case 'approvals':
        return <ApprovalsView approvals={approvals} onRefresh={refreshData} />
      case 'audit':
        return <AuditView />
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar activeView={activeView} onViewChange={setActiveView} approvalCount={approvals.length} />
      <main className="flex-1 overflow-y-auto p-8">
        {renderView()}
      </main>
    </div>
  )
}
