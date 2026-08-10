import type {
  AdminSessionItem,
  AdminUser,
  AutomationExecution,
  AutomationRule,
  AuditEventPage,
  CustomerExperienceSummary,
  CreatedTenantInvitation,
  CreatedWorkspaceOnboardingCode,
  EmailAuthenticationResult,
  HandoffTicket,
  CustomerDirectoryItem,
  InboxConversation,
  InboxCounts,
  LoginProviderConfiguration,
  InvitationPreview,
  ManagedSite,
  KnowledgeGap,
  MemoryItem,
  PlatformTenant,
  PlatformMembership,
  PlatformOnboardingRecord,
  PlatformPage,
  PlatformSite,
  PlatformSummary,
  PlatformUser,
  Site,
  SiteVerificationChallenge,
  SiteWebDiscoveryMode,
  SiteWebSourceConfig,
  SiteKnowledgeReadiness,
  SupportAnalytics,
  SupportConfiguration,
  SupportQueue,
  SupportQueueMember,
  SystemStatus,
  VisitorPresence,
  WidgetConfig,
  WidgetAsset,
  WidgetConfigurationState,
  Workspace,
  TenantWorkspace,
  TenantInvitation,
  WebSyncJob,
  WebSyncJobItem,
  WebCrawlManifest,
  WebSyncAvailability,
} from "./types";

interface ApiErrorBody {
  detail?: unknown;
  error?: unknown;
  message?: unknown;
}

type ApiErrorRecord = Record<string, unknown>;

const validationFieldLabels: Record<string, string> = {
  display_name: "显示名称",
  email: "企业邮箱",
  enterprise_code: "企业邀请码",
  password: "密码",
  workspace_name: "工作区名称",
};

function isApiErrorRecord(value: unknown): value is ApiErrorRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validationIssueMessage(issue: unknown): string | null {
  if (!isApiErrorRecord(issue)) return typeof issue === "string" ? issue : null;

  const type = typeof issue.type === "string" ? issue.type : "";
  const message = typeof issue.msg === "string" ? issue.msg : "输入内容无效";
  const location = Array.isArray(issue.loc) ? issue.loc : [];
  const field = [...location]
    .reverse()
    .find((part): part is string => typeof part === "string" && part !== "body");

  if (!field && type === "model_attributes_type") {
    return "提交格式无效，请刷新页面后重试";
  }
  return field ? `${validationFieldLabels[field] || field}：${message}` : message;
}

function errorDetailMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map(validationIssueMessage).filter((item): item is string => Boolean(item));
    return messages.length ? messages.join("；") : null;
  }
  if (!isApiErrorRecord(detail)) return null;

  for (const key of ["message", "detail", "error"]) {
    const message = errorDetailMessage(detail[key]);
    if (message) return message;
  }
  return validationIssueMessage(detail);
}

function apiErrorMessage(body: unknown, status: number): string {
  const fallback = `请求失败 (${status})`;
  if (!isApiErrorRecord(body)) return fallback;

  for (const key of ["detail", "message", "error"]) {
    const message = errorDetailMessage(body[key]);
    if (message) return message;
  }
  return fallback;
}

export class ApiRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = (await response.json()) as ApiErrorBody;
      message = apiErrorMessage(body, response.status);
    } catch {
      // The status remains the useful fallback.
    }
    throw new ApiRequestError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function getCurrentUser(): Promise<AdminUser> {
  const result = await request<{ user: AdminUser }>("/v1/auth/me");
  return result.user;
}

export async function login(values: {
  tenant_id: string;
  username: string;
  password: string;
}): Promise<AdminUser> {
  const result = await request<{ user: AdminUser }>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(values),
  });
  return result.user;
}

