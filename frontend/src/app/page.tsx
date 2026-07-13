'use client'

import { useState, useEffect, useCallback, useRef, type FormEvent } from 'react'
import { api } from '@/lib/api'
import Sidebar from '@/components/Sidebar'
import DashboardView from '@/components/DashboardView'
import AgentsView from '@/components/AgentsView'
import MemoryView from '@/components/MemoryView'
import WorkflowsView from '@/components/WorkflowsView'
import ChatView from '@/components/ChatView'
import ApprovalsView from '@/components/ApprovalsView'
import AuditView from '@/components/AuditView'
import IntegrationsView from '@/components/IntegrationsView'
import OperationsView from '@/components/OperationsView'
import InboxView from '@/components/InboxView'

export type ViewName =
  | 'dashboard'
  | 'agents'
  | 'memory'
  | 'workflows'
  | 'operations'
  | 'inbox'
  | 'chat'
  | 'approvals'
  | 'audit'
  | 'integrations'

export default function Home() {
  const [activeView, setActiveView] = useState<ViewName>('dashboard')
  const [kpis, setKpis] = useState<any>(null)
  const [agents, setAgents] = useState<any[]>([])
  const [approvals, setApprovals] = useState<any[]>([])
  const [autonomousCycles, setAutonomousCycles] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [authError, setAuthError] = useState<string | null>(null)
  const [loggingIn, setLoggingIn] = useState(false)
  const hasLoadedDataRef = useRef(false)

  const handleLogout = useCallback(() => {
    api.clearTokens()
    setAuthenticated(false)
    setKpis(null)
    setAgents([])
    setApprovals([])
    setAutonomousCycles([])
    setActiveView('dashboard')
    setAuthError(null)
    hasLoadedDataRef.current = false
  }, [])

  const refreshData = useCallback(async () => {
    if (!api.isAuthenticated()) {
      setAuthChecked(true)
      setLoading(false)
      return
    }
    const isInitialLoad = !hasLoadedDataRef.current
    if (isInitialLoad) {
      setLoading(true)
    }
    try {
      const [k, a, ap, cycles] = await Promise.all([
        api.getKpis(),
        api.listAgents(),
        api.getApprovalQueue(),
        api.listAutonomousCycles(10),
      ])
      setKpis(k)
      setAgents(a)
      setApprovals(ap)
      setAutonomousCycles(cycles)
      setAuthenticated(true)
      hasLoadedDataRef.current = true
    } catch (e: any) {
      if (`${e.message}`.includes('401')) {
        handleLogout()
      } else {
        console.error('Failed to refresh data:', e)
      }
    } finally {
      setAuthChecked(true)
      if (isInitialLoad) {
        setLoading(false)
      }
    }
  }, [handleLogout])

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setLoggingIn(true)
    setAuthError(null)
    try {
      await api.login(email, password)
      setAuthenticated(true)
      await refreshData()
    } catch (e: any) {
      setAuthError(e.message || 'Login failed')
      api.clearTokens()
      setAuthenticated(false)
      hasLoadedDataRef.current = false
    } finally {
      setLoggingIn(false)
    }
  }

  useEffect(() => {
    if (api.isAuthenticated()) {
      refreshData()
    } else {
      setAuthChecked(true)
    }
  }, [refreshData])

  useEffect(() => {
    if (!authenticated) return
    const interval = setInterval(refreshData, 30000)
    return () => clearInterval(interval)
  }, [authenticated, refreshData])

  if (!authChecked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950">
        <div className="h-12 w-12 animate-spin rounded-full border-b-2 border-blue-500" />
      </div>
    )
  }

  if (!authenticated) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6">
        <form
          onSubmit={handleLogin}
          className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-6 shadow-xl"
        >
          <div className="mb-6">
            <h1 className="text-xl font-bold text-white">Cyber-Team</h1>
            <p className="mt-1 text-sm text-slate-400">Owner Console</p>
          </div>
          <label className="mb-2 block text-sm font-medium text-slate-300" htmlFor="owner-email">
            Email
          </label>
          <input
            id="owner-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mb-4 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-white outline-none focus:border-blue-500"
            autoComplete="username"
            required
          />
          <label className="mb-2 block text-sm font-medium text-slate-300" htmlFor="owner-password">
            Password
          </label>
          <input
            id="owner-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-white outline-none focus:border-blue-500"
            autoComplete="current-password"
            required
          />
          {authError && (
            <div className="mt-4 rounded-lg border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-300">
              {authError}
            </div>
          )}
          <button type="submit" disabled={loggingIn} className="btn-primary mt-6 w-full">
            {loggingIn ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
      </main>
    )
  }

  const renderView = () => {
    switch (activeView) {
      case 'dashboard':
        return (
          <DashboardView
            kpis={kpis}
            agents={agents}
            approvals={approvals}
            autonomousCycles={autonomousCycles}
            loading={loading}
          />
        )
      case 'agents':
        return (
          <AgentsView
            agents={agents}
            onRefresh={refreshData}
            onNavigate={setActiveView}
          />
        )
      case 'memory':
        return <MemoryView />
      case 'workflows':
        return <WorkflowsView />
      case 'operations':
        return (
          <OperationsView
            cycles={autonomousCycles}
            onRefresh={refreshData}
            onNavigate={setActiveView}
          />
        )
      case 'inbox':
        return <InboxView />
      case 'chat':
        return <ChatView agents={agents} />
      case 'approvals':
        return (
          <ApprovalsView
            approvals={approvals}
            onRefresh={refreshData}
            onNavigate={setActiveView}
          />
        )
      case 'audit':
        return <AuditView />
      case 'integrations':
        return <IntegrationsView />
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar
        activeView={activeView}
        onViewChange={setActiveView}
        approvalCount={approvals.length}
        onLogout={handleLogout}
      />
      <main className="flex-1 overflow-y-auto p-8">
        {renderView()}
      </main>
    </div>
  )
}
