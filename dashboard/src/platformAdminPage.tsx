import { useCallback, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  Copy,
  ExternalLink,
  Globe2,
  KeyRound,
  LoaderCircle,
  Mail,
  MoreHorizontal,
  Plus,
  Search,
  ShieldCheck,
  UserCog,
  UserPlus,
  Users,
  X,
} from "lucide-react";

import {
  assignPlatformRole,
  createPlatformTenant,
  createWorkspaceOnboardingCode,
  getPlatformSummary,
  getPlatformTenant,
  listPlatformTenantMembers,
  queryPlatformOnboardingRecords,
  queryPlatformSites,
  queryPlatformTenantSites,
  queryPlatformTenants,
  queryPlatformUsers,
  revokePlatformOnboardingRecord,
  revokePlatformRole,
  upsertTenantMembership,
} from "./api";
import { formatRoleLabel } from "./content/terminology";
import type {
  AdminUser,
  CreatedWorkspaceOnboardingCode,
  PlatformMembership,
  PlatformOnboardingRecord,
  PlatformSite,
  PlatformSummary,
  PlatformTenant,
  PlatformUser,
} from "./types";

type PlatformTab = "overview" | "workspaces" | "sites" | "users" | "onboarding" | "access";
type ConfirmAction =
  | { kind: "revoke-code"; record: PlatformOnboardingRecord }
  | { kind: "revoke-role"; user: PlatformUser; role: string }
  | { kind: "membership"; member: PlatformMembership; status: "active" | "disabled" }
  | null;

const platformTabs: Array<{ id: PlatformTab; label: string; icon: typeof CircleGauge }> = [
  { id: "overview", label: "概览", icon: CircleGauge },
  { id: "workspaces", label: "工作区", icon: Building2 },
  { id: "sites", label: "网站目录", icon: Globe2 },
  { id: "users", label: "用户", icon: Users },
  { id: "onboarding", label: "开通记录", icon: Mail },
  { id: "access", label: "平台权限", icon: ShieldCheck },
];

const workspaceRoleOptions = [
  "tenant_owner",
  "support_manager",
  "support_agent",
  "knowledge_admin",
  "auditor",
] as const;

const platformRoleOptions = [
  ["platform_operator", "平台运营", "可开通工作区并管理跨工作区成员"],
  ["platform_auditor", "平台审计", "只读查看平台资源和审计信息"],
  ["platform_owner", "平台所有者", "可管理平台角色和全部平台能力"],
] as const;

