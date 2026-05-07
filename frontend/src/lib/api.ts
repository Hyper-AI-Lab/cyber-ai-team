const API_BASE = process.env.NEXT_PUBLIC_API_URL || `http://${typeof window !== 'undefined' ? window.location.hostname : 'localhost'}:8000`;

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

  private async request(path: string, options: RequestInit = {}): Promise<any> {
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
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`API error ${res.status}: ${text}`);
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

  async refreshSession() {
    if (!this.refreshToken) {
      throw new Error('No refresh token available');
    }
    const response = await this.request('/api/auth/refresh', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: this.refreshToken }),
    });
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

  async listAuditEvents(limit: number = 100) {
    return this.request(`/api/audit/events?limit=${limit}`);
  }
}

export const api = new ApiClient();
export default ApiClient;
