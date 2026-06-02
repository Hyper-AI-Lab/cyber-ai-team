'use client'

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Activity, Brain, Clock, RefreshCw, Search, Tag } from 'lucide-react'

export default function MemoryView() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [searching, setSearching] = useState(false)
  const [traces, setTraces] = useState<any[]>([])
  const [loadingTraces, setLoadingTraces] = useState(false)

  useEffect(() => {
    void loadTraces()
  }, [])

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

  const loadTraces = async () => {
    setLoadingTraces(true)
    try {
      const res = await api.listMemoryTraces(undefined, 25)
      setTraces(res)
    } catch (e: any) {
      console.error('Memory trace fetch failed:', e)
    } finally {
      setLoadingTraces(false)
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
