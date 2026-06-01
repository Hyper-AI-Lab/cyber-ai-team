import { beforeEach, describe, expect, it, vi } from 'vitest'

import ApiClient from './api'

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

  it('manages role gaps through the authenticated API client', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: 'gap-1' }]))
      .mockResolvedValueOnce(jsonResponse({ id: 'gap-1', status: 'open' }))
      .mockResolvedValueOnce(jsonResponse({
        role_gaps_reviewed: 1,
        role_gaps_proposed: ['gap-1'],
        workflow_failure_gaps: [],
      }))
      .mockResolvedValueOnce(jsonResponse({ id: 'gap-1', status: 'proposed' }))
      .mockResolvedValueOnce(jsonResponse({ id: 'gap-1', status: 'resolved' }))
      .mockResolvedValueOnce(jsonResponse({ id: 'gap-1', status: 'dismissed' }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient('http://api.test')

    client.setTokens('access-1')
    await client.listRoleGaps('open')
    await client.reportRoleGap({ title: 'Gap', description: 'Blocked work' })
    await client.runSupervisorRoleGapReview()
    await client.proposeRoleGap('gap-1', { name: 'Acme' })
    await client.applyRoleGap('gap-1', { name: 'Acme' }, 'approval-1')
    await client.resolveRoleGap('gap-1', 'dismissed', 'Not needed')

    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/roles/role-gaps?status=open')
    expect(fetchMock.mock.calls[1][0]).toBe('http://api.test/api/roles/role-gaps')
    expect(fetchMock.mock.calls[2][0]).toBe(
      'http://api.test/api/roles/role-gaps/supervisor-review',
    )
    expect(fetchMock.mock.calls[3][0]).toBe(
      'http://api.test/api/roles/role-gaps/gap-1/proposal',
    )
    expect(fetchMock.mock.calls[4][0]).toBe(
      'http://api.test/api/roles/role-gaps/gap-1/apply',
    )
    expect(JSON.parse(fetchMock.mock.calls[4][1]?.body as string)).toEqual({
      company_profile: { name: 'Acme' },
      approval_id: 'approval-1',
    })
    expect(fetchMock.mock.calls[5][0]).toBe(
      'http://api.test/api/roles/role-gaps/gap-1/resolve',
    )
  })
})