export async function emailLogin(values: {
  email: string;
  password: string;
}): Promise<EmailAuthenticationResult> {
  return request<EmailAuthenticationResult>("/v1/auth/email/login", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export async function previewInvitation(
  invitationToken: string,
): Promise<InvitationPreview> {
  return request<InvitationPreview>("/v1/auth/invitations/preview", {
    method: "POST",
    body: JSON.stringify({ invitation_token: invitationToken }),
  });
}

export async function registerWithInvitation(values: {
  invitation_token: string;
  display_name: string;
  password: string;
}): Promise<EmailAuthenticationResult> {
  return request<EmailAuthenticationResult>("/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export interface SelfServiceSignupResult {
  status: string;
  status_token: string;
  expires_at: string;
}

export async function selfServiceSignup(
  values: {
    email: string;
    password: string;
    display_name: string;
    workspace_name: string;
    enterprise_code: string;
  },
  idempotencyKey: string,
): Promise<SelfServiceSignupResult> {
  return request<SelfServiceSignupResult>("/v1/onboarding/signup", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(values),
  });
}

export async function verifySelfServiceEmail(
  verificationToken: string,
): Promise<{ status: string; tenant_id: string; workspace_name: string }> {
  return request("/v1/onboarding/verify-email", {
    method: "POST",
    body: JSON.stringify({ verification_token: verificationToken }),
  });
}

export async function resendSelfServiceVerification(
  statusToken: string,
): Promise<SelfServiceSignupResult> {
  return request<SelfServiceSignupResult>("/v1/onboarding/resend-verification", {
    method: "POST",
    body: JSON.stringify({ status_token: statusToken }),
  });
}

export async function requestPasswordReset(email: string): Promise<void> {
  await request("/v1/auth/password/forgot", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function resetEmailPassword(
  resetToken: string,
  newPassword: string,
): Promise<void> {
  await request("/v1/auth/password/reset", {
    method: "POST",
    body: JSON.stringify({ reset_token: resetToken, new_password: newPassword }),
  });
}

export async function getLoginProviders(): Promise<LoginProviderConfiguration> {
  return request<LoginProviderConfiguration>("/v1/auth/providers");
}

export async function listAuthWorkspaces(): Promise<TenantWorkspace[]> {
  const result = await request<{ items: TenantWorkspace[] }>("/v1/auth/workspaces");
  return result.items;
}

export async function switchTenant(tenantId: string): Promise<AdminUser> {
  const result = await request<{ user: AdminUser }>("/v1/auth/switch-tenant", {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId }),
  });
  return result.user;
}

export async function listPlatformTenants(): Promise<PlatformTenant[]> {
  const result = await request<{ items: PlatformTenant[] }>("/v1/platform/tenants");
  return result.items;
}

export async function queryPlatformTenants(values: {
  q?: string;
  status?: string;
  cursor?: string;
  limit?: number;
} = {}): Promise<PlatformPage<PlatformTenant>> {
  const query = new URLSearchParams();
  if (values.q) query.set("q", values.q);
  if (values.status) query.set("status", values.status);
  if (values.cursor) query.set("cursor", values.cursor);
  query.set("limit", String(values.limit ?? 25));
  return request<PlatformPage<PlatformTenant>>(`/v1/platform/tenants?${query.toString()}`);
}

export async function getPlatformSummary(): Promise<PlatformSummary> {
  return request<PlatformSummary>("/v1/platform/summary");
}

export async function getPlatformTenant(tenantId: string): Promise<PlatformTenant> {
  return request<PlatformTenant>(`/v1/platform/tenants/${encodeURIComponent(tenantId)}`);
}

interface PlatformSiteQuery {
  q?: string;
  status?: string;
  verificationStatus?: string;
  includeDisabled?: boolean;
  cursor?: string;
  limit?: number;
}

function platformSiteQuery(values: PlatformSiteQuery): string {
  const query = new URLSearchParams();
  if (values.q) query.set("q", values.q);
  if (values.status) query.set("status", values.status);
  if (values.verificationStatus) {
    query.set("verification_status", values.verificationStatus);
  }
  if (values.includeDisabled !== undefined) {
    query.set("include_disabled", String(values.includeDisabled));
  }
  if (values.cursor) query.set("cursor", values.cursor);
  query.set("limit", String(values.limit ?? 25));
  return query.toString();
}

export async function queryPlatformSites(
  values: PlatformSiteQuery & { tenantId?: string } = {},
): Promise<PlatformPage<PlatformSite>> {
  const query = new URLSearchParams(platformSiteQuery(values));
  if (values.tenantId) query.set("tenant_id", values.tenantId);
  return request<PlatformPage<PlatformSite>>(`/v1/platform/sites?${query.toString()}`);
}

export async function queryPlatformTenantSites(
  tenantId: string,
  values: PlatformSiteQuery = {},
): Promise<PlatformPage<PlatformSite>> {
  return request<PlatformPage<PlatformSite>>(
    `/v1/platform/tenants/${encodeURIComponent(tenantId)}/sites?${platformSiteQuery(values)}`,
  );
}

export async function listPlatformTenantMembers(tenantId: string): Promise<PlatformMembership[]> {
  const result = await request<{ items: PlatformMembership[] }>(
    `/v1/platform/tenants/${encodeURIComponent(tenantId)}/members`,
  );
  return result.items;
}

export async function createPlatformTenant(values: {
  tenant_id: string;
  name: string;
}): Promise<PlatformTenant> {
  return request<PlatformTenant>("/v1/platform/tenants", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export async function listPlatformUsers(): Promise<PlatformUser[]> {
  const result = await request<{ items: PlatformUser[] }>("/v1/platform/users");
  return result.items;
}

export async function queryPlatformUsers(values: {
  q?: string;
  status?: string;
  cursor?: string;
  limit?: number;
} = {}): Promise<PlatformPage<PlatformUser>> {
  const query = new URLSearchParams();
  if (values.q) query.set("q", values.q);
  if (values.status) query.set("status", values.status);
  if (values.cursor) query.set("cursor", values.cursor);
  query.set("limit", String(values.limit ?? 25));
  return request<PlatformPage<PlatformUser>>(`/v1/platform/users?${query.toString()}`);
}

export async function queryPlatformOnboardingRecords(values: {
  q?: string;
  status?: string;
  cursor?: string;
  limit?: number;
} = {}): Promise<PlatformPage<PlatformOnboardingRecord>> {
  const query = new URLSearchParams();
  if (values.q) query.set("q", values.q);
  if (values.status) query.set("status", values.status);
  if (values.cursor) query.set("cursor", values.cursor);
  query.set("limit", String(values.limit ?? 25));
  return request<PlatformPage<PlatformOnboardingRecord>>(
    `/v1/platform/onboarding-records?${query.toString()}`,
  );
}

export async function revokePlatformOnboardingRecord(codeId: string): Promise<void> {
  await request(`/v1/platform/onboarding-records/${encodeURIComponent(codeId)}/revoke`, {
    method: "POST",
  });
}

export async function createTenantInvitation(
  tenantId: string,
  values: { email: string; roles: string[]; expires_in_hours: number },
): Promise<CreatedTenantInvitation> {
  return request<CreatedTenantInvitation>(
    `/v1/platform/tenants/${encodeURIComponent(tenantId)}/invitations`,
    {
      method: "POST",
      body: JSON.stringify(values),
    },
  );
}

export async function createWorkspaceOnboardingCode(values: {
  target_email: string;
  expires_in_hours: number;
  site_limit: number;
}): Promise<CreatedWorkspaceOnboardingCode> {
  return request<CreatedWorkspaceOnboardingCode>("/v1/platform/workspace-onboarding-codes", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export async function listTenantInvitations(
  tenantId: string,
): Promise<TenantInvitation[]> {
  const result = await request<{ items: TenantInvitation[] }>(
    `/v1/platform/tenants/${encodeURIComponent(tenantId)}/invitations`,
  );
  return result.items;
}

export async function revokeTenantInvitation(
  tenantId: string,
  invitationId: string,
): Promise<TenantInvitation> {
  return request<TenantInvitation>(
    `/v1/platform/tenants/${encodeURIComponent(tenantId)}/invitations/${encodeURIComponent(invitationId)}`,
    { method: "DELETE" },
  );
}

export async function upsertTenantMembership(
  tenantId: string,
  userId: string,
  values: { roles: string[]; status: "active" | "disabled" },
): Promise<void> {
  await request(`/v1/platform/tenants/${encodeURIComponent(tenantId)}/members/${encodeURIComponent(userId)}`, {
    method: "PUT",
    body: JSON.stringify(values),
  });
}

export async function assignPlatformRole(userId: string, role: string): Promise<void> {
  await request(`/v1/platform/users/${encodeURIComponent(userId)}/platform-role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

export async function revokePlatformRole(userId: string, role: string): Promise<void> {
  await request(
    `/v1/platform/users/${encodeURIComponent(userId)}/platform-roles/${encodeURIComponent(role)}`,
    { method: "DELETE" },
  );
}

export async function logout(): Promise<void> {
  await request<void>("/v1/auth/logout", { method: "POST" });
}

export async function changePassword(values: {
  current_password: string;
  new_password: string;
}): Promise<void> {
  await request<void>("/v1/auth/change-password", {
    method: "POST",
    body: JSON.stringify(values),
  });
}


export async function listAdminSessions(): Promise<AdminSessionItem[]> {
  const result = await request<{ items: AdminSessionItem[] }>("/v1/auth/sessions");
  return result.items;
}

export async function revokeAdminSession(
  sessionId: string,
): Promise<{ session_id: string; revoked: boolean; changed: boolean; was_current: boolean }> {
  return request("/v1/auth/sessions/" + encodeURIComponent(sessionId), {
    method: "DELETE",
  });
}

export async function listAuditEvents(filters: {
  eventType?: string;
  resourceType?: string;
  correlationId?: string;
  cursor?: string;
} = {}): Promise<AuditEventPage> {
  const query = new URLSearchParams({ limit: "50" });
  if (filters.eventType) query.set("event_type", filters.eventType);
  if (filters.resourceType) query.set("resource_type", filters.resourceType);
  if (filters.correlationId) query.set("correlation_id", filters.correlationId);
  if (filters.cursor) query.set("cursor", filters.cursor);
  return request<AuditEventPage>("/v1/admin/audit-events?" + query.toString());
}

export async function listAdminUsers(): Promise<AdminUser[]> {
  const result = await request<{ items: AdminUser[] }>("/v1/admin/users");
  return result.items;
}

export async function createAdminUser(values: {
  username: string;
  display_name: string;
  password: string;
  roles: string[];
}): Promise<AdminUser> {
  return request<AdminUser>("/v1/admin/users", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export async function updateAdminUser(
  userId: string,
  values: { display_name: string; roles: string[]; status: "active" | "disabled" },
): Promise<AdminUser> {
  return request<AdminUser>("/v1/admin/users/" + userId, {
    method: "PUT",
    body: JSON.stringify(values),
  });
}

export async function resetAdminUserPassword(
  userId: string,
  newPassword: string,
): Promise<AdminUser> {
  return request<AdminUser>("/v1/admin/users/" + userId + "/reset-password", {
    method: "POST",
    body: JSON.stringify({ new_password: newPassword }),
  });
}

export async function listManagedSites(): Promise<ManagedSite[]> {
  const result = await request<{ items: ManagedSite[] }>("/v1/admin/site-management");
  return result.items;
}

export async function getSiteWebSourceConfig(siteId: string): Promise<SiteWebSourceConfig> {
  return request<SiteWebSourceConfig>(
    `/v1/admin/site-management/${encodeURIComponent(siteId)}/web-source`,
  );
}

export async function updateSiteWebSourceConfig(
  siteId: string,
  values: {
    discovery_mode: SiteWebDiscoveryMode;
    explicit_sitemap_urls: string[];
    expected_config_version: number;
  },
): Promise<SiteWebSourceConfig> {
  return request<SiteWebSourceConfig>(
    `/v1/admin/site-management/${encodeURIComponent(siteId)}/web-source`,
    { method: "PUT", body: JSON.stringify(values) },
  );
}

export async function createManagedSite(values: {
  site_id: string;
  name: string;
  base_url: string;
  primary_language: string;
}): Promise<ManagedSite> {
  return request<ManagedSite>("/v1/admin/site-management", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export async function updateManagedSite(
  siteId: string,
  values: {
    name: string;
    base_url: string;
    status: "active" | "disabled";
    primary_language: string;
  },
): Promise<ManagedSite> {
  return request<ManagedSite>("/v1/admin/site-management/" + siteId, {
    method: "PUT",
    body: JSON.stringify(values),
  });
}

export async function rotateManagedSiteKey(
  siteId: string,
  siteKey: string,
): Promise<ManagedSite> {
  return request<ManagedSite>("/v1/admin/site-management/" + siteId + "/rotate-key", {
    method: "POST",
    body: JSON.stringify({ site_key: siteKey }),
  });
}

export async function issueSiteVerificationChallenge(
  siteId: string,
  method: "dns_txt" | "script",
): Promise<SiteVerificationChallenge> {
  return request<SiteVerificationChallenge>(
    "/v1/admin/site-management/" + siteId + "/verification/challenge",
    { method: "POST", body: JSON.stringify({ method }) },
  );
}

export async function verifyManagedSite(
  siteId: string,
  method: "dns_txt" | "script",
): Promise<ManagedSite> {
  return request<ManagedSite>(
    "/v1/admin/site-management/" + siteId + "/verification/verify",
    { method: "POST", body: JSON.stringify({ method }) },
  );
}

export async function getSystemStatus(): Promise<SystemStatus> {
  return request<SystemStatus>("/v1/admin/system/status");
}

export async function listSites(): Promise<Site[]> {
  const result = await request<{ items: Site[] }>("/v1/admin/sites");
  return result.items;
}

export async function listPresence(activeWithinSeconds = 60): Promise<VisitorPresence[]> {
  const result = await request<{ items: VisitorPresence[] }>(
    `/v1/admin/presence?active_within_seconds=${activeWithinSeconds}`,
  );
  return result.items;
}

export async function listInbox(filters: {
  status?: string;
  ownership?: string;
  siteId?: string;
  mineOnly?: boolean;
  queueId?: string;
  priority?: string;
  tag?: string;
  unreadOnly?: boolean;
  search?: string;
  slaRisk?: boolean;
  priorityRisk?: boolean;
  /** @deprecated Use priorityRisk. */
  highIntent?: boolean;
  cursor?: string;
  limit?: number;
}): Promise<{ items: InboxConversation[]; next_cursor: string | null; total: number | null }> {
  const query = new URLSearchParams({ limit: String(filters.limit ?? 50) });
  if (filters.status) query.set("status", filters.status);
  if (filters.ownership) query.set("ownership", filters.ownership);
  if (filters.siteId) query.set("site_id", filters.siteId);
  if (filters.mineOnly) query.set("mine_only", "true");
  if (filters.queueId) query.set("queue_id", filters.queueId);
  if (filters.priority) query.set("priority", filters.priority);
  if (filters.tag) query.set("tag", filters.tag);
  if (filters.unreadOnly) query.set("unread_only", "true");
  if (filters.search) query.set("search", filters.search);
  if (filters.slaRisk) query.set("sla_risk", "true");
  if (filters.priorityRisk || filters.highIntent) query.set("priority_risk", "true");
  if (filters.cursor) query.set("cursor", filters.cursor);
  const result = await request<{ items: InboxConversation[]; next_cursor: string | null; total: number | null }>(
    `/v1/admin/inbox?${query.toString()}`,
  );
  return result;
}

export async function getInboxCounts(siteId?: string): Promise<InboxCounts> {
  const query = siteId ? `?site_id=${encodeURIComponent(siteId)}` : "";
  return request<InboxCounts>(`/v1/admin/inbox/counts${query}`);
}

export async function listCustomers(filters: {
  siteId?: string;
  search?: string;
  cursor?: string;
  limit?: number;
} = {}): Promise<{ items: CustomerDirectoryItem[]; next_cursor: string | null; total: number | null }> {
  const query = new URLSearchParams({ limit: String(filters.limit ?? 100) });
  if (filters.siteId) query.set("site_id", filters.siteId);
  if (filters.search) query.set("search", filters.search);
  if (filters.cursor) query.set("cursor", filters.cursor);
  return request<{ items: CustomerDirectoryItem[]; next_cursor: string | null; total: number | null }>(
    `/v1/admin/customers?${query.toString()}`,
  );
}

export async function listCustomerConversations(
  customerId: string,
  siteId?: string,
  cursor?: string,
): Promise<{ items: InboxConversation[]; next_cursor: string | null; total: number | null }> {
  const query = new URLSearchParams({ limit: "100" });
  if (siteId) query.set("site_id", siteId);
  if (cursor) query.set("cursor", cursor);
  return request<{ items: InboxConversation[]; next_cursor: string | null; total: number | null }>(
    `/v1/admin/customers/${encodeURIComponent(customerId)}/conversations?${query.toString()}`,
  );
}

export async function listHandoffs(): Promise<HandoffTicket[]> {
  const items: HandoffTicket[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ status: "", limit: "100" });
    if (cursor) query.set("cursor", cursor);
    const response: { items: HandoffTicket[]; next_cursor: string | null } = await request(
      "/v1/handoffs?" + query.toString(),
    );
    items.push(...response.items);
    cursor = response.next_cursor;
  } while (cursor);
  return items;
}

export async function getSupportConfiguration(): Promise<SupportConfiguration> {
  return request<SupportConfiguration>("/v1/admin/support-configuration");
}

export async function createSupportQueue(values: {
  name: string;
  description?: string;
  is_default?: boolean;
  site_id?: string | null;
  idempotency_key: string;
}): Promise<SupportQueue> {
  return request<SupportQueue>("/v1/admin/queues", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export async function updateSupportQueue(
  queueId: string,
  values: {
    name?: string;
    description?: string;
    status?: "active" | "disabled";
    is_default?: boolean;
    site_id?: string | null;
    idempotency_key: string;
  },
): Promise<SupportQueue> {
  return request<SupportQueue>(`/v1/admin/queues/${encodeURIComponent(queueId)}`, {
    method: "PATCH",
    body: JSON.stringify(values),
  });
}

export async function listSupportQueueMembers(queueId: string): Promise<{ items: SupportQueueMember[] }> {
  return request<{ items: SupportQueueMember[] }>(`/v1/admin/queues/${encodeURIComponent(queueId)}/members`);
}

export async function updateSupportQueueMembers(queueId: string, agentIds: string[]): Promise<{ items: SupportQueueMember[] }> {
  return request<{ items: SupportQueueMember[] }>(`/v1/admin/queues/${encodeURIComponent(queueId)}/members`, {
    method: "PUT",
    body: JSON.stringify({ agent_ids: agentIds, idempotency_key: crypto.randomUUID() }),
  });
}

export async function getWorkspace(conversationId: string): Promise<Workspace> {
  return request<Workspace>(`/v1/admin/conversations/${conversationId}`);
}

export async function conversationAction(
  conversationId: string,
  action: "takeover" | "release-to-ai" | "resolve",
): Promise<Workspace> {
  return request<Workspace>(`/v1/admin/conversations/${conversationId}/${action}`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
  });
}

export async function markConversationRead(conversationId: string): Promise<Workspace> {
  return request<Workspace>(`/v1/admin/conversations/${conversationId}/read`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
  });
}

export async function sendAgentMessage(
  conversationId: string,
  content: string,
): Promise<Workspace> {
  return request<Workspace>(`/v1/admin/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: crypto.randomUUID(), content }),
  });
}

export async function addInternalNote(conversationId: string, content: string): Promise<Workspace> {
  return request<Workspace>(`/v1/admin/conversations/${conversationId}/notes`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: crypto.randomUUID(), content }),
  });
}

