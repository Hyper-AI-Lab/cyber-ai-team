'use client'

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, RefreshCw, XCircle } from 'lucide-react'

import { api } from '@/lib/api'

type IntegrationItem = {
  channel?: string
  provider: string
  configured: boolean
  mode: 'live' | 'simulated' | 'disabled' | 'profile_only' | 'configuration_required' | string
  implementation?: string
  detail?: string
  required?: boolean
  optional_disabled?: boolean
  blocking?: boolean
  site_url?: string
  api_url?: string
  company_context?: {
    status?: string
    last_sync_at?: string | null
    stale?: boolean
    detail?: string
  }
  circuit?: {
    state: 'open' | 'closed' | string
    failures: number
    opened_until: number | null
  }
}

type IntegrationStatus = {
  environment: string
  communications: IntegrationItem[]
  erpnext?: IntegrationItem | null
  llm?: IntegrationItem | null
  required_providers?: string[]
  optional_disabled?: IntegrationItem[]
  simulation_enabled: boolean
  last_validation_result?: IntegrationValidationResult
}

type IntegrationValidationResult = {
  status: 'ready' | 'blocked' | 'failed' | string
  checked_at: string | null
  provider: string
  results?: Array<{
    channel: string
    provider: string
    status: string
    detail?: string
    missing?: string[]
    network_check?: string
  }>
}

const modeStyles: Record<string, string> = {
  live: 'badge-success',
  simulated: 'badge-warning',
  disabled: 'badge-danger',
  profile_only: 'badge-info',
  configuration_required: 'badge-warning',
}

function ModeIcon({ mode }: { mode: string }) {
  if (mode === 'live') return <CheckCircle2 className="h-5 w-5 text-green-400" />
  if (mode === 'simulated') return <AlertTriangle className="h-5 w-5 text-yellow-400" />
  if (mode === 'disabled' || mode === 'configuration_required') {
    return <XCircle className="h-5 w-5 text-red-400" />
  }
  return <CircleDashed className="h-5 w-5 text-blue-400" />
}

export default function IntegrationsView() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [validatingProvider, setValidatingProvider] = useState<string | null>(null)
  const [validationResult, setValidationResult] = useState<IntegrationValidationResult | null>(null)

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

  const validateProvider = async (provider: string) => {
    setValidatingProvider(provider)
    setError(null)
    try {
      const result = await api.validateIntegration(provider)
      setValidationResult(result)
      await loadStatus()
    } catch (e: any) {
      setError(e.message || 'Failed to validate integration')
    } finally {
      setValidatingProvider(null)
    }
  }

  const latestValidation = validationResult || status?.last_validation_result
  const integrationItems = [
    ...(status?.communications || []),
    ...(status?.erpnext ? [status.erpnext] : []),
    ...(status?.llm ? [status.llm] : []),
  ]
  const liveCount = integrationItems.filter((item) => item.mode === 'live').length
  const blockerCount = integrationItems.filter((item) => item.blocking).length

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

      {latestValidation?.checked_at && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 px-4 py-3 text-sm text-slate-300">
          <span className="font-medium text-slate-100">
            {latestValidation.provider}
          </span>{' '}
          validation is{' '}
          <span className={latestValidation.status === 'ready' ? 'text-green-300' : 'text-yellow-300'}>
            {latestValidation.status}
          </span>
          {latestValidation.results?.[0]?.detail ? `: ${latestValidation.results[0].detail}` : ''}
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
              <p className="mt-2 text-xl font-semibold">{liveCount}</p>
            </div>
            <div className="card">
              <p className="text-sm text-slate-400">Required Blockers</p>
              <p className="mt-2 text-xl font-semibold">{blockerCount}</p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {integrationItems.map((item) => (
              <div key={`${item.channel || 'integration'}-${item.provider}`} className="card">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <ModeIcon mode={item.mode} />
                    <div>
                      <h3 className="font-semibold capitalize">{item.channel || item.provider}</h3>
                      <p className="mt-1 text-sm text-slate-400">{item.detail}</p>
                      {item.site_url && (
                        <p className="mt-1 text-xs text-slate-500">{item.site_url}</p>
                      )}
                      {item.company_context && (
                        <p className="mt-1 text-xs text-slate-500">
                          context {item.company_context.status || 'unknown'} ·{' '}
                          {item.company_context.last_sync_at
                            ? new Date(item.company_context.last_sync_at).toLocaleString()
                            : item.company_context.detail || 'not synced'}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    {item.required ? (
                      <span className="badge-info">required</span>
                    ) : (
                      <span className="rounded-full bg-slate-700 px-2.5 py-1 text-xs font-medium text-slate-300">
                        optional
                      </span>
                    )}
                    <span className={modeStyles[item.mode] || 'badge-info'}>{item.mode}</span>
                  </div>
                </div>
                <div className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
                  <div>
                    <p className="text-slate-500">Provider</p>
                    <p className="text-slate-200">{item.provider}</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Implementation</p>
                    <p className="text-slate-200">
                      {item.implementation || (item.provider === 'erpnext' ? 'implemented' : 'unknown')}
                    </p>
                  </div>
                  <div>
                    <p className="text-slate-500">{item.api_url ? 'API' : 'Circuit'}</p>
                    <p
                      className={
                        item.circuit?.state === 'open' ? 'text-red-300' : 'text-slate-200'
                      }
                    >
                      {item.api_url || item.circuit?.state || 'unknown'}
                      {item.circuit && item.circuit.failures > 0
                        ? ` (${item.circuit.failures} failures)`
                        : ''}
                    </p>
                  </div>
                </div>
                <div className="mt-4 flex justify-end">
                  <button
                    type="button"
                    onClick={() => validateProvider(item.provider)}
                    disabled={validatingProvider === item.provider}
                    className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-blue-500 hover:text-blue-200 disabled:cursor-wait disabled:opacity-60"
                    title={`Validate ${item.provider}`}
                  >
                    <RefreshCw
                      className={`h-4 w-4 ${validatingProvider === item.provider ? 'animate-spin' : ''}`}
                    />
                    Validate
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
