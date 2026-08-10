import { describe, expect, it } from 'vitest'

import { approvalToolReplay, managedApprovalExecution } from './approvals'

describe('approval execution policy', () => {
  it('replays an ordinary approved tool request', () => {
    const approval = {
      action_payload: {
        tool_name: 'send_email',
        params: { to_address: 'owner@example.com' },
      },
    }

    expect(approvalToolReplay(approval)).toEqual({
      toolName: 'send_email',
      params: { to_address: 'owner@example.com' },
    })
  })

  it('leaves managed live-canary execution to its controlled endpoint', () => {
    const approval = {
      action_payload: {
        tool_name: 'task_create',
        params: { task_data: { subject: '[CYBERTEAM-CANARY] test' } },
        managed_execution: {
          kind: 'action_policy_live_canary',
          validation_case_id: 'actcase-1',
          approval_only_in_console: true,
        },
      },
    }

    expect(managedApprovalExecution(approval)?.validation_case_id).toBe('actcase-1')
    expect(approvalToolReplay(approval)).toBeNull()
  })
})
