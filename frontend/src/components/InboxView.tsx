'use client'

import { useCallback, useEffect, useState } from 'react'
import { Archive, CheckCircle2, Inbox, MailOpen, RefreshCw, ShieldAlert } from 'lucide-react'

import { api } from '@/lib/api'

type InboundEmail = {
  id: string
  from_address: string
  to_addresses: string[]
  cc_addresses: string[]
  subject: string
  snippet: string
  status: 'new' | 'triaged' | 'archived' | 'spam' | 'closed' | string
  received_at: string | null
  first_seen_at: string
  text_body?: string
  html_body?: string | null
  metadata?: Record<string, any>
}

const statuses = [
  { value: '', label: 'All' },
  { value: 'new', label: 'New' },
  { value: 'triaged', label: 'Triaged' },
  { value: 'closed', label: 'Closed' },
  { value: 'archived', label: 'Archived' },
  { value: 'spam', label: 'Spam' },
]

const statusStyle: Record<string, string> = {
  new: 'badge-info',
  triaged: 'badge-warning',
  closed: 'badge-success',
  archived: 'badge-info',
  spam: 'badge-danger',
}

function formatDate(value?: string | null) {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

export default function InboxView() {
  const [messages, setMessages] = useState<InboundEmail[]>([])
  const [selected, setSelected] = useState<InboundEmail | null>(null)
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(true)
  const [polling, setPolling] = useState(false)
  const [updating, setUpdating] = useState<string | null>(null)
  const [pollResult, setPollResult] = useState<any | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadMessages = useCallback(async (nextStatus = status) => {
    setLoading(true)
    setError(null)
    try {
      const result = await api.listInboundEmail(nextStatus || undefined, 50)
      setMessages(result)
    } catch (e: any) {
      setError(e.message || 'Failed to load inbound email')
    } finally {
      setLoading(false)
    }
  }, [status])

  useEffect(() => {
    loadMessages()
  }, [loadMessages])

  const changeStatusFilter = (nextStatus: string) => {
    setStatus(nextStatus)
  }

  const pollMailbox = async () => {
    setPolling(true)
    setError(null)
    try {
      const result = await api.pollInboundEmail()
      setPollResult(result)
      await loadMessages()
    } catch (e: any) {
      setError(e.message || 'Mailbox poll failed')
    } finally {
      setPolling(false)
    }
  }

  const openMessage = async (message: InboundEmail) => {
    setError(null)
    try {
      setSelected(await api.getInboundEmail(message.id))
    } catch (e: any) {
      setError(e.message || 'Failed to open message')
    }
  }

  const updateStatus = async (messageId: string, nextStatus: string) => {
    setUpdating(nextStatus)
    setError(null)
    try {
      const updated = await api.updateInboundEmailStatus(messageId, nextStatus)
      setSelected(updated)
      await loadMessages()
    } catch (e: any) {
      setError(e.message || 'Failed to update message status')
    } finally {
      setUpdating(null)
    }
  }

  const newCount = messages.filter((message) => message.status === 'new').length

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <Inbox className="h-7 w-7 text-blue-400" />
            <h2 className="text-2xl font-bold">Inbox</h2>
          </div>
          <p className="mt-1 text-slate-400">
            Inbound business email captured from the configured IMAP mailbox
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => loadMessages()}
            className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-100"
            title="Refresh inbox"
            aria-label="Refresh inbox"
          >
            <RefreshCw className={`h-5 w-5 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={pollMailbox}
            disabled={polling}
            className="btn-primary inline-flex items-center gap-2"
          >
            <MailOpen className={`h-4 w-4 ${polling ? 'animate-pulse' : ''}`} />
            Poll Mailbox
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {pollResult && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 px-4 py-3 text-sm text-slate-300">
          Mailbox poll is{' '}
          <span className={pollResult.status === 'ready' ? 'text-green-300' : 'text-yellow-300'}>
            {pollResult.status}
          </span>
          : fetched {pollResult.fetched ?? 0}, stored {pollResult.stored ?? 0}, duplicates{' '}
          {pollResult.duplicates ?? 0}
          {pollResult.errors?.length ? `, errors: ${pollResult.errors.join('; ')}` : ''}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="card">
          <p className="text-sm text-slate-400">Visible Messages</p>
          <p className="mt-2 text-2xl font-semibold">{messages.length}</p>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">New</p>
          <p className="mt-2 text-2xl font-semibold">{newCount}</p>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Selected</p>
          <p className="mt-2 truncate text-lg font-semibold">{selected?.subject || '-'}</p>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {statuses.map((item) => (
          <button
            key={item.value || 'all'}
            type="button"
            onClick={() => changeStatusFilter(item.value)}
            className={`rounded-lg border px-3 py-2 text-sm transition-colors ${
              status === item.value
                ? 'border-blue-500 bg-blue-600/20 text-blue-200'
                : 'border-slate-700 text-slate-300 hover:border-slate-500'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(360px,0.85fr)_1.15fr]">
        <div className="card min-h-[360px] overflow-hidden">
          {loading && messages.length === 0 ? (
            <div className="flex h-64 items-center justify-center">
              <div className="h-10 w-10 animate-spin rounded-full border-b-2 border-blue-500" />
            </div>
          ) : messages.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center text-center text-slate-500">
              <ShieldAlert className="mb-4 h-10 w-10 opacity-60" />
              <p>No inbound messages match this view</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-800">
              {messages.map((message) => (
                <button
                  key={message.id}
                  type="button"
                  onClick={() => openMessage(message)}
                  className={`block w-full px-1 py-4 text-left transition-colors hover:bg-slate-800/60 ${
                    selected?.id === message.id ? 'bg-slate-800/80' : ''
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-100">
                        {message.subject || '(no subject)'}
                      </p>
                      <p className="mt-1 truncate text-xs text-slate-400">
                        {message.from_address || 'unknown sender'}
                      </p>
                    </div>
                    <span className={statusStyle[message.status] || 'badge-info'}>
                      {message.status}
                    </span>
                  </div>
                  <p className="mt-2 line-clamp-2 text-sm text-slate-400">{message.snippet}</p>
                  <p className="mt-2 text-xs text-slate-500">{formatDate(message.received_at)}</p>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="card min-h-[360px]">
          {!selected ? (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center text-center text-slate-500">
              <MailOpen className="mb-4 h-10 w-10 opacity-60" />
              <p>Select a message to inspect the inbound record</p>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="break-words text-xl font-semibold text-slate-100">
                    {selected.subject || '(no subject)'}
                  </h3>
                  <p className="mt-1 text-sm text-slate-400">
                    From {selected.from_address || 'unknown sender'}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    To {selected.to_addresses?.join(', ') || '-'} · {formatDate(selected.received_at)}
                  </p>
                </div>
                <span className={statusStyle[selected.status] || 'badge-info'}>
                  {selected.status}
                </span>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => updateStatus(selected.id, 'triaged')}
                  disabled={updating === 'triaged'}
                  className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-amber-500"
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Triaged
                </button>
                <button
                  type="button"
                  onClick={() => updateStatus(selected.id, 'closed')}
                  disabled={updating === 'closed'}
                  className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-green-500"
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Closed
                </button>
                <button
                  type="button"
                  onClick={() => updateStatus(selected.id, 'archived')}
                  disabled={updating === 'archived'}
                  className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-blue-500"
                >
                  <Archive className="h-4 w-4" />
                  Archive
                </button>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4">
                <pre className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-200">
                  {selected.text_body || selected.snippet || 'No readable text body captured.'}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