export async function updateConversationRouting(
  conversationId: string,
  values: {
    assigned_agent_id: string | null;
    queue_id: string | null;
    priority: "low" | "normal" | "high" | "urgent";
    tags: string[];
  },
): Promise<Workspace> {
  return request<Workspace>(`/v1/admin/conversations/${conversationId}/routing`, {
    method: "POST",
    body: JSON.stringify({ ...values, idempotency_key: crypto.randomUUID() }),
  });
}

export async function createManualHandoff(
  conversationId: string,
  values: { summary: string; queue_id: string | null; priority: string },
): Promise<Workspace> {
  return request<Workspace>(`/v1/admin/conversations/${conversationId}/handoffs`, {
    method: "POST",
    body: JSON.stringify({ ...values, idempotency_key: crypto.randomUUID() }),
  });
}

export async function createCannedReply(values: {
  title: string;
  content: string;
  shortcut: string;
}): Promise<import("./types").CannedReply> {
  return request("/v1/admin/canned-replies", {
    method: "POST",
    body: JSON.stringify({ ...values, idempotency_key: crypto.randomUUID() }),
  });
}

export async function listMemory(customerId: string): Promise<MemoryItem[]> {
  const result = await request<{ items: MemoryItem[] }>(
    `/v1/admin/customers/${customerId}/memory`,
  );
  return result.items;
}



