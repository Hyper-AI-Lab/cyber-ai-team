type BrowserLocationLike = {
  hostname: string;
  origin: string;
}

function currentBrowserLocation(): BrowserLocationLike | null {
  return typeof window !== 'undefined' ? window.location : null;
}

function stripTrailingSlash(value: string) {
  return value.replace(/\/$/, '');
}

function isLoopbackHost(hostname: string) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1';
}

function isLoopbackUrl(value?: string) {
  if (!value) {
    return false;
  }
  try {
    return isLoopbackHost(new URL(value).hostname);
  } catch {
    return false;
  }
}

export function resolveApiBase(
  configuredApiBase: string | undefined = process.env.NEXT_PUBLIC_API_URL,
  location: BrowserLocationLike | null = currentBrowserLocation(),
) {
  const configured = configuredApiBase ? stripTrailingSlash(configuredApiBase) : '';
  if (configured && !(location && isLoopbackUrl(configured) && !isLoopbackHost(location.hostname))) {
    return configured;
  }
  if (!location) {
    return 'http://localhost:8000';
  }
  if (isLoopbackHost(location.hostname)) {
    return `http://${location.hostname}:8000`;
  }
  return stripTrailingSlash(location.origin);
}

const API_BASE = resolveApiBase();
export function resolveWsBase(
  configuredWsBase: string | undefined = process.env.NEXT_PUBLIC_WS_URL,
  apiBase: string = API_BASE,
  location: BrowserLocationLike | null = currentBrowserLocation(),
) {
  const configured = configuredWsBase ? stripTrailingSlash(configuredWsBase) : '';
  if (configured && !(location && isLoopbackUrl(configured) && !isLoopbackHost(location.hostname))) {
    return configured;
  }
  return apiBase.replace(/^http/, 'ws');
}

const WS_BASE = resolveWsBase();
const CHAT_WS_BASE = WS_BASE.replace(/\/ws\/?$/, '').replace(/\/$/, '');

class ApiClient {
  private baseUrl: string;
  private accessToken: string | null = null;
  private refreshToken: string | null = null;

  constructor(baseUrl: string = API_BASE) {
    this.baseUrl = baseUrl;
    if (typeof window !== 'undefined') {
      this.accessToken = window.localStorage.getItem('cyberteam_access_token');
      this.refreshToken = window.localStorage.getItem('cyberteam_refresh_token');
    }
  }

