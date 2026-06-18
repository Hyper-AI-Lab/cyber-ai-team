import { beforeEach, describe, expect, it, vi } from 'vitest'

import ApiClient, { resolveApiBase, resolveWsBase } from './api'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function textResponse(body: string, status: number) {
  return new Response(body, { status })
}

describe('ApiClient', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('uses same-origin API and WebSocket bases when loopback URLs are baked into a hosted UI', () => {
    const hostedLocation = {
      hostname: 'cyberteam.hyperailab.com',
      origin: 'https://cyberteam.hyperailab.com',
    }

    const apiBase = resolveApiBase('http://localhost:8000', hostedLocation)
    const wsBase = resolveWsBase('ws://localhost:8000', apiBase, hostedLocation)

    expect(apiBase).toBe('https://cyberteam.hyperailab.com')
    expect(wsBase).toBe('wss://cyberteam.hyperailab.com')
  })

  it('keeps loopback API URLs for local browser development', () => {
    const localLocation = {
      hostname: 'localhost',
      origin: 'http://localhost:3001',
    }

    expect(resolveApiBase('http://localhost:8000', localLocation)).toBe(
      'http://localhost:8000',
    )
  })

  it('stores login tokens and sends the bearer token on later requests', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        access_token: 'access-1',
        refresh_token: 'refresh-1',
        token_type: 'bearer',
      }))
      .mockResolvedValueOnce(jsonResponse({ total_agents: 3 }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    await client.login('owner@example.com', 'correct-password')
    await client.getKpis()

    expect(window.localStorage.getItem('cyberteam_access_token')).toBe('access-1')
    expect(window.localStorage.getItem('cyberteam_refresh_token')).toBe('refresh-1')
    expect(fetchMock.mock.calls[1][1]?.headers.Authorization).toBe('Bearer access-1')
  })

  it('refreshes once after an unauthorized response and retries the original request', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(textResponse('expired', 401))
      .mockResolvedValueOnce(jsonResponse({
        access_token: 'access-2',
        refresh_token: 'refresh-2',
      }))
      .mockResolvedValueOnce(jsonResponse({ total_agents: 5 }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')
    client.setTokens('access-1', 'refresh-1')

    const result = await client.getKpis()

    expect(result).toEqual({ total_agents: 5 })
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[0][1]?.headers.Authorization).toBe('Bearer access-1')
    expect(fetchMock.mock.calls[1][0]).toBe('http://api.test/api/auth/refresh')
    expect(fetchMock.mock.calls[2][1]?.headers.Authorization).toBe('Bearer access-2')
    expect(window.localStorage.getItem('cyberteam_access_token')).toBe('access-2')
    expect(window.localStorage.getItem('cyberteam_refresh_token')).toBe('refresh-2')
  })

  it('clears stored tokens when refresh fails', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(textResponse('expired', 401))
      .mockResolvedValueOnce(textResponse('invalid refresh', 401))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')
    client.setTokens('access-1', 'refresh-1')

    await expect(client.getKpis()).rejects.toThrow('API error 401: expired')

    expect(client.isAuthenticated()).toBe(false)
    expect(window.localStorage.getItem('cyberteam_access_token')).toBeNull()
    expect(window.localStorage.getItem('cyberteam_refresh_token')).toBeNull()
  })

  it('builds a chat websocket URL with an encoded one-time ticket', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ticket: 'ticket with spaces' }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')
    const expectedWsBase = (process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000')
      .replace(/\/ws\/?$/, '')
      .replace(/\/$/, '')

    client.setTokens('token with spaces')

    await expect(client.getChatWebSocketUrl())
      .resolves.toBe(`${expectedWsBase}/api/chat/ws?ticket=ticket%20with%20spaces`)
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/auth/ws-ticket')
    expect(fetchMock.mock.calls[0][1]?.headers.Authorization).toBe(
      'Bearer token with spaces',
    )
  })

  it('fetches integration status through the authenticated API client', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ communications: [] }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.getIntegrationStatus()

    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/integrations/status')
    expect(fetchMock.mock.calls[0][1]?.headers.Authorization).toBe('Bearer access-1')
  })

  it('manages ERPNext company context through the authenticated API client', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'synced', snapshot: { id: 'ctx-1' } }))
      .mockResolvedValueOnce(jsonResponse({ snapshot: { id: 'ctx-1' } }))
      .mockResolvedValueOnce(jsonResponse([{ id: 'run-1' }]))
      .mockResolvedValueOnce(jsonResponse({ status: 'unchanged', drift: { detected: false } }))
      .mockResolvedValueOnce(jsonResponse({ enabled: true }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.syncCompanyContext({ dry_run: true, apply_low_risk: false })
    await client.getCompanyContext()
    await client.listCompanyContextSyncRuns(5)
    await client.scanCompanyContextDrift({ dry_run: true })
    await client.getCompanyContextDriftStatus()

    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://api.test/api/operations/company-context/sync',
    )
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      dry_run: true,
      apply_low_risk: false,
      run_planner: true,
      source: 'erpnext',
    })
    expect(fetchMock.mock.calls[1][0]).toBe(
      'http://api.test/api/operations/company-context',
    )
    expect(fetchMock.mock.calls[2][0]).toBe(
      'http://api.test/api/operations/company-context/sync-runs?limit=5',
    )
    expect(fetchMock.mock.calls[3][0]).toBe(
      'http://api.test/api/operations/company-context/drift-scan',
    )
    expect(fetchMock.mock.calls[3][1]?.method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[3][1]?.body as string)).toEqual({
      dry_run: true,
      apply_low_risk: true,
      run_planner: true,
    })
    expect(fetchMock.mock.calls[4][0]).toBe(
      'http://api.test/api/operations/company-context/drift-status',
    )
  })

  it('validates an integration provider through the authenticated API client', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'blocked' }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.validateIntegration('smtp')

    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/integrations/validate')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
    expect(fetchMock.mock.calls[0][1]?.headers.Authorization).toBe('Bearer access-1')
    expect(fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ provider: 'smtp' }))
  })

  it('manages inbound email through the authenticated API client', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: 'msg-1' }]))
      .mockResolvedValueOnce(jsonResponse({ id: 'msg-1', text_body: 'hello' }))
      .mockResolvedValueOnce(jsonResponse({ status: 'ready', stored: 1 }))
      .mockResolvedValueOnce(jsonResponse({ id: 'msg-1', status: 'triaged' }))
      .mockResolvedValueOnce(jsonResponse({
        message: { id: 'msg-1', status: 'triaged' },
        approval: { approval_id: 'approval-1' },
      }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.listInboundEmail('new', 25)
    await client.getInboundEmail('msg-1')
    await client.pollInboundEmail()
    await client.updateInboundEmailStatus('msg-1', 'triaged')
    await client.triageInboundEmailAndPrepareReply('msg-1')

    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://api.test/api/comms/inbound-email?limit=25&status=new',
    )
    expect(fetchMock.mock.calls[1][0]).toBe('http://api.test/api/comms/inbound-email/msg-1')
    expect(fetchMock.mock.calls[2][0]).toBe('http://api.test/api/comms/inbound-email/poll')
    expect(fetchMock.mock.calls[2][1]?.method).toBe('POST')
    expect(fetchMock.mock.calls[3][0]).toBe(
      'http://api.test/api/comms/inbound-email/msg-1/status',
    )
    expect(fetchMock.mock.calls[3][1]?.method).toBe('PATCH')
    expect(fetchMock.mock.calls[3][1]?.body).toBe(JSON.stringify({ status: 'triaged' }))
    expect(fetchMock.mock.calls[4][0]).toBe(
      'http://api.test/api/comms/inbound-email/msg-1/triage-reply',
    )
    expect(fetchMock.mock.calls[4][1]?.method).toBe('POST')
    expect(fetchMock.mock.calls[4][1]?.body).toBe(JSON.stringify({}))
  })

  it('lists memory traces with optional filters', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: 'trace-1' }]))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.listMemoryTraces({
      agentId: 'agent 1',
      sourceType: 'tool_execution',
      conversationId: 'conversation-1',
      workflowRunId: 'workflow-1',
      toolName: 'memory_recall',
      memoryNamespace: 'company:acme:ops',
      coverage: 'read',
      limit: 25,
    })

    const url = new URL(fetchMock.mock.calls[0][0] as string)
    expect(url.pathname).toBe('/api/memory/traces')
    expect(url.searchParams.get('agent_id')).toBe('agent 1')
    expect(url.searchParams.get('source_type')).toBe('tool_execution')
    expect(url.searchParams.get('conversation_id')).toBe('conversation-1')
    expect(url.searchParams.get('workflow_run_id')).toBe('workflow-1')
    expect(url.searchParams.get('tool_name')).toBe('memory_recall')
    expect(url.searchParams.get('memory_namespace')).toBe('company:acme:ops')
    expect(url.searchParams.get('coverage')).toBe('read')
    expect(url.searchParams.get('limit')).toBe('25')
    expect(fetchMock.mock.calls[0][1]?.headers.Authorization).toBe('Bearer access-1')
  })

  it('manages memory steward reviews and findings', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ findings_created: 1 }))
      .mockResolvedValueOnce(jsonResponse({ actions_applied: 1 }))
      .mockResolvedValueOnce(jsonResponse([{ id: 'finding-1' }]))
      .mockResolvedValueOnce(jsonResponse({ id: 'finding-1', status: 'resolved' }))
      .mockResolvedValueOnce(jsonResponse({
        action: { action_type: 'seed_memory' },
        finding: { id: 'finding-1', status: 'acknowledged' },
      }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.runMemorySteward()
    await client.planMemorySteward(true, true, 25)
    await client.listMemoryStewardFindings('open', 25)
    await client.resolveMemoryStewardFinding('finding-1', 'resolved', 'Seeded memory')
    await client.executeMemoryStewardAction('finding-1', 'seed_memory')

    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/memory/steward/run')
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
    expect(fetchMock.mock.calls[1][0]).toBe(
      'http://api.test/api/memory/steward/plan',
    )
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({
      apply_safe_actions: true,
      request_approvals: true,
      limit: 25,
    })
    expect(fetchMock.mock.calls[2][0]).toBe(
      'http://api.test/api/memory/steward/findings?status=open&limit=25',
    )
    expect(fetchMock.mock.calls[3][0]).toBe(
      'http://api.test/api/memory/steward/findings/finding-1/resolve',
    )
    expect(JSON.parse(fetchMock.mock.calls[3][1]?.body as string)).toEqual({
      status: 'resolved',
      note: 'Seeded memory',
    })
    expect(fetchMock.mock.calls[4][0]).toBe(
      'http://api.test/api/memory/steward/findings/finding-1/actions',
    )
    expect(JSON.parse(fetchMock.mock.calls[4][1]?.body as string)).toEqual({
      action_type: 'seed_memory',
      params: {},
    })
  })

  it('lists and runs autonomous operation cycles', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ event_type: 'autonomous_operations.cycle' }]))
      .mockResolvedValueOnce(jsonResponse({ cycle_id: 'auto_cycle_1', status: 'completed' }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.listAutonomousCycles(10)
    await client.runAutonomousCycle({
      run_memory_steward: true,
      run_supervisor_review: false,
      memory_remediation_limit: 25,
    })

    const auditUrl = new URL(fetchMock.mock.calls[0][0] as string)
    expect(auditUrl.pathname).toBe('/api/audit/events')
    expect(auditUrl.searchParams.get('limit')).toBe('10')
    expect(auditUrl.searchParams.get('event_type')).toBe(
      'autonomous_operations.cycle',
    )
    expect(fetchMock.mock.calls[1][0]).toBe(
      'http://api.test/api/operations/autonomous-cycle',
    )
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({
      run_memory_steward: true,
      run_supervisor_review: false,
      memory_remediation_limit: 25,
    })
  })

  it('manages autonomous plans through the operations API', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: 'plan-1', status: 'planned' }]))
      .mockResolvedValueOnce(jsonResponse({ id: 'plan-1', status: 'planned' }))
      .mockResolvedValueOnce(jsonResponse({ plans_created: 1 }))
      .mockResolvedValueOnce(jsonResponse({
        counts: { cadences: 1, due: 1 },
        items: [{ cadence_id: 'cadence-1' }],
      }))
      .mockResolvedValueOnce(jsonResponse({ plans_created: 1 }))
      .mockResolvedValueOnce(jsonResponse({ status: 'completed' }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.listAutonomousPlans({
      status: 'planned',
      source_type: 'role_gap',
      limit: 10,
    })
    await client.getAutonomousPlan('plan-1')
    await client.scanAutonomousPlans({
      include_role_gaps: true,
      include_memory_findings: false,
      include_operating_cadence: true,
      auto_execute: true,
      limit: 10,
    })
    await client.getOperatingCadenceStatus({ company_namespace: 'company:acme', limit: 25 })
    await client.scanOperatingCadences({
      company_namespace: 'company:acme',
      auto_execute: false,
      limit: 25,
    })
    await client.executeAutonomousPlan('plan-1')

    const listUrl = new URL(fetchMock.mock.calls[0][0] as string)
    expect(listUrl.pathname).toBe('/api/operations/plans')
    expect(listUrl.searchParams.get('status')).toBe('planned')
    expect(listUrl.searchParams.get('source_type')).toBe('role_gap')
    expect(listUrl.searchParams.get('limit')).toBe('10')
    expect(fetchMock.mock.calls[1][0]).toBe('http://api.test/api/operations/plans/plan-1')
    expect(fetchMock.mock.calls[2][0]).toBe('http://api.test/api/operations/plans/scan')
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({
      include_role_gaps: true,
      include_memory_findings: false,
      include_operating_cadence: true,
      auto_execute: true,
      limit: 10,
    })
    const cadenceStatusUrl = new URL(fetchMock.mock.calls[3][0] as string)
    expect(cadenceStatusUrl.pathname).toBe('/api/operations/operating-cadence/status')
    expect(cadenceStatusUrl.searchParams.get('company_namespace')).toBe('company:acme')
    expect(cadenceStatusUrl.searchParams.get('limit')).toBe('25')
    expect(fetchMock.mock.calls[4][0]).toBe(
      'http://api.test/api/operations/operating-cadence/scan',
    )
    expect(JSON.parse(fetchMock.mock.calls[4][1]?.body as string)).toEqual({
      auto_execute: false,
      limit: 25,
      company_namespace: 'company:acme',
    })
    expect(fetchMock.mock.calls[5][0]).toBe(
      'http://api.test/api/operations/plans/plan-1/execute',
    )
  })

  it('fetches operations readiness, decision timeline, and GDPR workflows', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ready' }))
      .mockResolvedValueOnce(jsonResponse([{ id: 'timeline-1' }]))
      .mockResolvedValueOnce(jsonResponse({ dry_run: true }))
      .mockResolvedValueOnce(jsonResponse({ subject: 'person@example.com' }))
      .mockResolvedValueOnce(jsonResponse({ audit_events_retained: true }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.getOperationsReadiness()
    await client.getDecisionTimeline(25)
    await client.runRetentionCleanup(true)
    await client.exportSubjectData('person@example.com')
    await client.deleteSubjectData('person@example.com', true)

    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/operations/readiness')
    expect(fetchMock.mock.calls[1][0]).toBe(
      'http://api.test/api/operations/decision-timeline?limit=25',
    )
    expect(fetchMock.mock.calls[2][0]).toBe(
      'http://api.test/api/operations/retention/cleanup',
    )
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string)).toEqual({
      dry_run: true,
    })
    expect(fetchMock.mock.calls[3][0]).toBe(
      'http://api.test/api/operations/gdpr/subjects/person%40example.com/export',
    )
    expect(fetchMock.mock.calls[4][0]).toBe(
      'http://api.test/api/operations/gdpr/subjects/person%40example.com/delete',
    )
    expect(JSON.parse(fetchMock.mock.calls[4][1]?.body as string)).toEqual({
      dry_run: true,
      audit_preserving: true,
    })
  })

  it('manages role gaps through the authenticated API client', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: 'gap-1' }]))
      .mockResolvedValueOnce(jsonResponse({
        items: [{ gap_id: 'gap-1', recommended_action: 'regenerate_approval' }],
        groups: [],
        counts: { total: 1 },
      }))
      .mockResolvedValueOnce(jsonResponse({
        counts: { cadences: 1 },
        cadences: [{ agent_id: 'agent-1' }],
      }))
      .mockResolvedValueOnce(jsonResponse({ id: 'gap-1', status: 'open' }))
      .mockResolvedValueOnce(jsonResponse({
        role_gaps_reviewed: 1,
        role_gaps_proposed: ['gap-1'],
        workflow_failure_gaps: [],
      }))
      .mockResolvedValueOnce(jsonResponse({ id: 'gap-1', status: 'proposed' }))
      .mockResolvedValueOnce(jsonResponse({ id: 'gap-1', status: 'resolved' }))
      .mockResolvedValueOnce(jsonResponse({ approval_id: 'approval-2' }))
      .mockResolvedValueOnce(jsonResponse({ id: 'gap-1', status: 'dismissed' }))
      .mockResolvedValueOnce(jsonResponse({ succeeded_count: 1, failed_count: 0 }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.listRoleGaps('open')
    await client.getRoleGapSummary({
      status: 'open,proposed',
      source_type: 'company_context_snapshot',
      limit: 25,
    })
    await client.getRoleOperatingCadence('company:acme')
    await client.reportRoleGap({ title: 'Gap', description: 'Blocked work' })
    await client.runSupervisorRoleGapReview()
    await client.proposeRoleGap('gap-1', { name: 'Acme' })
    await client.applyRoleGap('gap-1', { name: 'Acme' }, 'approval-1')
    await client.regenerateRoleGapApproval('gap-1', { name: 'Acme' })
    await client.resolveRoleGap('gap-1', 'dismissed', 'Not needed')
    await client.batchRoleGapAction({
      gap_ids: ['gap-1'],
      action: 'regenerate_approval',
      company_profile: { name: 'Acme' },
      approval_ids: { 'gap-1': 'approval-1' },
      note: 'Batch request',
    })

    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/roles/role-gaps?status=open')
    expect(fetchMock.mock.calls[1][0]).toBe(
      'http://api.test/api/roles/role-gaps/summary?status=open%2Cproposed&limit=25&source_type=company_context_snapshot',
    )
    expect(fetchMock.mock.calls[2][0]).toBe(
      'http://api.test/api/roles/operating-cadence?company_namespace=company%3Aacme',
    )
    expect(fetchMock.mock.calls[3][0]).toBe('http://api.test/api/roles/role-gaps')
    expect(fetchMock.mock.calls[4][0]).toBe(
      'http://api.test/api/roles/role-gaps/supervisor-review',
    )
    expect(fetchMock.mock.calls[5][0]).toBe(
      'http://api.test/api/roles/role-gaps/gap-1/proposal',
    )
    expect(fetchMock.mock.calls[6][0]).toBe(
      'http://api.test/api/roles/role-gaps/gap-1/apply',
    )
    expect(JSON.parse(fetchMock.mock.calls[6][1]?.body as string)).toEqual({
      company_profile: { name: 'Acme' },
      approval_id: 'approval-1',
    })
    expect(fetchMock.mock.calls[7][0]).toBe(
      'http://api.test/api/roles/role-gaps/gap-1/approval/regenerate',
    )
    expect(JSON.parse(fetchMock.mock.calls[7][1]?.body as string)).toEqual({
      company_profile: { name: 'Acme' },
    })
    expect(fetchMock.mock.calls[8][0]).toBe(
      'http://api.test/api/roles/role-gaps/gap-1/resolve',
    )
    expect(fetchMock.mock.calls[9][0]).toBe(
      'http://api.test/api/roles/role-gaps/batch',
    )
    expect(JSON.parse(fetchMock.mock.calls[9][1]?.body as string)).toEqual({
      gap_ids: ['gap-1'],
      action: 'regenerate_approval',
      company_profile: { name: 'Acme' },
      approval_ids: { 'gap-1': 'approval-1' },
      note: 'Batch request',
    })
  })
})