export async function getSupportAnalytics(days = 30, siteId?: string): Promise<SupportAnalytics> {
  const query = new URLSearchParams({ days: String(days) });
  if (siteId) query.set("site_id", siteId);
  return request<SupportAnalytics>("/v1/admin/analytics/overview?" + query.toString());
}

const experienceBase = "/v1/admin/customer-experience";

export async function getWidgetConfiguration(siteId: string): Promise<WidgetConfigurationState> {
  return request(`${experienceBase}/sites/${encodeURIComponent(siteId)}/widget-config`);
}

export async function listWidgetAssets(siteId: string): Promise<WidgetAsset[]> {
  const result = await request<{ items: WidgetAsset[] }>(
    `${experienceBase}/sites/${encodeURIComponent(siteId)}/widget-assets`,
  );
  return result.items;
}

export async function uploadWidgetAsset(
  siteId: string,
  file: File,
  purpose: "launcher" | "avatar",
): Promise<WidgetAsset> {
  return request(
    `${experienceBase}/sites/${encodeURIComponent(siteId)}/widget-assets?purpose=${purpose}`,
    {
      method: "POST",
      headers: {
        "Content-Type": file.type,
        "X-Idempotency-Key": crypto.randomUUID(),
      },
      body: file,
    },
  );
}

