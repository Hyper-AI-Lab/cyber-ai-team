'use client'

import { cn } from '@/lib/utils'
import type { ViewName } from '@/app/page'
import {
  LayoutDashboard,
  Bot,
  Brain,
  GitBranch,
  Activity,
  MessageSquare,
  ShieldCheck,
  ScrollText,
  Settings,
  Zap,
  LogOut,
  PlugZap,
} from 'lucide-react'

interface SidebarProps {
  activeView: ViewName
  onViewChange: (view: ViewName) => void
  approvalCount: number
  onLogout: () => void
}

const navItems: { view: ViewName; label: string; icon: React.ElementType }[] = [
  { view: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { view: 'agents', label: 'Agents', icon: Bot },
  { view: 'memory', label: 'Memory', icon: Brain },
  { view: 'workflows', label: 'Workflows', icon: GitBranch },
  { view: 'operations', label: 'Operations', icon: Activity },
  { view: 'chat', label: 'Chat', icon: MessageSquare },
  { view: 'approvals', label: 'Approvals', icon: ShieldCheck },
  { view: 'integrations', label: 'Integrations', icon: PlugZap },
  { view: 'audit', label: 'Audit Trail', icon: ScrollText },
]

const appVersion = process.env.NEXT_PUBLIC_APP_VERSION || '0.1.0'
const buildSha = process.env.NEXT_PUBLIC_BUILD_SHA || ''

export default function Sidebar({
  activeView,
  onViewChange,
  approvalCount,
  onLogout,
}: SidebarProps) {
  return (
    <aside className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col">
      <div className="p-6 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center">
            <Zap className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-white">Cyber-Team</h1>
            <p className="text-xs text-slate-400">AI Company OS</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 p-4 space-y-1">
        {navItems.map(({ view, label, icon: Icon }) => (
          <button
            key={view}
            onClick={() => onViewChange(view)}
            className={cn(
              'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
              activeView === view
                ? 'bg-blue-600/20 text-blue-400'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            )}
          >
            <Icon className="w-5 h-5" />
            {label}
            {view === 'approvals' && approvalCount > 0 && (
              <span className="ml-auto bg-red-600 text-white text-xs px-2 py-0.5 rounded-full">
                {approvalCount}
              </span>
            )}
          </button>
        ))}
      </nav>

      <div className="p-4 border-t border-slate-800">
        <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
          <div className="flex items-center gap-2">
            <Settings className="w-4 h-4" />
            <span>
              v{appVersion}{buildSha ? ` ${buildSha.slice(0, 7)}` : ''}
            </span>
          </div>
          <button
            onClick={onLogout}
            className="rounded-md p-1 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-200"
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </aside>
  )
}
