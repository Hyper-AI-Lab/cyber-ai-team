'use client'

import { useState, useRef, useEffect } from 'react'
import { api } from '@/lib/api'
import { MessageSquare, Send, Bot } from 'lucide-react'

interface ChatViewProps {
  agents: any[]
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  agentName?: string
}

export default function ChatView({ agents }: ChatViewProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    if (!input.trim()) return
    const userMsg: Message = { role: 'user', content: input }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setSending(true)

    try {
      const res = await api.sendChat(selectedAgent, input, conversationId || undefined)
      if (!conversationId) setConversationId(res.conversation_id)
      const assistantMsg: Message = {
        role: 'assistant',
        content: res.message,
        agentName: res.agent_name,
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${e.message}`, agentName: 'System' },
      ])
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold">Chat</h2>
          <p className="text-slate-400 mt-1">Interact with your AI team</p>
        </div>
        <select
          value={selectedAgent || ''}
          onChange={(e) => {
            setSelectedAgent(e.target.value || null)
            setMessages([])
            setConversationId(null)
          }}
          className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm"
        >
          <option value="">Supervisor (All)</option>
          {agents.map((a: any) => (
            <option key={a.id} value={a.id}>
              {a.role_name}
            </option>
          ))}
        </select>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-4 mb-4">
        {messages.length === 0 && (
          <div className="text-center text-slate-500 py-16">
            <MessageSquare className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>Select an agent and start chatting</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[70%] rounded-2xl px-4 py-3 ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-800 text-slate-200'
              }`}
            >
              {msg.agentName && (
                <div className="flex items-center gap-1 text-xs text-slate-400 mb-1">
                  <Bot className="w-3 h-3" />
                  {msg.agentName}
                </div>
              )}
              <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="flex gap-3">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
          className="flex-1 bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 text-white"
          placeholder="Type a message..."
          disabled={sending}
        />
        <button
          onClick={handleSend}
          disabled={sending || !input.trim()}
          className="btn-primary rounded-xl px-6"
        >
          <Send className="w-5 h-5" />
        </button>
      </div>
    </div>
  )
}