export async function saveWidgetDraft(
  siteId: string,
  config: WidgetConfig,
): Promise<WidgetConfigurationState> {
  return request(`${experienceBase}/sites/${encodeURIComponent(siteId)}/widget-config/drafts`, {
    method: "POST",
    body: JSON.stringify({ ...config, idempotency_key: crypto.randomUUID() }),
  });
}

export async function publishWidgetVersion(
  siteId: string,
  versionId: string,
): Promise<WidgetConfigurationState> {
  return request(`${experienceBase}/sites/${encodeURIComponent(siteId)}/widget-config/publish`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId, idempotency_key: crypto.randomUUID() }),
  });
}

export async function rollbackWidgetVersion(
  siteId: string,
  versionId: string,
): Promise<WidgetConfigurationState> {
  return request(`${experienceBase}/sites/${encodeURIComponent(siteId)}/widget-config/rollback`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId, idempotency_key: crypto.randomUUID() }),
  });
}

export async function listAutomationRules(): Promise<AutomationRule[]> {
  const result = await request<{ items: AutomationRule[] }>(`${experienceBase}/automation/rules`);
  return result.items;
}

export async function saveAutomationRule(
  values: Omit<AutomationRule, "created_at" | "updated_at" | "rule_id"> & { rule_id: string | null },
): Promise<AutomationRule> {
  const { rule_id, name, enabled, sort_order, conditions, actions } = values;
  return request(`${experienceBase}/automation/rules`, {
    method: "POST",
    body: JSON.stringify({
      rule_id,
      name,
      enabled,
      sort_order,
      conditions,
      actions,
      idempotency_key: crypto.randomUUID(),
    }),
  });
}

