'use client'

import { useState } from 'react'
import { api } from '@/lib/api'
import { Brain, Search, Clock, Tag } from 'lucide-react'

export default function MemoryView() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [searching, setSearching] = useState(false)
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)
  const [agentMemory, setAgentMemory] = useState<any[]>([])

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

  const handleAgentMemory = async (agentId: string) => {
    try {
      const res = await api.getAgentMemory(agentId)
      setAgentMemory(res)
      setSelectedAgent(agentId)
    } catch (e: any) {
      console.error('Agent memory fetch failed:', e)
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold">Memory</h2>
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
    </div>
  )
}
