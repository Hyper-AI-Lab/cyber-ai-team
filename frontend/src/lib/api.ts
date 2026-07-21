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

  async listAgentCapabilityGrants(id: string) {
    return this.request(`/api/agents/${id}/capability-grants`);
  }

  async revokeAgentCapabilityGrant(agentId: string, grantId: string, reason: string = '') {
    return this.request(`/api/agents/${agentId}/capability-grants/${grantId}/revoke`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
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

  async getRoleGapSummary(filters: {
    status?: string;
    source_type?: string;
    limit?: number;
  } = {}) {
    const params = new URLSearchParams({
      status: filters.status ?? 'open,proposed',
      limit: String(filters.limit ?? 200),
    });
    if (filters.source_type) {
      params.set('source_type', filters.source_type);
    }
    return this.request(`/api/roles/role-gaps/summary?${params.toString()}`);
  }

  async getRoleOperatingCadence(companyNamespace?: string) {
    const params = new URLSearchParams();
    if (companyNamespace) {
      params.set('company_namespace', companyNamespace);
    }
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/api/roles/operating-cadence${suffix}`);
  }

  async getTeamActivationCoverage() {
    return this.request('/api/roles/team-activation/coverage');
  }

  async getLatestTeamActivationRun() {
    return this.request('/api/roles/team-activation/latest');
  }

  async listTeamActivationRuns(limit: number = 20) {
    return this.request(`/api/roles/team-activation/runs?limit=${limit}`);
  }

  async runTeamActivation(options: {
    dryRun?: boolean;
    applySafeRoles?: boolean;
    requestHighRiskGrants?: boolean;
    sourceSnapshotId?: string;
  } = {}) {
    return this.request('/api/roles/team-activation/run', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: options.dryRun ?? false,
        apply_safe_roles: options.applySafeRoles ?? true,
        request_high_risk_grants: options.requestHighRiskGrants ?? true,
        ...(options.sourceSnapshotId ? { source_snapshot_id: options.sourceSnapshotId } : {}),
      }),
    });
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

  async regenerateRoleGapApproval(gapId: string, companyProfile: Record<string, any> = {}) {
    return this.request(`/api/roles/role-gaps/${gapId}/approval/regenerate`, {
      method: 'POST',
      body: JSON.stringify({ company_profile: companyProfile }),
    });
  }

  async resolveRoleGap(gapId: string, status: string = 'dismissed', note: string = '') {
    return this.request(`/api/roles/role-gaps/${gapId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ status, note }),
    });
  }

  async batchRoleGapAction(data: {
    gap_ids: string[];
    action: 'propose' | 'apply' | 'regenerate_approval' | 'defer' | 'dismiss';
    company_profile?: Record<string, any>;
    approval_ids?: Record<string, string>;
    note?: string;
  }) {
    return this.request('/api/roles/role-gaps/batch', {
      method: 'POST',
      body: JSON.stringify(data),
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

  async listWorkflowTemplates(filters: {
    status?: string;
    category?: string;
    isCore?: boolean;
  } = {}) {
    const params = new URLSearchParams();
    if (filters.status !== undefined) {
      params.set('status', filters.status);
    }
    if (filters.category) {
      params.set('category', filters.category);
    }
    if (filters.isCore !== undefined) {
      params.set('is_core', String(filters.isCore));
    }
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/api/workflows/templates${suffix}`);
  }

  async instantiateWorkflowTemplate(templateId: string) {
    return this.request(`/api/workflows/templates/${templateId}/instantiate`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
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

  async runGovernor(options: Record<string, any> = {}) {
    return this.request('/api/operations/governor/run', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: false,
        continue_on_error: true,
        ...options,
      }),
    });
  }

  async getGovernorLatest() {
    return this.request('/api/operations/governor/latest');
  }

  async listGovernorRuns(limit: number = 20) {
    return this.request(`/api/operations/governor/runs?limit=${limit}`);
  }

  async listGovernorDecisions(
    filters: {
      status?: string;
      decisionType?: string;
      limit?: number;
    } = {}
  ) {
    const params = new URLSearchParams({ limit: String(filters.limit ?? 100) });
    if (filters.status) {
      params.set('status', filters.status);
    }
    if (filters.decisionType) {
      params.set('decision_type', filters.decisionType);
    }
    return this.request(`/api/operations/governor/decisions?${params.toString()}`);
  }

  async listGovernorToolProposals(
    filters: {
      status?: string;
      limit?: number;
    } = {}
  ) {
    const params = new URLSearchParams({ limit: String(filters.limit ?? 100) });
    if (filters.status) {
      params.set('status', filters.status);
    }
    return this.request(`/api/operations/governor/tool-proposals?${params.toString()}`);
  }

  async requestGovernorToolProposalApproval(proposalId: string, note: string = '') {
    return this.request(`/api/operations/governor/tool-proposals/${proposalId}/approval`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    });
  }

  async getCompanyObjectives() {
    return this.request('/api/operations/company-objectives');
  }

  async updateCompanyObjectives(objectives: any[]) {
    return this.request('/api/operations/company-objectives', {
      method: 'PUT',
      body: JSON.stringify({ objectives }),
    });
  }

  async getExecutiveBrief() {
    return this.request('/api/operations/executive-brief');
  }

  async getExecutiveCadence() {
    return this.request('/api/operations/executive-cadence');
  }

  async getExecutiveBriefEmailStatus() {
    return this.request('/api/operations/executive-brief/email/status');
  }

  async sendExecutiveBriefEmail(options: { dryRun?: boolean; force?: boolean } = {}) {
    return this.request('/api/operations/executive-brief/email', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: options.dryRun ?? false,
        force: options.force ?? false,
      }),
    });
  }

  async getOperationGraph(filters: {
    nodeType?: string;
    sourceType?: string;
    riskLevel?: string;
    limit?: number;
  } = {}) {
    const params = new URLSearchParams({ limit: String(filters.limit ?? 100) });
    if (filters.nodeType) {
      params.set('node_type', filters.nodeType);
    }
    if (filters.sourceType) {
      params.set('source_type', filters.sourceType);
    }
    if (filters.riskLevel) {
      params.set('risk_level', filters.riskLevel);
    }
    return this.request(`/api/operations/operation-graph?${params.toString()}`);
  }

  async listGovernorReflections(limit: number = 50) {
    return this.request(`/api/operations/governor/reflections?limit=${limit}`);
  }

  async listGovernorBenchmarks() {
    return this.request('/api/operations/governor/benchmarks');
  }

  async createGovernorBenchmark(data: Record<string, any>) {
    return this.request('/api/operations/governor/benchmarks', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async listGovernorBenchmarkResults(limit: number = 100) {
    return this.request(`/api/operations/governor/benchmark-results?limit=${limit}`);
  }

  async getAutonomyPolicy() {
    return this.request('/api/operations/governor/autonomy-policy');
  }

  async updateAutonomyPolicy(data: Record<string, any>) {
    return this.request('/api/operations/governor/autonomy-policy', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async instructGovernor(options: {
    instruction: string;
    dryRun?: boolean;
    observerReview?: boolean;
  }) {
    return this.request('/api/operations/governor/instruct', {
      method: 'POST',
      body: JSON.stringify({
        instruction: options.instruction,
        dry_run: options.dryRun ?? false,
        observer_review: options.observerReview ?? true,
      }),
    });
  }

  async pauseGovernor(reason: string = '') {
    return this.request('/api/operations/governor/pause', {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  }

  async resumeGovernor(reason: string = '') {
    return this.request('/api/operations/governor/resume', {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  }

  async listObserverReviews(limit: number = 100) {
    return this.request(`/api/operations/observer/reviews?limit=${limit}`);
  }

  async runObserverReview(options: {
    runId?: string;
    ownerInstruction?: string;
  } = {}) {
    return this.request('/api/operations/observer/run', {
      method: 'POST',
      body: JSON.stringify({
        ...(options.runId ? { run_id: options.runId } : {}),
        owner_instruction: options.ownerInstruction ?? '',
      }),
    });
  }

  async listOutsourcingRequests(filters: {
    status?: string;
    limit?: number;
  } = {}) {
    const params = new URLSearchParams({ limit: String(filters.limit ?? 100) });
    if (filters.status) {
      params.set('status', filters.status);
    }
    return this.request(`/api/operations/outsourcing-requests?${params.toString()}`);
  }

  async createOutsourcingRequest(data: Record<string, any>) {
    return this.request('/api/operations/outsourcing-requests', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async resolveOutsourcingRequest(requestId: string, data: Record<string, any>) {
    return this.request(`/api/operations/outsourcing-requests/${requestId}/resolve`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async deduplicateOutsourcingRequests(options: { dryRun?: boolean } = {}) {
    return this.request('/api/operations/outsourcing-requests/deduplicate', {
      method: 'POST',
      body: JSON.stringify({ dry_run: options.dryRun ?? true }),
    });
  }

  async getResourcePolicy() {
    return this.request('/api/operations/resource-policy');
  }

  async getOperationsReadiness() {
    return this.request('/api/operations/readiness');
  }

  async getInteropSummary() {
    return this.request('/api/interop/summary');
  }

  async getMcpToolCatalog() {
    return this.request('/api/interop/mcp/tools');
  }

  async getA2aAgentCards() {
    return this.request('/api/interop/a2a/agent-cards');
  }

  async testAlertEmail(options: { dryRun?: boolean; note?: string } = {}) {
    return this.request('/api/operations/alerts/test-email', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: options.dryRun ?? false,
        note: options.note ?? '',
      }),
    });
  }

  async recordCredentialRotationEvidence(options: {
    scope?: string;
    secretNames?: string[];
    evidenceReference?: string;
    note?: string;
    rotatedAt?: string;
  } = {}) {
    return this.request('/api/operations/security/credential-rotation/evidence', {
      method: 'POST',
      body: JSON.stringify({
        scope: options.scope ?? 'staging',
        secret_names: options.secretNames ?? [],
        evidence_reference: options.evidenceReference ?? 'owner-console',
        note: options.note ?? '',
        ...(options.rotatedAt ? { rotated_at: options.rotatedAt } : {}),
      }),
    });
  }

  async getOwnerAttention(
    filters: {
      status?: string;
      limit?: number;
    } = {}
  ) {
    const params = new URLSearchParams({
      status: filters.status ?? 'active',
      limit: String(filters.limit ?? 50),
    });
    return this.request(`/api/operations/owner-attention?${params.toString()}`);
  }

  async notifyOwnerAttention(options: { dryRun?: boolean; limit?: number } = {}) {
    return this.request('/api/operations/owner-attention/notify', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: options.dryRun ?? false,
        limit: options.limit ?? 25,
      }),
    });
  }

  async getOwnerAttentionNotificationStatus() {
    return this.request('/api/operations/owner-attention/notifications/status');
  }

  async syncCompanyContext(options: Record<string, any> = {}) {
    return this.request('/api/operations/company-context/sync', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: false,
        apply_low_risk: true,
        run_planner: true,
        source: 'erpnext',
        ...options,
      }),
    });
  }

  async getCompanyContext() {
    return this.request('/api/operations/company-context');
  }

  async listCompanyContextSyncRuns(limit: number = 20) {
    return this.request(`/api/operations/company-context/sync-runs?limit=${limit}`);
  }

  async scanCompanyContextDrift(options: Record<string, any> = {}) {
    return this.request('/api/operations/company-context/drift-scan', {
      method: 'POST',
      body: JSON.stringify({
        dry_run: false,
        apply_low_risk: true,
        run_planner: true,
        ...options,
      }),
    });
  }

  async getCompanyContextDriftStatus() {
    return this.request('/api/operations/company-context/drift-status');
  }

  async getOperatingCadenceStatus(
    filters: {
      company_namespace?: string;
      limit?: number;
    } = {}
  ) {
    const params = new URLSearchParams({ limit: String(filters.limit ?? 200) });
    if (filters.company_namespace) {
      params.set('company_namespace', filters.company_namespace);
    }
    return this.request(`/api/operations/operating-cadence/status?${params.toString()}`);
  }

  async getOperatingCadenceFollowUps(
    filters: {
      status?: string;
      kind?: string;
      target_view?: string;
      company_namespace?: string;
      limit?: number;
    } = {}
  ) {
    const params = new URLSearchParams({
      status: filters.status ?? 'active',
      limit: String(filters.limit ?? 100),
    });
    if (filters.kind) {
      params.set('kind', filters.kind);
    }
    if (filters.target_view) {
      params.set('target_view', filters.target_view);
    }
    if (filters.company_namespace) {
      params.set('company_namespace', filters.company_namespace);
    }
    return this.request(`/api/operations/operating-cadence/follow-ups?${params.toString()}`);
  }

  async resolveOperatingCadenceFollowUp(
    planId: string,
    action: 'reviewed' | 'deferred' | 'dismissed' = 'reviewed',
    note: string = '',
    deferUntil?: string
  ) {
    return this.request(`/api/operations/operating-cadence/follow-ups/${planId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({
        action,
        note,
        ...(deferUntil ? { defer_until: deferUntil } : {}),
      }),
    });
  }

  async scanOperatingCadences(options: Record<string, any> = {}) {
    return this.request('/api/operations/operating-cadence/scan', {
      method: 'POST',
      body: JSON.stringify({
        auto_execute: true,
        limit: 200,
        ...options,
      }),
    });
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