export async function deleteAutomationRule(ruleId: string): Promise<void> {
  await request(`${experienceBase}/automation/rules/${encodeURIComponent(ruleId)}`, {
    method: "DELETE",
    body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
  });
}

export async function testAutomationRule(
  conditions: AutomationRule["conditions"],
  facts: {
    site_id: string; page_path: string; within_business_hours: boolean;
    user_intent: string | null; risk_level: number; authenticated: boolean;
    dwell_seconds: number; has_assignee: boolean; has_ticket: boolean;
  },
): Promise<{ matched: boolean; reasons: string[] }> {
  return request(`${experienceBase}/automation/test`, {
    method: "POST",
    body: JSON.stringify({ conditions, facts }),
  });
}

export async function listAutomationExecutions(): Promise<AutomationExecution[]> {
  const result = await request<{ items: AutomationExecution[] }>(
    `${experienceBase}/automation/executions?limit=100`,
  );
  return result.items;
}

export async function createKnowledgeGap(
  conversationId: string,
  category: "missing_knowledge" | "incorrect_answer",
  summary: string,
): Promise<KnowledgeGap> {
  return request(`${experienceBase}/conversations/${conversationId}/knowledge-gaps`, {
    method: "POST",
    body: JSON.stringify({ category, summary, idempotency_key: crypto.randomUUID() }),
  });
}

