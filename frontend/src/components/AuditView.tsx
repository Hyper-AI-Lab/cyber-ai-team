'use client'

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { ShieldCheck } from 'lucide-react'

export default function AuditView() {
  const [events, setEvents] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.listAuditEvents(100)
      .then(setEvents)
      .catch(() => setEvents([]))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold">Audit Trail</h2>
        <p className="text-slate-400 mt-1">Structured governance events for authentication, approvals, tools, and workflows.</p>
      </div>

      <div className="card overflow-hidden">
        {loading ? (
          <p className="text-slate-400">Loading audit events...</p>
        ) : events.length === 0 ? (
          <div className="text-center text-slate-500 py-12">
            <ShieldCheck className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>No audit events recorded yet</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-400 border-b border-slate-700">
                  <th className="py-3 pr-4">Time</th>
                  <th className="py-3 pr-4">Event</th>
                  <th className="py-3 pr-4">Actor</th>
                  <th className="py-3 pr-4">Resource</th>
                  <th className="py-3 pr-4">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id} className="border-b border-slate-800 text-slate-200">
                    <td className="py-3 pr-4 whitespace-nowrap text-slate-400">{new Date(event.created_at).toLocaleString()}</td>
                    <td className="py-3 pr-4 font-mono text-xs">{event.event_type}</td>
                    <td className="py-3 pr-4">{event.actor}</td>
                    <td className="py-3 pr-4 text-slate-400">{event.resource_type || '-'}{event.resource_id ? `:${event.resource_id}` : ''}</td>
                    <td className="py-3 pr-4">
                      <span className={`px-2 py-1 rounded-full text-xs ${event.outcome === 'success' ? 'bg-green-600/20 text-green-400' : event.outcome === 'failed' ? 'bg-red-600/20 text-red-400' : 'bg-amber-600/20 text-amber-400'}`}>
                        {event.outcome}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
