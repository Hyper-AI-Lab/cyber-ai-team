'use client'

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, RefreshCw, XCircle } from 'lucide-react'

import { api } from '@/lib/api'

type IntegrationItem = {
  channel: string
  provider: string
  configured: boolean
  mode: 'live' | 'simulated' | 'disabled' | 'profile_only' | string
  implementation?: string
  detail?: string
  circuit?: {
    state: 'open' | 'closed' | string
    failures: number
    opened_until: number | null
  }
}

type IntegrationStatus = {
  environment: string
  communications: IntegrationItem[]
  simulation_enabled: boolean
}

const modeStyles: Record<string, string> = {
  live: 'badge-success',
  simulated: 'badge-warning',
  disabled: 'badge-danger',
  profile_only: 'badge-info',
}

function ModeIcon({ mode }: { mode: string }) {
  if (mode === 'live') return <CheckCircle2 className="h-5 w-5 text-green-400" />
  if (mode === 'simulated') return <AlertTriangle className="h-5 w-5 text-yellow-400" />
  if (mode === 'disabled') return <XCircle className="h-5 w-5 text-red-400" />
  return <CircleDashed className="h-5 w-5 text-blue-400" />
}

export default function IntegrationsView() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadStatus = async () => {
    setLoading(true)
    setError(null)
    try {
      setStatus(await api.getIntegrationStatus())
    } catch (e: any) {
      setError(e.message || 'Failed to load integration status')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadStatus()
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold">Integrations</h2>
          <p className="mt-1 text-slate-400">Runtime connection status for external channels</p>
        </div>
        <button
          type="button"
          onClick={loadStatus}
          className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-100"
          title="Refresh integration status"
          aria-label="Refresh integration status"
        >
          <RefreshCw className={`h-5 w-5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading && !status ? (
        <div className="flex h-64 items-center justify-center">
          <div className="h-12 w-12 animate-spin rounded-full border-b-2 border-blue-500" />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="card">
              <p className="text-sm text-slate-400">Environment</p>
              <p className="mt-2 text-xl font-semibold">{status?.environment}</p>
            </div>
            <div className="card">
              <p className="text-sm text-slate-400">Simulation</p>
              <p className="mt-2 text-xl font-semibold">
                {status?.simulation_enabled ? 'Enabled' : 'Disabled'}
              </p>
            </div>
            <div className="card">
              <p className="text-sm text-slate-400">Live Channels</p>
              <p className="mt-2 text-xl font-semibold">
                {status?.communications.filter((item) => item.mode === 'live').length ?? 0}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {status?.communications.map((item) => (
              <div key={`${item.channel}-${item.provider}`} className="card">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <ModeIcon mode={item.mode} />
                    <div>
                      <h3 className="font-semibold capitalize">{item.channel}</h3>
                      <p className="mt-1 text-sm text-slate-400">{item.detail}</p>
                    </div>
                  </div>
                  <span className={modeStyles[item.mode] || 'badge-info'}>{item.mode}</span>
                </div>
                <div className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
                  <div>
                    <p className="text-slate-500">Provider</p>
                    <p className="text-slate-200">{item.provider}</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Implementation</p>
                    <p className="text-slate-200">{item.implementation || 'unknown'}</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Circuit</p>
                    <p
                      className={
                        item.circuit?.state === 'open' ? 'text-red-300' : 'text-slate-200'
                      }
                    >
                      {item.circuit?.state || 'unknown'}
                      {item.circuit && item.circuit.failures > 0
                        ? ` (${item.circuit.failures} failures)`
                        : ''}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