export async function listKnowledgeGaps(status = ""): Promise<KnowledgeGap[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const result = await request<{ items: KnowledgeGap[] }>(`${experienceBase}/knowledge-gaps${query}`);
  return result.items;
}

export async function resolveKnowledgeGap(gapId: string, resolutionNote: string): Promise<KnowledgeGap> {
  return request(`${experienceBase}/knowledge-gaps/${gapId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution_note: resolutionNote, idempotency_key: crypto.randomUUID() }),
  });
}

export async function getExperienceSummary(
  days = 30,
  siteId?: string,
): Promise<CustomerExperienceSummary> {
  const query = new URLSearchParams({ days: String(days) });
  if (siteId) query.set("site_id", siteId);
  return request(`${experienceBase}/summary?${query.toString()}`);
}

export async function enqueueWebSyncJob(
  siteId: string,
  manifestId: string,
  mode: "shadow" | "production",
  sampleSize: 20 | 100 | 200 | 500 | null,
  signal?: AbortSignal,
): Promise<{ created: boolean; job: WebSyncJob }> {
  return request(`/v1/knowledge/web-sync-jobs/${encodeURIComponent(siteId)}`, {
    method: "POST",
    signal,
    body: JSON.stringify({
      idempotency_key: crypto.randomUUID(),
      manifest_id: manifestId,
      mode,
      sample_size: sampleSize,
    }),
  });
}

export async function runWebCrawlPreflight(
  siteId: string,
  signal?: AbortSignal,
): Promise<WebCrawlManifest> {
  return request(`/v1/knowledge/web-crawl-preflights/${encodeURIComponent(siteId)}`, {
    method: "POST",
    signal,
    body: JSON.stringify({ translation_provider: "gtranslate" }),
  });
}

export async function getLatestWebCrawlManifest(
  siteId: string,
  signal?: AbortSignal,
): Promise<WebCrawlManifest | null> {
  return request(`/v1/knowledge/web-crawl-preflights/${encodeURIComponent(siteId)}/latest`, { signal });
}

export async function getWebSyncAvailability(
  siteId: string,
  signal?: AbortSignal,
): Promise<WebSyncAvailability> {
  return request(`/v1/knowledge/web-sync-availability/${encodeURIComponent(siteId)}`, { signal });
}

export async function getSiteKnowledgeReadiness(
  siteId: string,
  signal?: AbortSignal,
): Promise<SiteKnowledgeReadiness> {
  return request(`/v1/knowledge/readiness/${encodeURIComponent(siteId)}`, { signal });
}

export async function listWebSyncJobs(
  siteId?: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<WebSyncJob[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (siteId) query.set("site_id", siteId);
  const result = await request<{ items: WebSyncJob[] }>(
    `/v1/knowledge/web-sync-jobs?${query.toString()}`,
    { signal },
  );
  return result.items;
}

export async function getWebSyncJob(jobId: string, signal?: AbortSignal): Promise<WebSyncJob> {
  return request(`/v1/knowledge/web-sync-jobs/${encodeURIComponent(jobId)}`, { signal });
}

export async function cancelWebSyncJob(jobId: string, signal?: AbortSignal): Promise<WebSyncJob> {
  return request(`/v1/knowledge/web-sync-jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    signal,
  });
}

export async function retryWebSyncJob(jobId: string, signal?: AbortSignal): Promise<WebSyncJob> {
  return request(`/v1/knowledge/web-sync-jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
    signal,
  });
}

export async function reconcileStaleWebSyncVersions(
  jobId: string,
  signal?: AbortSignal,
): Promise<WebSyncJob> {
  return request(
    `/v1/knowledge/web-sync-jobs/${encodeURIComponent(jobId)}/reconcile-stale-versions`,
    { method: "POST", signal },
  );
}

export async function listWebSyncJobItems(
  jobId: string,
  offset = 0,
  limit = 100,
  signal?: AbortSignal,
): Promise<{ items: WebSyncJobItem[]; offset: number; next_offset: number | null }> {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return request(
    `/v1/knowledge/web-sync-jobs/${encodeURIComponent(jobId)}/items?${query.toString()}`,
    { signal },
  );
}