  private async request(
    path: string,
    options: RequestInit = {},
    retryOnUnauthorized: boolean = true
  ): Promise<any> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string> | undefined),
    };
    if (this.accessToken) {
      headers.Authorization = `Bearer ${this.accessToken}`;
    }
    const res = await fetch(url, {
      ...options,
      headers,
    });
    if (
      res.status === 401
      && retryOnUnauthorized
      && this.refreshToken
      && path !== '/api/auth/login'
    ) {
      try {
        await this.refreshSession();
        return this.request(path, options, false);
      } catch {
        this.clearTokens();
      }
    }
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`API error ${res.status}: ${text}`);
    }
    if (res.status === 204) {
      return null;
    }
    const contentType = res.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      return res.text();
    }
    return res.json();
  }

  // Auth
  async login(email: string, password: string) {
    const response = await this.request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    this.setTokens(response.access_token, response.refresh_token);
    return response;
  }

  setTokens(accessToken: string, refreshToken?: string) {
    this.accessToken = accessToken;
    this.refreshToken = refreshToken || null;
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('cyberteam_access_token', accessToken);
      if (refreshToken) {
        window.localStorage.setItem('cyberteam_refresh_token', refreshToken);
      } else {
        window.localStorage.removeItem('cyberteam_refresh_token');
      }
    }
  }

  clearTokens() {
    this.accessToken = null;
    this.refreshToken = null;
    if (typeof window !== 'undefined') {
      window.localStorage.removeItem('cyberteam_access_token');
      window.localStorage.removeItem('cyberteam_refresh_token');
    }
  }

  isAuthenticated() {
    return Boolean(this.accessToken);
  }

  getAccessToken() {
    return this.accessToken;
  }

  async getChatWebSocketUrl() {
    if (!this.accessToken) {
      return `${CHAT_WS_BASE}/api/chat/ws`;
    }
    const response = await this.request('/api/auth/ws-ticket', { method: 'POST' });
    return `${CHAT_WS_BASE}/api/chat/ws?ticket=${encodeURIComponent(response.ticket)}`;
  }

  async refreshSession() {
    if (!this.refreshToken) {
      throw new Error('No refresh token available');
    }
    const response = await this.request('/api/auth/refresh', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: this.refreshToken }),
    }, false);
    this.setTokens(response.access_token, response.refresh_token);
    return response;
  }

  // Dashboard
  async getKpis() {
    return this.request('/api/dashboard/kpis');
  }

  async getAgentStatus() {
    return this.request('/api/dashboard/agent-status');
  }

  async getApprovalQueue(status?: string) {
    const params = status ? `?status=${status}` : '';
    return this.request(`/api/dashboard/approval-queue${params}`);
  }

  async approveAction(id: string, note: string = '') {
    return this.request(`/api/dashboard/approval/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    });
  }

  async rejectAction(id: string, note: string = '') {
    return this.request(`/api/dashboard/approval/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    });
  }

  async getRecentActivity(limit: number = 50) {
    return this.request(`/api/dashboard/recent-activity?limit=${limit}`);
  }

  async getIntegrationStatus() {
    return this.request('/api/integrations/status');
  }

  async validateIntegration(provider: string = 'smtp') {
    return this.request('/api/integrations/validate', {
      method: 'POST',
      body: JSON.stringify({ provider }),
    });
  }

  // Agents
  async listAgents() {
    return this.request('/api/agents/');
  }

  async getAgent(id: string) {
    return this.request(`/api/agents/${id}`);
  }

  async invokeAgent(id: string, task: string) {
    return this.request(`/api/agents/${id}/invoke`, {
      method: 'POST',
      body: JSON.stringify({ task }),
    });
  }

  // Roles
  async listRoleCatalog() {
    return this.request('/api/roles/catalog');
  }

  async instantiateRole(manifestId: string, overrides: Record<string, string> = {}) {
    return this.request(`/api/roles/instantiate/${manifestId}`, {
      method: 'POST',
      body: JSON.stringify(overrides),
    });
  }

  async runCompanyBuilder(profile: Record<string, any>) {
    return this.request('/api/roles/company-builder', {
      method: 'POST',
      body: JSON.stringify(profile),
    });
  }

  async provisionRole(data: Record<string, any>) {
    return this.request('/api/roles/provision', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async listRoleGaps(status?: string) {
    const params = status ? `?status=${encodeURIComponent(status)}` : '';
    return this.request(`/api/roles/role-gaps${params}`);
  }

  async reportRoleGap(data: Record<string, any>) {
    return this.request('/api/roles/role-gaps', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async runSupervisorRoleGapReview() {
    return this.request('/api/roles/role-gaps/supervisor-review', {
      method: 'POST',
    });
  }

  async proposeRoleGap(gapId: string, companyProfile: Record<string, any> = {}) {
    return this.request(`/api/roles/role-gaps/${gapId}/proposal`, {
      method: 'POST',
      body: JSON.stringify({ company_profile: companyProfile }),
    });
  }

  async applyRoleGap(
    gapId: string,
    companyProfile: Record<string, any> = {},
    approvalId?: string
  ) {
    return this.request(`/api/roles/role-gaps/${gapId}/apply`, {
      method: 'POST',
      body: JSON.stringify({
        company_profile: companyProfile,
        ...(approvalId ? { approval_id: approvalId } : {}),
      }),
    });
  }

  async resolveRoleGap(gapId: string, status: string = 'dismissed', note: string = '') {
    return this.request(`/api/roles/role-gaps/${gapId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ status, note }),
    });
  }

  // Memory
  async recallMemory(query: string, namespace?: string, limit: number = 10) {
    return this.request('/api/memory/recall', {
      method: 'POST',
      body: JSON.stringify({ query, namespace, limit }),
    });
  }

  async getAgentMemory(agentId: string) {
    return this.request(`/api/memory/agent/${agentId}`);
  }

  async listMemoryTraces(
    filters: {
      agentId?: string;
      sourceType?: string;
      conversationId?: string;
      workflowRunId?: string;
      toolName?: string;
      memoryNamespace?: string;
      coverage?: string;
      limit?: number;
    } = {}
  ) {
    const params = new URLSearchParams({ limit: String(filters.limit ?? 50) });
    if (filters.agentId) {
      params.set('agent_id', filters.agentId);
    }
    if (filters.sourceType) {
      params.set('source_type', filters.sourceType);
    }
    if (filters.conversationId) {
      params.set('conversation_id', filters.conversationId);
    }
    if (filters.workflowRunId) {
      params.set('workflow_run_id', filters.workflowRunId);
    }
    if (filters.toolName) {
      params.set('tool_name', filters.toolName);
    }
    if (filters.memoryNamespace) {
      params.set('memory_namespace', filters.memoryNamespace);
    }
    if (filters.coverage) {
      params.set('coverage', filters.coverage);
    }
    return this.request(`/api/memory/traces?${params.toString()}`);
  }

  async runMemorySteward() {
    return this.request('/api/memory/steward/run', { method: 'POST' });
  }

  async planMemorySteward(
    applySafeActions?: boolean,
    requestApprovals?: boolean,
    limit: number = 100
  ) {
    return this.request('/api/memory/steward/plan', {
      method: 'POST',
      body: JSON.stringify({
        ...(applySafeActions === undefined ? {} : { apply_safe_actions: applySafeActions }),
        ...(requestApprovals === undefined ? {} : { request_approvals: requestApprovals }),
        limit,
      }),
    });
  }

  async listMemoryStewardFindings(status: string = 'open', limit: number = 50) {
    const params = new URLSearchParams({ status, limit: String(limit) });
    return this.request(`/api/memory/steward/findings?${params.toString()}`);
  }

  async resolveMemoryStewardFinding(
    findingId: string,
    status: string = 'resolved',
    note: string = ''
  ) {
    return this.request(`/api/memory/steward/findings/${findingId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ status, note }),
    });
  }

  async executeMemoryStewardAction(
    findingId: string,
    actionType: 'seed_memory' | 'report_role_gap',
    params: Record<string, any> = {}
  ) {
    return this.request(`/api/memory/steward/findings/${findingId}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action_type: actionType, params }),
    });
  }

  // Workflows
  async listWorkflows() {
    return this.request('/api/workflows/');
  }

  async getWorkflow(id: string) {
    return this.request(`/api/workflows/${id}`);
  }

  async runWorkflow(id: string, inputData: Record<string, any> = {}) {
    return this.request(`/api/workflows/${id}/run`, {
      method: 'POST',
      body: JSON.stringify(inputData),
    });
  }

  async listWorkflowRuns(workflowId: string) {
    return this.request(`/api/workflows/${workflowId}/runs`);
  }

  async getWorkflowRun(runId: string) {
    return this.request(`/api/workflows/runs/${runId}`);
  }

  async resumeWorkflowRun(runId: string) {
    return this.request(`/api/workflows/runs/${runId}/resume`, {
      method: 'POST',
    });
  }

  // Chat
  async sendChat(agentId: string | null, message: string, conversationId?: string) {
    return this.request('/api/chat/send', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, message, conversation_id: conversationId }),
    });
  }

  // Communications
  async getCommLogs(channel?: string, limit: number = 50) {
    const params = channel ? `?channel=${channel}&limit=${limit}` : `?limit=${limit}`;
    return this.request(`/api/comms/logs${params}`);
  }

  async listInboundEmail(status?: string, limit: number = 50) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (status) {
      params.set('status', status);
    }
    return this.request(`/api/comms/inbound-email?${params.toString()}`);
  }

  async getInboundEmail(messageId: string) {
    return this.request(`/api/comms/inbound-email/${messageId}`);
  }

  async pollInboundEmail() {
    return this.request('/api/comms/inbound-email/poll', {
      method: 'POST',
      body: JSON.stringify({}),
    });
  }

  async updateInboundEmailStatus(messageId: string, status: string) {
    return this.request(`/api/comms/inbound-email/${messageId}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    });
  }

  async triageInboundEmailAndPrepareReply(messageId: string) {
    return this.request(`/api/comms/inbound-email/${messageId}/triage-reply`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
  }

  // Tools
  async listTools(category?: string) {
    const params = category ? `?category=${category}` : '';
    return this.request(`/api/tools/${params}`);
  }

  async executeTool(toolName: string, params: Record<string, any> = {}) {
    return this.request('/api/tools/execute', {
      method: 'POST',
      body: JSON.stringify({ tool_name: toolName, params }),
    });
  }

  async listAuditEvents(
    limit: number = 100,
    filters: {
      eventType?: string;
      actor?: string;
      resourceType?: string;
      resourceId?: string;
    } = {}
  ) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (filters.eventType) {
      params.set('event_type', filters.eventType);
    }
    if (filters.actor) {
      params.set('actor', filters.actor);
    }
    if (filters.resourceType) {
      params.set('resource_type', filters.resourceType);
    }
    if (filters.resourceId) {
      params.set('resource_id', filters.resourceId);
    }
    return this.request(`/api/audit/events?${params.toString()}`);
  }

  async listAutonomousCycles(limit: number = 20) {
    return this.listAuditEvents(limit, { eventType: 'autonomous_operations.cycle' });
  }

  async runAutonomousCycle(options: Record<string, any> = {}) {
    return this.request('/api/operations/autonomous-cycle', {
      method: 'POST',
      body: JSON.stringify(options),
    });
  }

  async getOperationsReadiness() {
    return this.request('/api/operations/readiness');
  }

  async getDecisionTimeline(limit: number = 50) {
    return this.request(`/api/operations/decision-timeline?limit=${limit}`);
  }

  async runRetentionCleanup(dryRun: boolean = true) {
    return this.request('/api/operations/retention/cleanup', {
      method: 'POST',
      body: JSON.stringify({ dry_run: dryRun }),
    });
  }

  async exportSubjectData(subject: string) {
    return this.request(`/api/operations/gdpr/subjects/${encodeURIComponent(subject)}/export`);
  }

  async deleteSubjectData(subject: string, dryRun: boolean = true) {
    return this.request(`/api/operations/gdpr/subjects/${encodeURIComponent(subject)}/delete`, {
      method: 'POST',
      body: JSON.stringify({ dry_run: dryRun, audit_preserving: true }),
    });
  }

  async listAutonomousPlans(
    filters: {
      status?: string;
      source_type?: string;
      limit?: number;
    } = {}
  ) {
    const params = new URLSearchParams({ limit: String(filters.limit ?? 50) });
    if (filters.status) {
      params.set('status', filters.status);
    }
    if (filters.source_type) {
      params.set('source_type', filters.source_type);
    }
    return this.request(`/api/operations/plans?${params.toString()}`);
  }

  async getAutonomousPlan(planId: string) {
    return this.request(`/api/operations/plans/${planId}`);
  }

  async scanAutonomousPlans(options: Record<string, any> = {}) {
    return this.request('/api/operations/plans/scan', {
      method: 'POST',
      body: JSON.stringify(options),
    });
  }

  async executeAutonomousPlan(planId: string) {
    return this.request(`/api/operations/plans/${planId}/execute`, {
      method: 'POST',
    });
  }
}

export const api = new ApiClient();
export default ApiClient;
