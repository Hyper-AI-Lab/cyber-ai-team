export interface ApprovalToolReplay {
  toolName: string
  params: Record<string, unknown>
}

export function managedApprovalExecution(approval: any) {
  const managed = approval?.action_payload?.managed_execution
  if (managed?.kind !== 'action_policy_live_canary') return null
  return managed
}

export function approvalToolReplay(approval: any): ApprovalToolReplay | null {
  if (managedApprovalExecution(approval)) return null
  const payload = approval?.action_payload || {}
  const replayBody = payload.replay_instructions?.body || {}
  const toolName = payload.tool_name || replayBody.tool_name
  const params = payload.params || replayBody.params
  if (!toolName || !params) return null
  return { toolName, params }
}