export function PlatformAdminPage({ user }: { user: AdminUser }) {
  const [tab, setTabState] = useState<PlatformTab>(() => tabFromLocation(user));
  const [summary, setSummary] = useState<PlatformSummary | null>(null);
  const [tenants, setTenants] = useState<PlatformTenant[]>([]);
  const [tenantTotal, setTenantTotal] = useState(0);
  const [tenantCursor, setTenantCursor] = useState<string | null>(null);
  const [tenantSearch, setTenantSearch] = useState("");
  const [tenantStatus, setTenantStatus] = useState("");
  const [sites, setSites] = useState<PlatformSite[]>([]);
  const [siteTotal, setSiteTotal] = useState(0);
  const [siteCursor, setSiteCursor] = useState<string | null>(null);
  const [siteSearch, setSiteSearch] = useState("");
  const [siteStatus, setSiteStatus] = useState("");
  const [siteVerification, setSiteVerification] = useState("");
  const [siteLoading, setSiteLoading] = useState(false);
  const [siteError, setSiteError] = useState("");
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [userTotal, setUserTotal] = useState(0);
  const [userCursor, setUserCursor] = useState<string | null>(null);
  const [userSearch, setUserSearch] = useState("");
  const [userStatus, setUserStatus] = useState("");
  const [records, setRecords] = useState<PlatformOnboardingRecord[]>([]);
  const [recordTotal, setRecordTotal] = useState(0);
  const [recordCursor, setRecordCursor] = useState<string | null>(null);
  const [recordSearch, setRecordSearch] = useState("");
  const [recordStatus, setRecordStatus] = useState("");
  const [selectedTenant, setSelectedTenant] = useState<PlatformTenant | null>(null);
  const [tenantMembers, setTenantMembers] = useState<PlatformMembership[]>([]);
  const [tenantSites, setTenantSites] = useState<PlatformSite[]>([]);
  const [tenantSiteTotal, setTenantSiteTotal] = useState(0);
  const [tenantSiteCursor, setTenantSiteCursor] = useState<string | null>(null);
  const [tenantSitesLoading, setTenantSitesLoading] = useState(false);
  const [tenantSitesError, setTenantSitesError] = useState("");
  const [selectedUser, setSelectedUser] = useState<PlatformUser | null>(null);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [directCreateOpen, setDirectCreateOpen] = useState(false);
  const [membershipOpen, setMembershipOpen] = useState(false);
  const [roleOpen, setRoleOpen] = useState(false);
  const [roleTarget, setRoleTarget] = useState<PlatformUser | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const isOwner = user.platform_roles.includes("platform_owner");
  const canOperate = user.platform_roles.some((role) =>
    ["platform_owner", "platform_operator"].includes(role),
  );

  const refreshSummary = useCallback(async () => {
    setSummary(await getPlatformSummary());
  }, []);

  const loadTenants = useCallback(
    async (append = false) => {
      const page = await queryPlatformTenants({
        q: tenantSearch.trim() || undefined,
        status: tenantStatus || undefined,
        cursor: append ? tenantCursor || undefined : undefined,
      });
      setTenants((current) => (append ? [...current, ...page.items] : page.items));
      setTenantTotal(page.total);
      setTenantCursor(page.next_cursor);
    },
    [tenantCursor, tenantSearch, tenantStatus],
  );

  const loadSites = useCallback(
    async (append = false) => {
      setSiteLoading(true);
      setSiteError("");
      try {
        const page = await queryPlatformSites({
          q: siteSearch.trim() || undefined,
          status: siteStatus || undefined,
          verificationStatus: siteVerification || undefined,
          includeDisabled: true,
          cursor: append ? siteCursor || undefined : undefined,
        });
        setSites((current) => (append ? mergeSites(current, page.items) : page.items));
        setSiteTotal(page.total);
        setSiteCursor(page.next_cursor);
      } catch (reason) {
        setSiteError(reason instanceof Error ? reason.message : "网站目录加载失败");
      } finally {
        setSiteLoading(false);
      }
    },
    [siteCursor, siteSearch, siteStatus, siteVerification],
  );

  const loadUsers = useCallback(
    async (append = false) => {
      const page = await queryPlatformUsers({
        q: userSearch.trim() || undefined,
        status: userStatus || undefined,
        cursor: append ? userCursor || undefined : undefined,
      });
      setUsers((current) => (append ? [...current, ...page.items] : page.items));
      setUserTotal(page.total);
      setUserCursor(page.next_cursor);
    },
    [userCursor, userSearch, userStatus],
  );

  const loadRecords = useCallback(
    async (append = false) => {
      const page = await queryPlatformOnboardingRecords({
        q: recordSearch.trim() || undefined,
        status: recordStatus || undefined,
        cursor: append ? recordCursor || undefined : undefined,
      });
      setRecords((current) => (append ? [...current, ...page.items] : page.items));
      setRecordTotal(page.total);
      setRecordCursor(page.next_cursor);
    },
    [recordCursor, recordSearch, recordStatus],
  );

  const run = useCallback(async (operation: () => Promise<void>) => {
    setLoading(true);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTenantSites = useCallback(
    async (tenantId: string, append = false) => {
      setTenantSitesLoading(true);
      setTenantSitesError("");
      try {
        const page = await queryPlatformTenantSites(tenantId, {
          includeDisabled: true,
          cursor: append ? tenantSiteCursor || undefined : undefined,
          limit: 50,
        });
        setTenantSites((current) => (append ? mergeSites(current, page.items) : page.items));
        setTenantSiteTotal(page.total);
        setTenantSiteCursor(page.next_cursor);
      } catch (reason) {
        setTenantSitesError(reason instanceof Error ? reason.message : "绑定网站加载失败");
      } finally {
        setTenantSitesLoading(false);
      }
    },
    [tenantSiteCursor],
  );

  useEffect(() => {
    void run(async () => {
      const tasks: Promise<unknown>[] = [refreshSummary()];
      if (tab === "workspaces") tasks.push(loadTenants());
      if (tab === "sites") tasks.push(loadSites());
      if (tab === "users" || tab === "access") tasks.push(loadUsers());
      if (tab === "onboarding") tasks.push(loadRecords());
      await Promise.all(tasks);
    });
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  function setTab(next: PlatformTab) {
    if (next === "access" && !isOwner) return;
    setTabState(next);
    const query = new URLSearchParams(window.location.search);
    query.set("tab", next);
    window.history.replaceState({}, "", `/platform?${query.toString()}`);
    setError("");
    setNotice("");
  }

  async function openTenant(item: PlatformTenant) {
    setSelectedTenant(item);
    setTenantMembers([]);
    setTenantSites([]);
    setTenantSiteTotal(0);
    setTenantSiteCursor(null);
    setTenantSitesError("");
    setTenantSitesLoading(true);
    await run(async () => {
      try {
        const [tenant, members] = await Promise.all([
          getPlatformTenant(item.tenant_id),
          listPlatformTenantMembers(item.tenant_id),
        ]);
        setSelectedTenant(tenant);
        setTenantMembers(members);
        await loadTenantSites(item.tenant_id);
      } finally {
        setTenantSitesLoading(false);
      }
    });
  }

  async function openTenantById(tenantId: string) {
    setTenantMembers([]);
    setTenantSites([]);
    setTenantSiteTotal(0);
    setTenantSiteCursor(null);
    setTenantSitesError("");
    setTenantSitesLoading(true);
    await run(async () => {
      try {
        const [tenant, members] = await Promise.all([
          getPlatformTenant(tenantId),
          listPlatformTenantMembers(tenantId),
        ]);
        setSelectedTenant(tenant);
        setTenantMembers(members);
        await loadTenantSites(tenantId);
      } finally {
        setTenantSitesLoading(false);
      }
    });
  }

  function closeTenant() {
    setSelectedTenant(null);
    setTenantMembers([]);
    setTenantSites([]);
    setTenantSiteTotal(0);
    setTenantSiteCursor(null);
    setTenantSitesError("");
    setTenantSitesLoading(false);
  }

  async function refreshSelectedTenant() {
    if (!selectedTenant) return;
    const [tenant, members] = await Promise.all([
      getPlatformTenant(selectedTenant.tenant_id),
      listPlatformTenantMembers(selectedTenant.tenant_id),
    ]);
    setSelectedTenant(tenant);
    setTenantMembers(members);
    await loadTenantSites(tenant.tenant_id);
    await loadTenants();
    await refreshSummary();
  }

  async function executeConfirmation() {
    if (!confirmAction) return;
    await run(async () => {
      if (confirmAction.kind === "revoke-code") {
        await revokePlatformOnboardingRecord(confirmAction.record.code_id);
        setNotice("开通链接已撤销。");
        await Promise.all([loadRecords(), refreshSummary()]);
      } else if (confirmAction.kind === "revoke-role") {
        await revokePlatformRole(confirmAction.user.user_id, confirmAction.role);
        setNotice("平台角色已撤销，相关登录会话已失效。");
        await Promise.all([loadUsers(), refreshSummary()]);
      } else {
        await upsertTenantMembership(
          confirmAction.member.tenant_id,
          confirmAction.member.user_id,
          { roles: confirmAction.member.roles, status: confirmAction.status },
        );
        setNotice(confirmAction.status === "active" ? "成员已重新启用。" : "成员已停用。");
        await refreshSelectedTenant();
      }
      setConfirmAction(null);
    });
  }

  const visibleTabs = platformTabs.filter((item) => item.id !== "access" || isOwner);

  return (
    <div className="platform-admin-page">
      <div className="platform-admin-header">
        <nav className="platform-tabs" aria-label="平台管理视图">
          {visibleTabs.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={tab === item.id ? "active" : ""}
                onClick={() => setTab(item.id)}
              >
                <Icon aria-hidden="true" />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="platform-primary-actions">
          {canOperate && (
            <button className="platform-primary-button" onClick={() => setOnboardingOpen(true)}>
              <Plus aria-hidden="true" />
              开通独立工作区
            </button>
          )}
          {canOperate && (
            <details className="platform-more-menu">
              <summary title="更多操作" aria-label="更多操作">
                <MoreHorizontal aria-hidden="true" />
              </summary>
              <button onClick={() => setDirectCreateOpen(true)}>直接创建工作区</button>
            </details>
          )}
        </div>
      </div>

      {(error || notice) && (
        <button
          className={`platform-feedback ${error ? "error" : "success"}`}
          onClick={() => {
            setError("");
            setNotice("");
          }}
        >
          {error || notice}
          <X aria-hidden="true" />
        </button>
      )}

      {loading && <div className="platform-loading">正在更新平台数据…</div>}

      {tab === "overview" && summary && (
        <PlatformOverview summary={summary} onNavigate={setTab} />
      )}

      {tab === "workspaces" && (
        <DirectorySection
          title="工作区"
          count={tenantTotal}
          search={tenantSearch}
          status={tenantStatus}
          searchPlaceholder="搜索工作区名称或标识"
          onSearch={setTenantSearch}
          onStatus={setTenantStatus}
          onSubmit={() => void run(() => loadTenants())}
        >
          <div className="platform-table-shell">
            <table>
              <thead>
                <tr>
                  <th>工作区</th>
                  <th>所有者</th>
                  <th>启用成员</th>
                  <th>绑定网站</th>
                  <th>套餐</th>
                  <th>状态</th>
                  <th>最近活动</th>
                  <th aria-label="操作" />
                </tr>
              </thead>
              <tbody>
                {tenants.map((item) => (
                  <tr key={item.tenant_id}>
                    <td>
                      <button className="platform-entity-link" onClick={() => void openTenant(item)}>
                        <strong>{item.name}</strong>
                        <small>{item.tenant_id}</small>
                      </button>
                    </td>
                    <td><TenantOwners tenant={item} /></td>
                    <td>
                      <strong>{item.member_count}</strong>
                      {item.disabled_member_count > 0 && (
                        <small className="cell-subtext">已停用 {item.disabled_member_count}</small>
                      )}
                    </td>
                    <td>
                      <div className="platform-count-cell">
                        <strong>{item.site_count + item.disabled_site_count} 个</strong>
                        <small>
                          {item.site_count} 启用
                          {item.disabled_site_count > 0 && ` · ${item.disabled_site_count} 停用`}
                          {item.unverified_site_count > 0 && ` · ${item.unverified_site_count} 未验证`}
                        </small>
                        {item.site_limit && <small>额度占用 {item.site_quota_used}/{item.site_limit}</small>}
                      </div>
                    </td>
                    <td>{planLabel(item.plan_id)}</td>
                    <td><StatusBadge value={item.status} /></td>
                    <td>{item.last_activity_at ? formatDateTime(item.last_activity_at) : "暂无"}</td>
                    <td>
                      <button
                        className="icon-command"
                        title="查看工作区"
                        aria-label={`查看 ${item.name}`}
                        onClick={() => void openTenant(item)}
                      >
                        <ChevronRight aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {tenants.length === 0 && <EmptyState label="没有匹配的工作区" />}
          </div>
          {tenantCursor && (
            <LoadMore loaded={tenants.length} total={tenantTotal} onClick={() => void run(() => loadTenants(true))} />
          )}
        </DirectorySection>
      )}

      {tab === "sites" && (
        <DirectorySection
          title="网站目录"
          count={siteTotal}
          search={siteSearch}
          status={siteStatus}
          searchPlaceholder="搜索网站、域名、工作区或站点标识"
          onSearch={setSiteSearch}
          onStatus={setSiteStatus}
          onSubmit={() => void loadSites()}
          extraFilters={(
            <select
              value={siteVerification}
              onChange={(event) => setSiteVerification(event.target.value)}
              aria-label="验证状态筛选"
            >
              <option value="">全部验证状态</option>
              <option value="verified">已验证</option>
              <option value="pending">待验证</option>
              <option value="failed">验证失败</option>
              <option value="expired">验证已过期</option>
            </select>
          )}
        >
          <div className="platform-table-shell platform-site-table">
            <table>
              <thead>
                <tr>
                  <th>网站</th>
                  <th>域名</th>
                  <th>管理工作区</th>
                  <th>负责人</th>
                  <th>状态</th>
                  <th>验证</th>
                  <th>知识发布</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                {sites.map((item) => (
                  <tr key={`${item.tenant_id}:${item.site_id}`}>
                    <td>
                      <div className="platform-user-cell">
                        <strong>{item.name}</strong>
                        <small>{item.site_id}</small>
                      </div>
                    </td>
                    <td><SiteDomain site={item} /></td>
                    <td>
                      <button
                        className="platform-entity-link"
                        onClick={() => void openTenantById(item.tenant_id)}
                      >
                        <strong>{item.tenant_name}</strong>
                        <small>{item.tenant_id}</small>
                      </button>
                    </td>
                    <td><SiteManagers site={item} /></td>
                    <td><StatusBadge value={item.status} /></td>
                    <td><VerificationBadge value={item.verification_status} /></td>
                    <td><KnowledgeBadge value={item.knowledge_publication_state} /></td>
                    <td>{formatDateTime(item.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <DirectoryLoadState
              loading={siteLoading}
              error={siteError}
              empty={!sites.length}
              emptyLabel="没有匹配的网站"
              onRetry={() => void loadSites()}
            />
          </div>
          {siteCursor && !siteLoading && (
            <LoadMore loaded={sites.length} total={siteTotal} onClick={() => void loadSites(true)} />
          )}
        </DirectorySection>
      )}

      {tab === "users" && (
        <DirectorySection
          title="平台用户"
          count={userTotal}
          search={userSearch}
          status={userStatus}
          searchPlaceholder="搜索姓名或邮箱"
          onSearch={setUserSearch}
          onStatus={setUserStatus}
          onSubmit={() => void run(() => loadUsers())}
        >
          <UserTable users={users} onSelect={setSelectedUser} showAccess />
          {userCursor && (
            <LoadMore loaded={users.length} total={userTotal} onClick={() => void run(() => loadUsers(true))} />
          )}
        </DirectorySection>
      )}

      {tab === "onboarding" && (
        <DirectorySection
          title="独立工作区开通记录"
          count={recordTotal}
          search={recordSearch}
          status={recordStatus}
          searchPlaceholder="搜索企业邮箱或工作区"
          onSearch={setRecordSearch}
          onStatus={setRecordStatus}
          statusOptions={onboardingStatusOptions}
          onSubmit={() => void run(() => loadRecords())}
        >
          <div className="platform-table-shell">
            <table>
              <thead>
                <tr>
                  <th>企业邮箱</th>
                  <th>状态</th>
                  <th>工作区</th>
                  <th>有效期</th>
                  <th>创建人</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {records.map((item) => (
                  <tr key={item.code_id}>
                    <td><strong>{item.target_email}</strong></td>
                    <td><OnboardingBadge status={item.status} /></td>
                    <td>{item.workspace_name || "尚未填写"}</td>
                    <td>{onboardingExpiry(item)}</td>
                    <td>{item.created_by_name || shortId(item.created_by)}</td>
                    <td>{formatDateTime(item.created_at)}</td>
                    <td>
                      {item.status === "issued" || item.status === "verification_pending" || item.status === "failed" ? (
                        <button className="danger-text-button" onClick={() => setConfirmAction({ kind: "revoke-code", record: item })}>撤销</button>
                      ) : item.tenant_id ? (
                        <button
                          className="secondary-command"
                          onClick={() => {
                            setTab("workspaces");
                            setTenantSearch(item.tenant_id || "");
                          }}
                        >
                          查看工作区
                        </button>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {records.length === 0 && <EmptyState label="暂无开通记录" />}
          </div>
          {recordCursor && (
            <LoadMore loaded={records.length} total={recordTotal} onClick={() => void run(() => loadRecords(true))} />
          )}
        </DirectorySection>
      )}

      {tab === "access" && isOwner && (
        <DirectorySection
          title="平台权限"
          count={userTotal}
          search={userSearch}
          status={userStatus}
          searchPlaceholder="搜索需要授权的用户"
          onSearch={setUserSearch}
          onStatus={setUserStatus}
          onSubmit={() => void run(() => loadUsers())}
        >
          <div className="access-boundary-note">
            <ShieldCheck aria-hidden="true" />
            <span>平台角色只管理平台控制面，不会自动获得客户会话访问权。</span>
          </div>
          <div className="platform-table-shell">
            <table>
              <thead><tr><th>用户</th><th>当前工作区</th><th>平台角色</th><th>最近登录</th><th>操作</th></tr></thead>
              <tbody>
                {users.map((item) => (
                  <tr key={item.user_id}>
                    <td><div className="platform-user-cell"><strong>{item.display_name}</strong><small>{item.email || shortId(item.user_id)}</small></div></td>
                    <td><WorkspaceAccessCount user={item} /></td>
                    <td>
                      <div className="role-chip-list">
                        {item.platform_roles.length ? item.platform_roles.map((role) => (
                          <span key={role}>{platformRoleLabel(role)}<button title={`撤销${platformRoleLabel(role)}`} aria-label={`撤销 ${platformRoleLabel(role)}`} onClick={() => setConfirmAction({ kind: "revoke-role", user: item, role })}><X aria-hidden="true" /></button></span>
                        )) : <span className="muted-role">无平台角色</span>}
                      </div>
                    </td>
                    <td>{item.last_login_at ? formatDateTime(item.last_login_at) : "从未登录"}</td>
                    <td><button className="secondary-command" onClick={() => { setRoleTarget(item); setRoleOpen(true); }}><KeyRound aria-hidden="true" />授予角色</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {userCursor && (
            <LoadMore loaded={users.length} total={userTotal} onClick={() => void run(() => loadUsers(true))} />
          )}
        </DirectorySection>
      )}

      {selectedTenant && (
        <WorkspaceDrawer
          tenant={selectedTenant}
          members={tenantMembers}
          sites={tenantSites}
          siteTotal={tenantSiteTotal}
          siteCursor={tenantSiteCursor}
          sitesLoading={tenantSitesLoading}
          sitesError={tenantSitesError}
          canOperate={canOperate}
          onClose={closeTenant}
          onAddMember={() => setMembershipOpen(true)}
          onMembershipStatus={(member, status) => setConfirmAction({ kind: "membership", member, status })}
          onRetrySites={() => void loadTenantSites(selectedTenant.tenant_id)}
          onLoadMoreSites={() => void loadTenantSites(selectedTenant.tenant_id, true)}
        />
      )}

      {selectedUser && <UserDrawer user={selectedUser} onClose={() => setSelectedUser(null)} />}

      {onboardingOpen && (
        <OnboardingDialog
          busy={loading}
          onClose={() => setOnboardingOpen(false)}
          onCreated={async () => {
            setNotice("注册链接已生成。原始邀请码关闭后无法再次查看。");
            await Promise.all([loadRecords(), refreshSummary()]);
          }}
        />
      )}

      {directCreateOpen && (
        <DirectCreateDialog
          busy={loading}
          onClose={() => setDirectCreateOpen(false)}
          onSubmit={(tenantId, name) => run(async () => {
            await createPlatformTenant({ tenant_id: tenantId, name });
            setDirectCreateOpen(false);
            setNotice("工作区已直接创建。请尽快分配工作区所有者。");
            await Promise.all([loadTenants(), refreshSummary()]);
          })}
        />
      )}

      {membershipOpen && selectedTenant && (
        <MembershipDialog
          tenant={selectedTenant}
          busy={loading}
          onClose={() => setMembershipOpen(false)}
          onSubmit={(targetUser, role) => run(async () => {
            await upsertTenantMembership(selectedTenant.tenant_id, targetUser.user_id, {
              roles: [role],
              status: "active",
            });
            setMembershipOpen(false);
            setNotice(`${targetUser.display_name} 已加入 ${selectedTenant.name}。`);
            await refreshSelectedTenant();
          })}
        />
      )}

      {roleOpen && roleTarget && (
        <PlatformRoleDialog
          user={roleTarget}
          busy={loading}
          onClose={() => setRoleOpen(false)}
          onSubmit={(role) => run(async () => {
            await assignPlatformRole(roleTarget.user_id, role);
            setRoleOpen(false);
            setNotice(`${roleTarget.display_name} 已获得${platformRoleLabel(role)}权限。`);
            await Promise.all([loadUsers(), refreshSummary()]);
          })}
        />
      )}

      {confirmAction && (
        <ConfirmDialog
          action={confirmAction}
          busy={loading}
          onCancel={() => setConfirmAction(null)}
          onConfirm={() => void executeConfirmation()}
        />
      )}
    </div>
  );
}

function PlatformOverview({ summary, onNavigate }: { summary: PlatformSummary; onNavigate: (tab: PlatformTab) => void }) {
  const metrics = [
    ["活跃工作区", summary.active_workspace_count, "workspaces"],
    ["平台用户", summary.user_count, "users"],
    ["待完成开通", summary.pending_onboarding_count, "onboarding"],
    ["需要关注", summary.attention_count, "overview"],
  ] as const;
  const attention = [
    ["即将过期的邀请码", summary.expiring_code_count, "onboarding"],
    ["邮件发送失败", summary.failed_email_count, "onboarding"],
    ["没有所有者的工作区", summary.orphan_workspace_count, "workspaces"],
  ] as const;
  return (
    <div className="platform-overview">
      <section className="platform-metric-band" aria-label="平台指标">
        {metrics.map(([label, value, target]) => (
          <button key={label} onClick={() => onNavigate(target)}>
            <span>{label}</span><strong>{value}</strong><ChevronRight aria-hidden="true" />
          </button>
        ))}
      </section>
      <div className="platform-overview-grid">
        <section className="platform-section">
          <header><div><h2>待处理事项</h2><p>需要平台管理员确认的异常和风险</p></div></header>
          <div className="attention-list">
            {attention.map(([label, count, target]) => (
              <button key={label} onClick={() => onNavigate(target)} disabled={count === 0}>
                <span className={count > 0 ? "warning" : "ok"}>{count > 0 ? <AlertTriangle /> : <CheckCircle2 />}</span>
                <div><strong>{label}</strong><small>{count > 0 ? `${count} 项待处理` : "当前正常"}</small></div>
                <ChevronRight aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
        <section className="platform-section">
          <header><div><h2>最近平台操作</h2><p>跨工作区控制面的审计记录</p></div></header>
          <div className="activity-list">
            {summary.recent_activity.map((item) => (
              <div key={`${item.event_type}-${item.resource_id}-${item.created_at}`}>
                <span><UserCog aria-hidden="true" /></span>
                <div><strong>{eventLabel(item.event_type)}</strong><small>{item.resource_type} · {shortId(item.resource_id)}</small></div>
                <time>{formatDateTime(item.created_at)}</time>
              </div>
            ))}
            {!summary.recent_activity.length && <EmptyState label="暂无平台操作" />}
          </div>
        </section>
      </div>
    </div>
  );
}

function DirectorySection({ title, count, search, status, searchPlaceholder, statusOptions, extraFilters, onSearch, onStatus, onSubmit, children }: { title: string; count: number; search: string; status: string; searchPlaceholder: string; statusOptions?: Array<[string, string]>; extraFilters?: ReactNode; onSearch: (value: string) => void; onStatus: (value: string) => void; onSubmit: () => void; children: ReactNode }) {
  return <section className="platform-directory">
    <header><div><h2>{title}</h2><p>共 {count} 项</p></div></header>
    <form className="platform-filterbar" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <label className="platform-search"><Search aria-hidden="true" /><input value={search} onChange={(event) => onSearch(event.target.value)} placeholder={searchPlaceholder} /></label>
      <select value={status} onChange={(event) => onStatus(event.target.value)} aria-label="状态筛选">
        <option value="">全部状态</option>
        {(statusOptions || [["active", "启用"], ["disabled", "停用"]]).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
      </select>
      {extraFilters}
      <button className="secondary-command" type="submit">筛选</button>
    </form>
    {children}
  </section>;
}

function UserTable({ users, onSelect, showAccess }: { users: PlatformUser[]; onSelect: (user: PlatformUser) => void; showAccess?: boolean }) {
  return <div className="platform-table-shell"><table><thead><tr><th>用户</th><th>工作区访问</th>{showAccess && <th>平台角色</th>}<th>账号状态</th><th>最近登录</th><th aria-label="操作" /></tr></thead><tbody>{users.map((item) => <tr key={item.user_id}><td><button className="platform-entity-link" onClick={() => onSelect(item)}><strong>{item.display_name}</strong><small>{item.email || shortId(item.user_id)}</small></button></td><td><WorkspaceAccessCount user={item} /></td>{showAccess && <td>{item.platform_roles.length ? item.platform_roles.map(platformRoleLabel).join("、") : "无"}</td>}<td><StatusBadge value={item.status} /></td><td>{item.last_login_at ? formatDateTime(item.last_login_at) : "从未登录"}</td><td><button className="icon-command" title="查看用户" aria-label={`查看 ${item.display_name}`} onClick={() => onSelect(item)}><ChevronRight aria-hidden="true" /></button></td></tr>)}</tbody></table>{!users.length && <EmptyState label="没有匹配的用户" />}</div>;
}

function SiteDomain({ site }: { site: PlatformSite }) {
  return <a className="platform-site-domain" href={site.base_url} target="_blank" rel="noreferrer" title={site.base_url}><Globe2 aria-hidden="true" /><span>{siteHostname(site.base_url)}</span><ExternalLink aria-hidden="true" /></a>;
}

function SiteManagers({ site }: { site: PlatformSite }) {
  const names = site.manager_names.length ? site.manager_names : site.manager_emails;
  if (!names.length) return <AttentionText>未分配</AttentionText>;
  return <div className="platform-site-managers" title={[...site.manager_names, ...site.manager_emails].join(" · ")}><strong>{names.join("、")}</strong>{site.manager_emails.length > 0 && <small>{site.manager_emails.join("、")}</small>}</div>;
}

function TenantOwners({ tenant }: { tenant: PlatformTenant }) {
  const names = tenant.owner_names.length ? tenant.owner_names : tenant.owner_emails;
  if (!names.length) return <AttentionText>未分配</AttentionText>;
  return <div className="platform-site-managers" title={[...tenant.owner_names, ...tenant.owner_emails].join(" · ")}><strong>{names.join("、")}</strong>{tenant.owner_emails.length > 0 && <small>{tenant.owner_emails.join("、")}</small>}{names.length > 1 && <small>{names.length} 位负责人</small>}</div>;
}

function DrawerSiteItem({ site }: { site: PlatformSite }) {
  return <article className="drawer-site-item">
    <header><div><strong>{site.name}</strong><small>{site.site_id}</small></div><StatusBadge value={site.status} /></header>
    <SiteDomain site={site} />
    <dl>
      <div><dt>负责人</dt><dd><SiteManagers site={site} /></dd></div>
      <div><dt>验证</dt><dd><VerificationBadge value={site.verification_status} /></dd></div>
      <div><dt>知识发布</dt><dd><KnowledgeBadge value={site.knowledge_publication_state} /></dd></div>
      <div><dt>更新时间</dt><dd>{formatDateTime(site.updated_at)}</dd></div>
    </dl>
  </article>;
}

function DirectoryLoadState({ loading, error, empty, emptyLabel, onRetry, compact = false }: { loading: boolean; error: string; empty: boolean; emptyLabel: string; onRetry: () => void; compact?: boolean }) {
  if (loading) return <div className={`platform-directory-state loading${compact ? " compact" : ""}`}><LoaderCircle aria-hidden="true" /><span>正在加载网站数据…</span></div>;
  if (error) return <div className={`platform-directory-state error${compact ? " compact" : ""}`}><AlertTriangle aria-hidden="true" /><div><strong>网站数据加载失败</strong><small>{error}</small></div><button className="secondary-command" onClick={onRetry}>重试</button></div>;
  if (empty) return <div className={`platform-directory-state empty${compact ? " compact" : ""}`}><Globe2 aria-hidden="true" /><span>{emptyLabel}</span></div>;
  return null;
}

function WorkspaceDrawer({ tenant, members, sites, siteTotal, siteCursor, sitesLoading, sitesError, canOperate, onClose, onAddMember, onMembershipStatus, onRetrySites, onLoadMoreSites }: { tenant: PlatformTenant; members: PlatformMembership[]; sites: PlatformSite[]; siteTotal: number; siteCursor: string | null; sitesLoading: boolean; sitesError: string; canOperate: boolean; onClose: () => void; onAddMember: () => void; onMembershipStatus: (member: PlatformMembership, status: "active" | "disabled") => void; onRetrySites: () => void; onLoadMoreSites: () => void }) {
  const activeMembers = members.filter((member) => member.status === "active");
  const disabledMembers = members.filter((member) => member.status !== "active");
  return <Drawer title={tenant.name} subtitle={tenant.tenant_id} onClose={onClose}>
    <div className="workspace-detail-stats"><div><span>启用网站</span><strong>{tenant.site_count}</strong></div><div><span>停用网站</span><strong>{tenant.disabled_site_count}</strong></div><div><span>未验证</span><strong>{tenant.unverified_site_count}</strong></div><div><span>额度占用</span><strong>{tenant.site_limit ? `${tenant.site_quota_used}/${tenant.site_limit}` : "未配置"}</strong></div></div>
    <section className="drawer-section"><h3>工作区概况</h3><dl className="detail-list"><div><dt>所有者</dt><dd><TenantOwners tenant={tenant} /></dd></div><div><dt>状态</dt><dd><StatusBadge value={tenant.status} /></dd></div><div><dt>成员</dt><dd>{tenant.member_count} 启用{tenant.disabled_member_count > 0 ? `，${tenant.disabled_member_count} 停用` : ""}</dd></div><div><dt>套餐</dt><dd>{planLabel(tenant.plan_id)}</dd></div><div><dt>创建时间</dt><dd>{formatDateTime(tenant.created_at)}</dd></div><div><dt>最近活动</dt><dd>{tenant.last_activity_at ? formatDateTime(tenant.last_activity_at) : "暂无"}</dd></div></dl></section>
    <section className="drawer-section">
      <header><h3>绑定网站 ({siteTotal})</h3><span className="read-only-label">平台只读</span></header>
      <div className="drawer-site-list">
        {sites.map((site) => <DrawerSiteItem key={`${site.tenant_id}:${site.site_id}`} site={site} />)}
        <DirectoryLoadState loading={sitesLoading} error={sitesError} empty={!sites.length} emptyLabel="该工作区尚未绑定网站" onRetry={onRetrySites} compact />
      </div>
      {siteCursor && !sitesLoading && <LoadMore loaded={sites.length} total={siteTotal} onClick={onLoadMoreSites} />}
    </section>
    <section className="drawer-section"><header><h3>成员访问</h3>{canOperate && <button className="secondary-command" onClick={onAddMember}><UserPlus aria-hidden="true" />添加成员</button>}</header>
      <MembershipGroup title={`当前成员 (${activeMembers.length})`} members={activeMembers} emptyLabel="暂无启用成员" canOperate={canOperate} onMembershipStatus={onMembershipStatus} />
      {disabledMembers.length > 0 && <MembershipGroup title={`已停用成员 (${disabledMembers.length})`} members={disabledMembers} emptyLabel="暂无已停用成员" canOperate={canOperate} onMembershipStatus={onMembershipStatus} />}
    </section>
  </Drawer>;
}

function UserDrawer({ user, onClose }: { user: PlatformUser; onClose: () => void }) {
  return <Drawer title={user.display_name} subtitle={user.email || user.user_id} onClose={onClose}><section className="drawer-section"><h3>账号概况</h3><dl className="detail-list"><div><dt>账号状态</dt><dd><StatusBadge value={user.status} /></dd></div><div><dt>当前工作区</dt><dd>{user.workspace_count}</dd></div><div><dt>已停用关系</dt><dd>{user.disabled_workspace_count}</dd></div><div><dt>平台角色</dt><dd>{user.platform_roles.length ? user.platform_roles.map(platformRoleLabel).join("、") : "无"}</dd></div><div><dt>最近登录</dt><dd>{user.last_login_at ? formatDateTime(user.last_login_at) : "从未登录"}</dd></div></dl></section><WorkspaceNameSection title="可访问工作区" names={user.workspace_names} emptyLabel="当前没有可访问的工作区" /><WorkspaceNameSection title="已停用的工作区关系" names={user.disabled_workspace_names} emptyLabel="没有已停用的工作区关系" muted /></Drawer>;
}

function WorkspaceAccessCount({ user }: { user: PlatformUser }) {
  return <div className="workspace-access-count"><strong>当前 {user.workspace_count}</strong>{user.disabled_workspace_count > 0 && <small>已停用 {user.disabled_workspace_count}</small>}<small>{user.workspace_names.slice(0, 2).join("、")}</small></div>;
}

function MembershipGroup({ title, members, emptyLabel, canOperate, onMembershipStatus }: { title: string; members: PlatformMembership[]; emptyLabel: string; canOperate: boolean; onMembershipStatus: (member: PlatformMembership, status: "active" | "disabled") => void }) {
  return <div className="drawer-member-group"><h4>{title}</h4><div className="drawer-member-list">{members.map((member) => <div key={member.membership_id}><div><strong>{member.display_name || shortId(member.user_id)}</strong><small>{member.email || member.roles.map(formatRoleLabel).join("、")}</small></div><StatusBadge value={member.status} />{canOperate && <button className={member.status === "active" ? "danger-text-button" : "secondary-command"} onClick={() => onMembershipStatus(member, member.status === "active" ? "disabled" : "active")}>{member.status === "active" ? "停用" : "重新启用"}</button>}</div>)}{!members.length && <EmptyState label={emptyLabel} />}</div></div>;
}

function WorkspaceNameSection({ title, names, emptyLabel, muted = false }: { title: string; names: string[]; emptyLabel: string; muted?: boolean }) {
  if (!names.length && muted) return null;
  return <section className={`drawer-section${muted ? " muted-section" : ""}`}><h3>{title}</h3><div className="simple-name-list">{names.map((name) => <span key={name}><Building2 aria-hidden="true" />{name}</span>)}{!names.length && <EmptyState label={emptyLabel} />}</div></section>;
}

function OnboardingDialog({ busy, onClose, onCreated }: { busy: boolean; onClose: () => void; onCreated: () => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [expires, setExpires] = useState(72);
  const [siteLimit, setSiteLimit] = useState(1);
  const [result, setResult] = useState<CreatedWorkspaceOnboardingCode | null>(null);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    try { const created = await createWorkspaceOnboardingCode({ target_email: email, expires_in_hours: expires, site_limit: siteLimit }); setResult(created); await onCreated(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "生成注册链接失败"); }
  }
  async function copy(value: string) { try { await navigator.clipboard.writeText(value); } catch { setError("浏览器未允许复制，请手动选择链接。"); } }
  return <Modal title={result ? "注册链接已生成" : "开通独立工作区"} onClose={onClose}><form className="platform-dialog-form" onSubmit={submit}>{!result ? <><label><span>企业邮箱</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="client@example.com" autoFocus required /></label><label><span>有效时间</span><select value={expires} onChange={(event) => setExpires(Number(event.target.value))}><option value={24}>24 小时</option><option value={72}>72 小时</option><option value={168}>7 天</option></select></label><details className="advanced-fields"><summary>高级设置</summary><label><span>站点额度</span><input type="number" min={1} max={100} value={siteLimit} onChange={(event) => setSiteLimit(Number(event.target.value))} required /></label></details>{error && <div className="dialog-error">{error}</div>}<footer><button type="button" className="secondary-command" onClick={onClose}>取消</button><button className="platform-primary-button" disabled={busy || !email}><Plus aria-hidden="true" />生成注册链接</button></footer></> : <><div className="one-time-warning"><AlertTriangle aria-hidden="true" /><span>邀请码只显示这一次。关闭窗口前请复制注册链接。</span></div><label><span>企业邀请码</span><div className="copy-field"><input value={result.enrollment_code} readOnly /><button type="button" title="复制邀请码" aria-label="复制邀请码" onClick={() => void copy(result.enrollment_code)}><Copy /></button></div></label><label><span>注册链接</span><div className="copy-field"><input value={result.signup_url} readOnly /><button type="button" title="复制注册链接" aria-label="复制注册链接" onClick={() => void copy(result.signup_url)}><Copy /></button></div></label>{error && <div className="dialog-error">{error}</div>}<footer><button type="button" className="secondary-command" onClick={() => void copy(result.signup_url)}><Copy aria-hidden="true" />复制链接</button><button type="button" className="platform-primary-button" onClick={() => window.open(result.signup_url, "_blank", "noopener,noreferrer")}><ExternalLink aria-hidden="true" />打开注册页面</button></footer></>}</form></Modal>;
}

function DirectCreateDialog({ busy, onClose, onSubmit }: { busy: boolean; onClose: () => void; onSubmit: (tenantId: string, name: string) => Promise<void> }) {
  const [tenantId, setTenantId] = useState(""); const [name, setName] = useState("");
  return <Modal title="直接创建工作区" onClose={onClose}><form className="platform-dialog-form" onSubmit={(event) => { event.preventDefault(); void onSubmit(tenantId, name); }}><div className="danger-boundary-note"><AlertTriangle aria-hidden="true" /><span>此操作不会创建所有者账号，可能产生无人管理的工作区。</span></div><label><span>工作区标识</span><input value={tenantId} onChange={(event) => setTenantId(event.target.value)} pattern="[a-z0-9][a-z0-9-]{2,99}" placeholder="brand-cn" required autoFocus /></label><label><span>工作区名称</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label><footer><button type="button" className="secondary-command" onClick={onClose}>取消</button><button className="danger-command" disabled={busy}>确认直接创建</button></footer></form></Modal>;
}

function MembershipDialog({ tenant, busy, onClose, onSubmit }: { tenant: PlatformTenant; busy: boolean; onClose: () => void; onSubmit: (user: PlatformUser, role: string) => Promise<void> }) {
  const [query, setQuery] = useState(""); const [results, setResults] = useState<PlatformUser[]>([]); const [selected, setSelected] = useState<PlatformUser | null>(null); const [role, setRole] = useState("support_agent"); const [searching, setSearching] = useState(false);
  async function search(event: FormEvent) { event.preventDefault(); setSearching(true); try { setResults((await queryPlatformUsers({ q: query, limit: 20 })).items); } finally { setSearching(false); } }
  return <Modal title={`添加成员到 ${tenant.name}`} onClose={onClose}><div className="platform-dialog-form"><form className="dialog-search" onSubmit={search}><Search aria-hidden="true" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入姓名或邮箱" autoFocus /><button className="secondary-command" disabled={searching}>搜索</button></form><div className="user-pick-list">{results.map((item) => <button key={item.user_id} className={selected?.user_id === item.user_id ? "selected" : ""} onClick={() => setSelected(item)}><span>{initials(item.display_name)}</span><div><strong>{item.display_name}</strong><small>{item.email || item.user_id}</small></div>{selected?.user_id === item.user_id && <CheckCircle2 aria-hidden="true" />}</button>)}{!results.length && <EmptyState label="搜索平台用户后进行选择" />}</div><label><span>工作区角色</span><select value={role} onChange={(event) => setRole(event.target.value)}>{workspaceRoleOptions.map((item) => <option value={item} key={item}>{formatRoleLabel(item)}</option>)}</select></label><footer><button className="secondary-command" onClick={onClose}>取消</button><button className="platform-primary-button" disabled={busy || !selected} onClick={() => selected && void onSubmit(selected, role)}><UserPlus aria-hidden="true" />添加成员</button></footer></div></Modal>;
}

function PlatformRoleDialog({ user, busy, onClose, onSubmit }: { user: PlatformUser; busy: boolean; onClose: () => void; onSubmit: (role: string) => Promise<void> }) {
  const [role, setRole] = useState("platform_operator");
  return <Modal title={`授予 ${user.display_name} 平台角色`} onClose={onClose}><form className="platform-dialog-form" onSubmit={(event) => { event.preventDefault(); void onSubmit(role); }}><div className="access-boundary-note"><ShieldCheck aria-hidden="true" /><span>平台角色不会自动获得任何客户会话访问权。</span></div><div className="role-choice-list">{platformRoleOptions.map(([value, label, description]) => <label className={role === value ? "selected" : ""} key={value}><input type="radio" name="platform-role" value={value} checked={role === value} onChange={() => setRole(value)} /><span><strong>{label}</strong><small>{description}</small></span></label>)}</div><footer><button type="button" className="secondary-command" onClick={onClose}>取消</button><button className="platform-primary-button" disabled={busy}><KeyRound aria-hidden="true" />确认授予</button></footer></form></Modal>;
}

function ConfirmDialog({ action, busy, onCancel, onConfirm }: { action: NonNullable<ConfirmAction>; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const content = action.kind === "revoke-code" ? ["撤销开通链接", `${action.record.target_email} 将无法再使用当前链接开通工作区。`, "确认撤销"] : action.kind === "revoke-role" ? ["撤销平台角色", `${action.user.display_name} 的${platformRoleLabel(action.role)}权限将立即失效，现有会话会被撤销。`, "确认撤销"] : [action.status === "active" ? "重新启用成员" : "停用工作区成员", `${action.member.display_name || action.member.email || action.member.user_id} 在此工作区的访问权限将${action.status === "active" ? "恢复" : "立即失效"}。`, action.status === "active" ? "确认启用" : "确认停用"];
  return <Modal title={content[0]} onClose={onCancel}><div className="confirmation-content"><AlertTriangle aria-hidden="true" /><p>{content[1]}</p><footer><button className="secondary-command" onClick={onCancel}>取消</button><button className="danger-command" disabled={busy} onClick={onConfirm}>{content[2]}</button></footer></div></Modal>;
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) { return <div className="platform-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="platform-modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-command" title="关闭" aria-label="关闭" onClick={onClose}><X aria-hidden="true" /></button></header>{children}</section></div>; }
function Drawer({ title, subtitle, onClose, children }: { title: string; subtitle: string; onClose: () => void; children: ReactNode }) { return <div className="platform-overlay drawer-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><aside className="platform-drawer" aria-label={title}><header><div><h2>{title}</h2><p>{subtitle}</p></div><button className="icon-command" title="关闭" aria-label="关闭" onClick={onClose}><X /></button></header><div className="drawer-content">{children}</div></aside></div>; }
function LoadMore({ loaded, total, onClick }: { loaded: number; total: number; onClick: () => void }) { return <div className="platform-load-more"><button className="secondary-command" onClick={onClick}>加载更多（{loaded}/{total}）</button></div>; }
function EmptyState({ label }: { label: string }) { return <div className="platform-empty"><Search aria-hidden="true" /><span>{label}</span></div>; }
function AttentionText({ children }: { children: ReactNode }) { return <span className="attention-text"><AlertTriangle aria-hidden="true" />{children}</span>; }
function StatusBadge({ value }: { value: string }) { const active = value === "active"; return <span className={`platform-status ${active ? "active" : "disabled"}`}>{active ? "启用" : "停用"}</span>; }
function VerificationBadge({ value }: { value: string }) { const states: Record<string, [string, string]> = { verified: ["已验证", "success"], pending: ["待验证", "warning"], failed: ["验证失败", "danger"], expired: ["已过期", "muted"] }; const [label, tone] = states[value] || [value, "muted"]; return <span className={`platform-state-badge ${tone}`}>{label}</span>; }
function KnowledgeBadge({ value }: { value: string }) { const states: Record<string, [string, string]> = { active: ["已发布", "success"], switching: ["发布中", "progress"], recovery_required: ["需要恢复", "danger"], missing: ["未发布", "muted"] }; const [label, tone] = states[value] || [value, "muted"]; return <span className={`platform-state-badge ${tone}`}>{label}</span>; }
function OnboardingBadge({ status }: { status: PlatformOnboardingRecord["status"] }) { return <span className={`onboarding-status ${status}`}>{onboardingStatusLabel(status)}</span>; }

const onboardingStatusOptions: Array<[string, string]> = [["issued", "未开始"], ["verification_pending", "待验证"], ["completed", "已开通"], ["failed", "邮件失败"], ["expired", "已过期"], ["revoked", "已撤销"]];
function onboardingStatusLabel(status: PlatformOnboardingRecord["status"]) { return onboardingStatusOptions.find(([value]) => value === status)?.[1] || status; }
function onboardingExpiry(record: PlatformOnboardingRecord) { if (["completed", "revoked"].includes(record.status)) return record.status === "completed" ? "已完成" : "已撤销"; const remaining = new Date(record.expires_at).getTime() - Date.now(); if (remaining <= 0) return "已过期"; const hours = Math.ceil(remaining / 3_600_000); return hours < 24 ? `还剩 ${hours} 小时` : `还剩 ${Math.ceil(hours / 24)} 天`; }
function tabFromLocation(user: AdminUser): PlatformTab { const value = new URLSearchParams(window.location.search).get("tab") as PlatformTab | null; const allowed: PlatformTab[] = ["overview", "workspaces", "sites", "users", "onboarding", ...(user.platform_roles.includes("platform_owner") ? ["access" as const] : [])]; return value && allowed.includes(value) ? value : "overview"; }
function formatDateTime(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function siteHostname(value: string) { try { return new URL(value).hostname; } catch { return value; } }
function mergeSites(current: PlatformSite[], incoming: PlatformSite[]) {
  const items = new Map(current.map((site) => [`${site.tenant_id}:${site.site_id}`, site]));
  incoming.forEach((site) => items.set(`${site.tenant_id}:${site.site_id}`, site));
  return [...items.values()];
}
function shortId(value: string) { return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value; }
function initials(value: string) { return value.trim().slice(0, 2).toUpperCase() || "U"; }
function planLabel(value: string | null) { return value === "trial" ? "试用版" : value === "standard" ? "标准版" : value === "enterprise" ? "企业版" : "未配置"; }
function platformRoleLabel(value: string) { return value === "platform_owner" ? "平台所有者" : value === "platform_operator" ? "平台运营" : value === "platform_auditor" ? "平台审计" : value; }
function eventLabel(value: string) { const labels: Record<string, string> = { "tenant.provisioned": "创建工作区", "tenant_membership.created": "添加工作区成员", "tenant_membership.updated": "更新工作区成员", "platform_role.assigned": "授予平台角色", "platform_role.revoked": "撤销平台角色", "enrollment.code_issued": "生成开通链接", "enrollment.code_revoked": "撤销开通链接", "enrollment.completed": "完成独立工作区开通" }; return labels[value] || value.replaceAll("_", " ").replaceAll(".", " · "); }
