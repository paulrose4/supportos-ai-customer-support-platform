import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  conversationAction,
  createManualHandoff,
  createKnowledgeGap,
  addInternalNote,
  ApiRequestError,
  emailLogin,
  getSupportConfiguration,
  getCurrentUser,
  getInboxCounts,
  getLoginProviders,
  getWorkspace,
  listAuthWorkspaces,
  listInbox,
  listMemory,
  listPresence,
  listSites,
  login,
  logout,
  markConversationRead,
  previewInvitation,
  registerWithInvitation,
  resendSelfServiceVerification,
  requestPasswordReset,
  resetEmailPassword,
  sendAgentMessage,
  switchTenant,
  selfServiceSignup,
  updateConversationRouting,
  verifySelfServiceEmail,
} from "./api";
import {
  AudiencePage,
  pageConfiguration,
  pageFromPath,
  AutomationPage,
  ReportsPage,
  SettingsPage,
  countryDetails,
  type AudienceTab,
  type PageId,
} from "./pages";
import { WidgetConfigurationManagement } from "./customerExperiencePages";
import { KnowledgePage } from "./knowledgePage";
import { OverviewPage } from "./overviewPage";
import { PlatformAdminPage } from "./platformAdminPage";
import { Bell, Globe2, Inbox as InboxIcon, LogOut, MapPin, MessageSquare, RefreshCw, Search, UserRound } from "lucide-react";
import { helpText } from "./content/helpText";
import {
  formatAccountDisplayName,
  formatOwnership,
  formatPriority,
  formatRiskLevel,
  formatRoleLabel,
  terminology,
} from "./content/terminology";
import type {
  AdminUser,
  InboxConversation,
  InvitationPreview,
  LoginProviderConfiguration,
  MemoryItem,
  PresenceLoadState,
  RealtimeEvent,
  Site,
  VisitorPresence,
  Workspace,
  SupportConfiguration,
  TenantWorkspace,
} from "./types";

const navigationOrder: PageId[] = [
  "overview",
  "inbox",
  "audience",
  "content",
  "reports",
  "platform",
  "settings",
];

type ContentTab = "knowledge" | "widget" | "automation";

export default function App() {
  const [user, setUser] = useState<AdminUser | null>(null);
  const [authWorkspaces, setAuthWorkspaces] = useState<TenantWorkspace[]>([]);
  const [authLoading, setAuthLoading] = useState(true);
  const [page, setPage] = useState<PageId>(() => pageFromPath(window.location.pathname));
  const [audienceTab, setAudienceTab] = useState<AudienceTab>(() => audienceTabFromLocation());
  const [contentTab, setContentTab] = useState<ContentTab>(() => contentTabFromLocation());
  const [focusedCustomerId, setFocusedCustomerId] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get("customer"),
  );
  const [sites, setSites] = useState<Site[]>([]);
  const [siteId, setSiteId] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [ownershipFilter, setOwnershipFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [mineOnly, setMineOnly] = useState(false);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [priorityRiskOnly, setPriorityRiskOnly] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [clock, setClock] = useState(() => Date.now());
  const [inbox, setInbox] = useState<InboxConversation[]>([]);
  const [inboxTotal, setInboxTotal] = useState<number | null>(null);
  const [inboxNextCursor, setInboxNextCursor] = useState<string | null>(null);
  const [serverCounts, setServerCounts] = useState<{
    all: number; mine: number; waiting_human: number; sla_risk: number; unread: number; priority_risk: number; high_intent?: number; resolved: number;
  } | null>(null);
  const inboxRequestId = useRef(0);
  const [presence, setPresence] = useState<VisitorPresence[]>([]);
  const [presenceLoadState, setPresenceLoadState] = useState<PresenceLoadState>("loading");
  const [presenceUpdatedAt, setPresenceUpdatedAt] = useState<number | null>(null);
  const presenceHasSucceeded = useRef(false);
  const presenceRequestId = useRef(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileInboxView, setMobileInboxView] = useState<"list" | "conversation" | "customer">("list");
  const selectedIdRef = useRef<string | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [realtimeState, setRealtimeState] = useState<"connecting" | "live" | "offline">(
    "offline",
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notificationsEnabled, setNotificationsEnabled] = useState(
    () => typeof Notification !== "undefined" && Notification.permission === "granted",
  );
  const [supportConfiguration, setSupportConfiguration] = useState<SupportConfiguration>({
    queues: [],
    agents: [],
    canned_replies: [],
  });

  const navigate = useCallback((nextPage: PageId) => {
    const path = nextPage === "audience"
      ? `${pageConfiguration[nextPage].path}?tab=${audienceTab}`
      : nextPage === "content"
        ? `${pageConfiguration[nextPage].path}?tab=${contentTab}`
        : pageConfiguration[nextPage].path;
    window.history.pushState({}, "", path);
    setPage(nextPage);
  }, [audienceTab, contentTab]);

  useEffect(() => {
    const onPopState = () => {
      setPage(pageFromPath(window.location.pathname));
      setAudienceTab(audienceTabFromLocation());
      setContentTab(contentTabFromLocation());
      setFocusedCustomerId(new URLSearchParams(window.location.search).get("customer"));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const legacyPath = window.location.pathname;
    if (legacyPath === "/tickets") {
      setOwnershipFilter("queued");
      window.history.replaceState({}, "", "/inbox?view=waiting_human");
    } else if (["/visitors", "/customers"].includes(legacyPath)) {
      window.history.replaceState({}, "", `/audience?tab=${audienceTabFromLocation()}`);
    } else if (["/automation", "/knowledge"].includes(legacyPath)) {
      window.history.replaceState({}, "", `/content?tab=${contentTabFromLocation()}`);
    }
  }, []);

  useEffect(() => {
    if (!user) {
      setAuthWorkspaces([]);
      return;
    }
    listAuthWorkspaces().then(setAuthWorkspaces).catch(() => setAuthWorkspaces([]));
  }, [user]);

  useEffect(() => {
    if (!user || page !== "content") return;
    const canManageWidget = user.scopes.includes("sites:manage");
    const canReadAutomation = user.scopes.includes("automation:read") || canManageWidget;
    if ((contentTab === "widget" && !canManageWidget) || (contentTab === "automation" && !canReadAutomation)) {
      setContentTab("knowledge");
      window.history.replaceState({}, "", "/content?tab=knowledge");
    }
  }, [contentTab, page, user]);

  useEffect(() => {
    if (!user || page !== "platform" || user.platform_roles.length > 0) return;
    window.history.replaceState({}, "", "/");
    setPage("overview");
  }, [page, user]);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setAuthLoading(false));
  }, []);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const refreshInbox = useCallback(async () => {
    if (!user) return;
    const requestId = ++inboxRequestId.current;
    const result = await listInbox({
      limit: 50,
      siteId: siteId || undefined,
      status: statusFilter || undefined,
      ownership: ownershipFilter || undefined,
      mineOnly,
      priority: priorityFilter || undefined,
      unreadOnly,
      search: searchTerm.trim() || undefined,
      slaRisk: overdueOnly,
      priorityRisk: priorityRiskOnly,
    });
    if (requestId !== inboxRequestId.current) return;
    setInbox(result.items);
    setInboxTotal(result.total);
    setInboxNextCursor(result.next_cursor);
    const counts = await getInboxCounts(siteId || undefined);
    if (requestId === inboxRequestId.current) setServerCounts(counts);
  }, [priorityRiskOnly, mineOnly, overdueOnly, ownershipFilter, priorityFilter, searchTerm, siteId, statusFilter, unreadOnly, user]);

  const loadMoreInbox = useCallback(async () => {
    if (!user || !inboxNextCursor) return;
    const result = await listInbox({
      limit: 50,
      cursor: inboxNextCursor,
      siteId: siteId || undefined,
      status: statusFilter || undefined,
      ownership: ownershipFilter || undefined,
      mineOnly,
      priority: priorityFilter || undefined,
      unreadOnly,
      search: searchTerm.trim() || undefined,
      slaRisk: overdueOnly,
      priorityRisk: priorityRiskOnly,
    });
    setInbox((current) => [...current, ...result.items]);
    setInboxTotal(result.total);
    setInboxNextCursor(result.next_cursor);
  }, [inboxNextCursor, mineOnly, overdueOnly, ownershipFilter, priorityFilter, priorityRiskOnly, searchTerm, siteId, statusFilter, unreadOnly, user]);

  const refreshSites = useCallback(async () => {
    if (!user) return;
    setSites(await listSites());
  }, [user]);

  const refreshSupportConfiguration = useCallback(async () => {
    if (!user) return;
    setSupportConfiguration(await getSupportConfiguration());
  }, [user]);

  useEffect(() => {
    refreshSites().catch((reason: Error) => setError(reason.message));
  }, [refreshSites]);

  useEffect(() => {
    refreshSupportConfiguration().catch((reason: Error) => setError(reason.message));
  }, [refreshSupportConfiguration]);

  useEffect(() => {
    refreshInbox().catch((reason: Error) => setError(reason.message));
  }, [refreshInbox]);

  const refreshPresence = useCallback(async () => {
    if (!user) return;
    const requestId = ++presenceRequestId.current;
    try {
      const items = await listPresence(300);
      if (requestId !== presenceRequestId.current) return;
      setPresence(items);
      setPresenceUpdatedAt(Date.now());
      setPresenceLoadState("ready");
      presenceHasSucceeded.current = true;
    } catch (_reason) {
      if (requestId !== presenceRequestId.current) return;
      setPresenceLoadState(presenceHasSucceeded.current ? "stale" : "error");
    }
  }, [user]);

  useEffect(() => {
    if (!user) {
      presenceRequestId.current += 1;
      setPresence([]);
      setPresenceUpdatedAt(null);
      setPresenceLoadState("loading");
      presenceHasSucceeded.current = false;
      return;
    }
    void refreshPresence();
    const timer = window.setInterval(refreshPresence, 15_000);
    return () => {
      window.clearInterval(timer);
    };
  }, [refreshPresence, user]);

  const filteredInbox = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();
    return inbox.filter((item) => {
      if (siteId && item.site_id !== siteId) return false;
      if (statusFilter && item.status !== statusFilter) return false;
      if (ownershipFilter && item.ownership_mode !== ownershipFilter) return false;
      if (priorityFilter && item.priority !== priorityFilter) return false;
      if (mineOnly && item.assigned_agent_id !== user?.user_id) return false;
      if (unreadOnly && item.unread_count === 0) return false;
      if (overdueOnly && !isSlaOverdue(item.sla_due_at, clock)) return false;
      // Priority-risk and SLA filters are applied by the server before pagination.
      if (!normalizedSearch) return true;
      return `${item.customer_display_name || ""} ${item.customer_id || ""} ${item.last_message_preview || ""} ${item.tags.join(" ")}`
        .toLowerCase()
        .includes(normalizedSearch);
    }).sort((left, right) => compareInboxPriority(left, right, clock));
  }, [clock, inbox, mineOnly, overdueOnly, ownershipFilter, priorityFilter, priorityRiskOnly, searchTerm, siteId, statusFilter, unreadOnly, user]);

  useEffect(() => {
    if (page !== "inbox") return;
    setSelectedId((current) => {
      if (current && filteredInbox.some((item) => item.conversation_id === current)) return current;
      return filteredInbox[0]?.conversation_id ?? null;
    });
  }, [filteredInbox, page]);

  const refreshWorkspace = useCallback(async (conversationId: string) => {
    setLoadingWorkspace(true);
    try {
      let next = await getWorkspace(conversationId);
      if (next.conversation.unread_count > 0) {
        next = await markConversationRead(conversationId);
      }
      setWorkspace(next);
      setMemory(next.conversation.customer_id ? await listMemory(next.conversation.customer_id) : []);
    } finally {
      setLoadingWorkspace(false);
    }
  }, []);

  useEffect(() => {
    if (page !== "inbox" || !selectedId) {
      if (!selectedId) {
        setWorkspace(null);
        setMemory([]);
      }
      return;
    }
    refreshWorkspace(selectedId).catch((reason: Error) => setError(reason.message));
  }, [page, refreshWorkspace, selectedId]);

  useEffect(() => {
    if (!user) return;
    let closed = false;
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let retryDelay = 1000;
    const connect = () => {
      setRealtimeState("connecting");
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/v1/ws/support`);
      socket.onopen = () => {
        retryDelay = 1000;
        setRealtimeState("live");
      };
      socket.onmessage = (message) => {
        if (message.data === "pong") return;
        const event = JSON.parse(message.data) as RealtimeEvent;
        if (!["conversation", "customer_memory"].includes(event.resource_type)) return;
        if (event.payload.kind === "handoff" && typeof Notification !== "undefined" && Notification.permission === "granted") {
          new Notification("新的人工处理请求", { body: "有客户请求需要人工处理，请在回复时限内接管。" });
        }
        refreshInbox().catch(() => undefined);
        if (event.resource_type === "conversation" && event.resource_id === selectedIdRef.current) {
          refreshWorkspace(event.resource_id).catch(() => undefined);
        }
      };
      socket.onclose = (event) => {
        setRealtimeState("offline");
        if (closed) return;
        const refreshSession = () => {
          getCurrentUser().catch((reason: unknown) => {
            if (
              !closed &&
              reason instanceof ApiRequestError &&
              (reason.status === 401 || reason.status === 403)
            ) {
              setUser(null);
            }
          });
        };
        if (event.code === 1008) {
          refreshSession();
          return;
        }
        refreshSession();
        retryTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 30_000);
      };
    };
    connect();
    return () => {
      closed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [refreshInbox, refreshWorkspace, user]);

  const counts = useMemo(() => {
    const countInbox = siteId ? inbox.filter((item) => item.site_id === siteId) : inbox;
    const countPresence = siteId ? presence.filter((item) => item.site_id === siteId) : presence;
    return {
      all: serverCounts?.all ?? countInbox.length,
      online: countPresence.filter(
        (item) => Date.now() - new Date(item.last_seen_at).getTime() <= 60_000,
      ).length,
      queued: serverCounts?.waiting_human ?? countInbox.filter((item) => item.ownership_mode === "queued").length,
      mine: serverCounts?.mine ?? countInbox.filter((item) => item.assigned_agent_id === user?.user_id).length,
      resolved: serverCounts?.resolved ?? countInbox.filter((item) => item.status === "resolved").length,
      active: countInbox.filter(
        (item) =>
          item.last_message_at && Date.now() - new Date(item.last_message_at).getTime() < 30 * 60_000,
      ).length,
      customers: new Set(countInbox.map((item) => item.customer_id).filter(Boolean)).size,
      overdue: serverCounts?.sla_risk ?? countInbox.filter((item) => isSlaOverdue(item.sla_due_at, clock)).length,
      unread: serverCounts?.unread ?? countInbox.filter((item) => item.unread_count > 0).length,
      priorityRisk: serverCounts?.priority_risk
        ?? serverCounts?.high_intent
        ?? countInbox.filter(
          (item) => item.risk_level >= 2 || ["high", "urgent"].includes(item.priority),
        ).length,
    };
  }, [clock, inbox, presence, serverCounts, siteId, user]);

  const openConversation = useCallback(
    (conversationId: string) => {
      setStatusFilter("");
      setOwnershipFilter("");
      setMineOnly(false);
      setUnreadOnly(false);
      setOverdueOnly(false);
      setPriorityRiskOnly(false);
      setPriorityFilter("");
      setSearchTerm("");
      setSelectedId(conversationId);
      setMobileInboxView("conversation");
      navigate("inbox");
    },
    [navigate],
  );

  const runAction = async (action: "takeover" | "release-to-ai" | "resolve") => {
    if (!workspace) return;
    setError(null);
    try {
      const next = await conversationAction(workspace.conversation.conversation_id, action);
      setWorkspace(next);
      await refreshInbox();
      setNotice(
        action === "takeover" ? "会话已接管" : action === "resolve" ? "会话已解决" : "已交还 AI",
      );
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const enableNotifications = async () => {
    if (typeof Notification === "undefined") {
      setError("当前浏览器不支持桌面通知");
      return;
    }
    const permission = await Notification.requestPermission();
    setNotificationsEnabled(permission === "granted");
    setNotice(permission === "granted" ? "人工请求桌面通知已开启" : "桌面通知未获授权");
  };

  const updateRouting = async (values: {
    assigned_agent_id: string | null;
    queue_id: string | null;
    priority: "low" | "normal" | "high" | "urgent";
    tags: string[];
  }) => {
    if (!workspace) return;
    try {
      const next = await updateConversationRouting(
        workspace.conversation.conversation_id,
        values,
      );
      setWorkspace(next);
      await refreshInbox();
      setNotice("会话分配设置已更新");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const addNote = async (content: string) => {
    if (!workspace) return;
    try {
      setWorkspace(await addInternalNote(workspace.conversation.conversation_id, content));
      setNotice("内部备注已保存");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const createHandoff = async () => {
    if (!workspace) return;
    const conversation = workspace.conversation;
    try {
      setWorkspace(await createManualHandoff(conversation.conversation_id, {
        summary: workspace.handoff_context?.unresolved_question || conversation.last_message_preview || "人工客服继续跟进",
        queue_id: conversation.queue_id,
        priority: conversation.priority,
      }));
      await refreshInbox();
      setNotice("会话已转入等待人工处理");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const markKnowledgeGap = async (category: "missing_knowledge" | "incorrect_answer") => {
    if (!workspace) return;
    const initial = workspace.handoff_context?.unresolved_question || workspace.conversation.last_message_preview || "";
    const summary = window.prompt(category === "incorrect_answer" ? "请描述 AI 回答错误：" : "请描述缺失的知识：", initial);
    if (!summary) return;
    try {
      await createKnowledgeGap(workspace.conversation.conversation_id, category, summary);
      setNotice(category === "incorrect_answer" ? "已记录 AI 回答错误" : "已记录知识缺口");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const showAudienceTab = (tab: AudienceTab, customerId: string | null = null) => {
    setAudienceTab(tab);
    setFocusedCustomerId(customerId);
    setPage("audience");
    const query = new URLSearchParams({ tab });
    if (customerId) query.set("customer", customerId);
    window.history.pushState({}, "", `/audience?${query.toString()}`);
  };

  const showContentTab = (tab: ContentTab) => {
    setContentTab(tab);
    setPage("content");
    window.history.pushState({}, "", `/content?tab=${tab}`);
  };

  if (authLoading) return <LoadingScreen />;
  if (!user) return <LoginScreen onAuthenticated={setUser} />;

  const changeWorkspace = async (tenantId: string) => {
    if (!tenantId || tenantId === user.tenant_id) return;
    setError(null);
    try {
      await switchTenant(tenantId);
      window.location.assign("/");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  const pageConfig = pageConfiguration[page];
  const accountDisplayName = formatAccountDisplayName(user.display_name, user.roles);
  const accountRoleLabels = user.roles.length > 0 ? user.roles.map(formatRoleLabel).join(" · ") : "未分配角色";
  const scopedInbox = siteId ? inbox.filter((item) => item.site_id === siteId) : inbox;
  const scopedPresence = siteId ? presence.filter((item) => item.site_id === siteId) : presence;
  const commonPageProps = {
    inbox: scopedInbox,
    sites,
    presence: scopedPresence,
    presenceLoadState,
    presenceUpdatedAt,
    onRetryPresence: () => void refreshPresence(),
    user,
    onOpenConversation: openConversation,
  };

  return (
    <div className={`app-shell ${page === "inbox" ? "with-section-sidebar" : "single-sidebar"}`}>
      <aside className="primary-sidebar">
        <img className="brand-mark" src="/supportos-logo.svg" alt="SupportOS" />
        <div className="nav-stack">
          {navigationOrder.map((pageId) => {
            const item = pageConfiguration[pageId];
            const permitted = pageId === "platform"
              ? user.platform_roles.length > 0
              : user.scopes.includes(item.scope);
            if (pageId === "platform" && !permitted) return null;
            const Icon = item.icon;
            return <button className={`nav-icon ${page === pageId ? "active" : ""}`} key={pageId} title={permitted ? item.label : `无权限：${item.scope}`} aria-label={permitted ? item.label : `无权限：${item.scope}`} disabled={!permitted} onClick={() => navigate(pageId)}><Icon aria-hidden="true" /><small>{item.label}</small></button>;
          })}
        </div>
        <div className="agent-avatar" title={accountDisplayName}>{initials(accountDisplayName)}<i /></div>
        <button className="sidebar-logout" title="退出登录" aria-label="退出登录" onClick={() => logout().finally(() => setUser(null))}><LogOut aria-hidden="true" /></button>
      </aside>

      {page === "inbox" && <SectionSidebar
        page={page}
        counts={counts}
        sites={sites}
        siteId={siteId}
        tenantId={user.tenant_id}
        realtimeState={realtimeState}
        activeInboxFilter={overdueOnly ? "SLA 风险" : mineOnly ? "待我处理" : unreadOnly ? "未读" : priorityRiskOnly ? "风险/优先级" : ownershipFilter === "queued" ? "等待人工" : statusFilter === "resolved" ? "已解决" : "全部"}
        onInboxFilter={(filter) => {
          setStatusFilter(filter.status || "");
          setOwnershipFilter(filter.ownership || "");
          setMineOnly(Boolean(filter.mine));
          setOverdueOnly(Boolean(filter.overdue));
          setUnreadOnly(Boolean(filter.unread));
          setPriorityRiskOnly(Boolean(filter.priorityRisk));
          setPriorityFilter("");
          navigate("inbox");
        }}
        onLogout={() => logout().finally(() => setUser(null))}
      />}

      <main className="workspace-shell">
        <header className="topbar">
          <div><p>{pageConfig.eyebrow}</p><h1>{page === "inbox" ? "统一收件箱" : pageConfig.label}</h1></div>
          <div className="topbar-actions">
            {page === "inbox" && <button className={`notification-button ${notificationsEnabled ? "enabled" : ""}`} onClick={() => void enableNotifications()} title="人工请求桌面通知"><Bell aria-hidden="true" /> <span>{notificationsEnabled ? "通知已开" : "开启通知"}</span></button>}
            {page !== "platform" && <select value={siteId} onChange={(event) => setSiteId(event.target.value)}><option value="">全部站点</option>{sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.name}</option>)}</select>}
            {page !== "platform" && authWorkspaces.length > 1 && <select className="workspace-switcher" aria-label="切换工作区" value={user.tenant_id} onChange={(event) => void changeWorkspace(event.target.value)}>{authWorkspaces.map((item) => <option value={item.tenant_id} key={item.tenant_id}>{item.name}</option>)}</select>}
            <div className="user-chip"><span>{initials(accountDisplayName)}</span><div><strong>{accountDisplayName}</strong><small>{accountRoleLabels}</small></div></div>
          </div>
        </header>

        {(error || notice) && <div className={`toast ${error ? "error" : "success"}`} onClick={() => { setError(null); setNotice(null); }}>{error || notice}</div>}

        {page === "overview" && <OverviewPage inbox={scopedInbox} counts={serverCounts} presence={scopedPresence} sites={sites} selectedSiteId={siteId} onOpenConversation={openConversation} onShowQueue={(filter) => {
          setStatusFilter("");
          setOwnershipFilter(filter === "queued" ? "queued" : "");
          setMineOnly(filter === "mine");
          setUnreadOnly(filter === "unread");
          setOverdueOnly(filter === "overdue");
          setPriorityRiskOnly(false);
          setPriorityFilter("");
          navigate("inbox");
        }} />}
        {page === "inbox" && <>
          <div className="filterbar">
            <div className="search-box"><Search aria-hidden="true" size={18} /> <input value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} placeholder="搜索客户或消息…" /></div>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部状态</option><option value="open">进行中</option><option value="waiting_human">等待人工</option><option value="resolved">已解决</option></select>
            <select value={ownershipFilter} onChange={(event) => setOwnershipFilter(event.target.value)}><option value="">全部归属</option><option value="ai">AI 处理中</option><option value="queued">等待人工</option><option value="human">人工处理中</option></select>
            <select value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value)}><option value="">全部优先级</option><option value="urgent">紧急</option><option value="high">高</option><option value="normal">普通</option><option value="low">低</option></select>
            <label className="mine-toggle"><input type="checkbox" checked={mineOnly} onChange={(event) => setMineOnly(event.target.checked)} />仅看我的</label>
            <label className="mine-toggle"><input type="checkbox" checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)} />仅看未读</label>
            <button className="refresh-button" onClick={() => refreshInbox()} title="刷新会话"><RefreshCw aria-hidden="true" size={17} /></button>
          </div>
          <nav className="mobile-inbox-tabs" aria-label="收件箱视图">
            <button className={mobileInboxView === "list" ? "active" : ""} onClick={() => setMobileInboxView("list")}><InboxIcon aria-hidden="true" />会话</button>
            <button className={mobileInboxView === "conversation" ? "active" : ""} disabled={!selectedId} onClick={() => setMobileInboxView("conversation")}><MessageSquare aria-hidden="true" />对话</button>
            <button className={mobileInboxView === "customer" ? "active" : ""} disabled={!selectedId} onClick={() => setMobileInboxView("customer")}><UserRound aria-hidden="true" />客户</button>
          </nav>
          <div className="three-panel" data-mobile-view={mobileInboxView}>
            <ConversationList items={filteredInbox} selectedId={selectedId} onSelect={(conversationId) => { setSelectedId(conversationId); setMobileInboxView("conversation"); }} />
            <ConversationPanel workspace={workspace} loading={loadingWorkspace} currentUser={user} configuration={supportConfiguration} onAction={runAction} onRouting={updateRouting} onCreateHandoff={createHandoff} onMarkKnowledgeGap={markKnowledgeGap} onNote={addNote} onSend={async (content) => { if (!workspace) return; setWorkspace(await sendAgentMessage(workspace.conversation.conversation_id, content)); await refreshInbox(); }} />
            <CustomerPanel workspace={workspace} memory={memory} onViewCustomer={(customerId) => showAudienceTab("customers", customerId)} />
          </div>
          {inboxNextCursor && <div className="inbox-load-more"><button className="secondary-button" onClick={() => void loadMoreInbox()}>加载更多会话{inboxTotal !== null ? `（已加载 ${inbox.length}/${inboxTotal}）` : ""}</button></div>}
        </>}
        {page === "audience" && <AudiencePage {...commonPageProps} tab={audienceTab} onTabChange={(tab) => showAudienceTab(tab)} selectedSiteId={siteId} preferredCustomerId={focusedCustomerId} />}
        {page === "content" && <div className="workspace-tabs-shell">
          <nav className="workspace-tabs" aria-label="内容与自动化视图">
            <button className={contentTab === "knowledge" ? "active" : ""} onClick={() => showContentTab("knowledge")}>知识库</button>
            {user.scopes.includes("sites:manage") && <button className={contentTab === "widget" ? "active" : ""} onClick={() => showContentTab("widget")}>客服窗口</button>}
            {(user.scopes.includes("automation:read") || user.scopes.includes("sites:manage")) && <button className={contentTab === "automation" ? "active" : ""} onClick={() => showContentTab("automation")}>自动化</button>}
          </nav>
          {contentTab === "knowledge" && <KnowledgePage sites={sites} user={user} selectedSiteId={siteId} />}
          {contentTab === "widget" && user.scopes.includes("sites:manage") && <div className="page-canvas"><WidgetConfigurationManagement sites={sites} /></div>}
          {contentTab === "automation" && (user.scopes.includes("automation:read") || user.scopes.includes("sites:manage")) && <AutomationPage sites={sites} configuration={supportConfiguration} />}
        </div>}
        {page === "reports" && <ReportsPage {...commonPageProps} selectedSiteId={siteId} />}
        {page === "platform" && user.platform_roles.length > 0 && <PlatformAdminPage user={user} />}
        {page === "settings" && <SettingsPage {...commonPageProps} supportConfiguration={supportConfiguration} onSupportConfigurationChanged={refreshSupportConfiguration} onPasswordChanged={() => setUser(null)} onSitesChanged={refreshSites} />}
      </main>
    </div>
  );
}

function SectionSidebar({ counts, sites, siteId, tenantId, realtimeState, activeInboxFilter, onInboxFilter, onLogout }: { page: PageId; counts: Record<string, number>; sites: Site[]; siteId: string; tenantId: string; realtimeState: "connecting" | "live" | "offline"; activeInboxFilter: string; onInboxFilter: (filter: { status?: string; ownership?: string; mine?: boolean; overdue?: boolean; unread?: boolean; priorityRisk?: boolean }) => void; onLogout: () => void }) {
  const summaries = [
    ["全部", counts.all, {}],
    ["待我处理", counts.mine, { mine: true }],
    ["等待人工", counts.queued, { ownership: "queued" }],
    ["SLA 风险", counts.overdue, { overdue: true }],
    ["未读", counts.unread, { unread: true }],
    ["风险/优先级", counts.priorityRisk, { priorityRisk: true }],
    ["已解决", counts.resolved, { status: "resolved" }],
  ] as const;
  return <aside className="section-sidebar"><div className="section-title"><div><span>SupportOS</span><strong>智能客服中心</strong></div></div><nav className="section-nav">{summaries.map(([label, count, filter]) => <button className={`section-link ${activeInboxFilter === label ? "active" : ""}`} key={label} onClick={() => onInboxFilter(filter)}><span>{label}</span><b className={label === "等待人工" || label === "SLA 风险" ? "alert-count" : ""}>{count}</b></button>)}</nav><div className="section-divider" /><div className="sidebar-label">工作空间</div><div className="site-card"><span className="site-logo">W</span><div><strong>{sites.find((site) => site.site_id === siteId)?.name || "全部站点"}</strong><small>{tenantId}</small></div></div><div className="sidebar-footer"><div className="realtime-row"><span className={`status-dot ${realtimeState}`} />{realtimeState === "live" ? "实时连接正常" : realtimeState === "offline" ? "实时连接已断开" : "正在连接实时服务"}</div><button className="logout-button" onClick={onLogout}>退出登录</button></div></aside>;
}

function LoginScreen({ onAuthenticated }: { onAuthenticated: (user: AdminUser) => void }) {
  const [tenantId, setTenantId] = useState("tenant-demo");
  const [username, setUsername] = useState("");
  const [legacyPassword, setLegacyPassword] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [enterpriseCode, setEnterpriseCode] = useState("");
  const [statusToken, setStatusToken] = useState("");
  const [mode, setMode] = useState<"login" | "forgot" | "signup">("login");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [providers, setProviders] = useState<LoginProviderConfiguration | null>(null);
  const [providersLoading, setProvidersLoading] = useState(true);
  const [providerError, setProviderError] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<TenantWorkspace[]>([]);
  const [invitation, setInvitation] = useState<InvitationPreview | null>(null);
  const requiresWorkspace = new URLSearchParams(window.location.search).get("workspace_selection") === "required";
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const invitationToken = fragment.get("invite") || "";
  const signupCode = fragment.get("signup_code") || "";
  const signupEmail = fragment.get("email") || "";
  const resetToken = fragment.get("reset") || "";
  const verificationToken = fragment.get("verify-email") || "";
  const loadProviders = useCallback(async () => {
    setProvidersLoading(true);
    setProviderError(null);
    try {
      setProviders(await getLoginProviders());
    } catch (reason) {
      setProviderError((reason as Error).message);
    } finally {
      setProvidersLoading(false);
    }
  }, []);
  useEffect(() => {
    void loadProviders();
    if (requiresWorkspace) {
      listAuthWorkspaces().then(setWorkspaces).catch((reason: Error) => setError(reason.message));
    }
    if (invitationToken) {
      previewInvitation(invitationToken).then((value) => {
        setInvitation(value);
        setEmail(value.email);
      }).catch((reason: Error) => setError(reason.message));
    }
    if (verificationToken) {
      verifySelfServiceEmail(verificationToken).then((value) => {
        window.history.replaceState({}, "", "/");
        setEmail("");
        setPassword("");
        setMode("login");
        setNotice(`邮箱已验证，工作区“${value.workspace_name}”已创建，请使用邮箱密码登录。`);
      }).catch((reason: Error) => setError(reason.message));
    }
    const authError = new URLSearchParams(window.location.search).get("auth_error");
    if (authError) setError(externalLoginError(authError));
    if (signupCode) {
      setMode("signup");
      setEnterpriseCode(signupCode);
      if (signupEmail) setEmail(signupEmail);
    }
  }, [invitationToken, loadProviders, requiresWorkspace, signupCode, signupEmail, verificationToken]);
  const completeEmailAuthentication = (result: { user: AdminUser | null; workspace_selection_required: boolean }) => {
    if (result.user) {
      window.history.replaceState({}, "", "/");
      onAuthenticated(result.user);
    } else if (result.workspace_selection_required) {
      window.location.assign("/?workspace_selection=required");
    }
  };
  const submitEmail = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null); setNotice(null);
    try { completeEmailAuthentication(await emailLogin({ email, password })); }
    catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  const submitRegistration = async (event: FormEvent) => {
    event.preventDefault(); setError(null); setNotice(null);
    if (password !== confirmPassword) { setError("两次输入的密码不一致。"); return; }
    setBusy(true);
    try {
      completeEmailAuthentication(await registerWithInvitation({
        invitation_token: invitationToken,
        display_name: displayName,
        password,
      }));
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  const submitSelfServiceSignup = async (event: FormEvent) => {
    event.preventDefault(); setError(null); setNotice(null);
    if (password !== confirmPassword) { setError("两次输入的密码不一致。"); return; }
    setBusy(true);
    try {
      const result = await selfServiceSignup(
        { email, password, display_name: displayName, workspace_name: workspaceName, enterprise_code: enterpriseCode },
        crypto.randomUUID(),
      );
      window.history.replaceState({}, "", "/");
      setStatusToken(result.status_token);
      setNotice("注册申请已提交，请查收邮箱并点击验证链接。验证完成后再使用邮箱密码登录。");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  const submitReset = async (event: FormEvent) => {
    event.preventDefault(); setError(null); setNotice(null);
    if (password !== confirmPassword) { setError("两次输入的密码不一致。"); return; }
    setBusy(true);
    try {
      await resetEmailPassword(resetToken, password);
      window.history.replaceState({}, "", "/");
      setPassword(""); setConfirmPassword(""); setNotice("密码已更新，请使用新密码登录。");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  const submitForgot = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null); setNotice(null);
    try {
      await requestPasswordReset(email);
      setNotice("如果该邮箱已注册，重置邮件会很快送达。");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  const submitLegacy = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null); setNotice(null);
    try { onAuthenticated(await login({ tenant_id: tenantId, username, password: legacyPassword })); }
    catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  const chooseWorkspace = async (workspace: TenantWorkspace) => {
    setBusy(true); setError(null);
    try {
      onAuthenticated(await switchTenant(workspace.tenant_id));
      window.history.replaceState({}, "", "/");
    } catch (reason) {
      setError((reason as Error).message);
    } finally { setBusy(false); }
  };
  const dingtalk = providers?.providers.find((provider) => provider.provider === "dingtalk");
  const emailLoginAvailable = providers?.email_login_enabled ?? true;
  const selfServiceSignupAvailable = providers?.self_service_signup_enabled ?? false;
  const title = requiresWorkspace ? "选择工作区" : invitationToken ? "接受工作区邀请" : resetToken ? "设置新密码" : mode === "forgot" ? "找回密码" : mode === "signup" ? "创建独立工作区" : "登录客服工作台";
  const description = requiresWorkspace ? "请选择本次要管理的网站工作区" : invitationToken ? invitation ? `加入 ${invitation.tenant_name}` : "正在验证邀请" : resetToken ? "设置新的登录密码" : mode === "forgot" ? "输入注册邮箱接收重置链接" : mode === "signup" ? "邮箱验证后，系统会为你创建独立工作区" : "使用邮箱和密码安全登录";
  return <div className="login-page">
    <div className="login-visual"><div className="visual-content"><span className="eyebrow">AI-FIRST SUPPORT OPERATIONS</span><h1>让 AI 处理重复问题，<br />让人工专注关键时刻。</h1><p>统一知识检索、客户上下文、风险控制与人工协作。</p><div className="visual-metrics"><div><strong>24/7</strong><span>AI 自动接待</span></div><div><strong>100%</strong><span>证据可追溯</span></div><div><strong>0</strong><span>跨工作区泄漏</span></div></div></div></div>
    <div className="login-card">
      <img className="login-logo" src="/supportos-logo.svg" alt="SupportOS" /><span className="eyebrow dark">SUPPORTOS CONSOLE</span><h2>{title}</h2><p>{description}</p>
      {providerError && <div className="login-service-alert" role="status"><div><strong>认证服务暂时无法确认登录配置</strong><span>邮箱登录入口仍然保留，您也可以重新检测服务状态。</span><details><summary>技术信息</summary><code>{providerError}</code></details></div><button type="button" disabled={providersLoading} onClick={() => void loadProviders()}>{providersLoading ? "检测中…" : "重新检测"}</button></div>}
      {error && <div className="form-error">{error}</div>}{notice && <div className="inline-success">{notice}</div>}
      {requiresWorkspace ? <div className="workspace-list">{workspaces.map((workspace) => <button className="workspace-option" disabled={busy} key={workspace.tenant_id} onClick={() => void chooseWorkspace(workspace)}><span>{workspace.name.slice(0, 1)}</span><div><strong>{workspace.name}</strong><small>{workspace.roles.length > 0 ? workspace.roles.map(formatRoleLabel).join(" · ") : "未分配角色"}</small></div></button>)}{workspaces.length === 0 && !error && <p className="workspace-loading">正在读取可用工作区…</p>}</div>
      : invitationToken ? <form className="email-login-form" onSubmit={submitRegistration}><label>受邀邮箱<input value={email} readOnly /></label><label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" required /></label><label>设置密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label><label>确认密码<input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label><button className="primary-button" disabled={busy || !invitation}>{busy ? "正在创建账号…" : "创建账号并加入"}</button></form>
      : resetToken ? <form className="email-login-form" onSubmit={submitReset}><label>新密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label><label>确认新密码<input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label><button className="primary-button" disabled={busy}>{busy ? "正在更新…" : "更新密码"}</button></form>
      : mode === "signup" ? selfServiceSignupAvailable ? <form className="email-login-form" onSubmit={submitSelfServiceSignup}><label>企业邮箱<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required /></label><label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" required /></label><label>工作区名称<input value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} required /></label><label>企业邀请码<input value={enterpriseCode} onChange={(event) => setEnterpriseCode(event.target.value)} autoComplete="one-time-code" required /></label><label>设置密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label><label>确认密码<input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label><button className="primary-button" disabled={busy}>{busy ? "正在提交…" : "创建独立工作区"}</button>{statusToken && <button className="text-button" type="button" disabled={busy} onClick={() => resendSelfServiceVerification(statusToken).then(() => setNotice("验证邮件已重新发送。"), (reason: Error) => setError(reason.message))}>重新发送验证邮件</button>}<button className="text-button" type="button" onClick={() => { setMode("login"); setError(null); setNotice(null); }}>返回登录</button></form> : <div className="form-error">独立工作区开通暂未启用，请联系平台管理员。</div>
      : mode === "forgot" ? <form className="email-login-form" onSubmit={submitForgot}><label>注册邮箱<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required /></label><button className="primary-button" disabled={busy}>{busy ? "正在提交…" : "发送重置邮件"}</button><button className="text-button" type="button" onClick={() => { setMode("login"); setError(null); setNotice(null); }}>返回登录</button></form>
      : <>{emailLoginAvailable && <form className="email-login-form" onSubmit={submitEmail}><label>邮箱<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required /></label><label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></label><div className="login-form-actions"><button className="primary-button" disabled={busy}>{busy ? "正在验证…" : "登录"}</button>{providers?.password_reset_enabled && <button className="text-button" type="button" onClick={() => { setMode("forgot"); setError(null); setNotice(null); }}>忘记密码</button>}{selfServiceSignupAvailable && <button className="text-button" type="button" onClick={() => { setMode("signup"); setError(null); setNotice(null); }}>创建独立工作区</button>}</div></form>}{dingtalk && <button className="dingtalk-button" onClick={() => window.location.assign(`${dingtalk.start_url}?return_path=${encodeURIComponent("/")}`)}><span>钉</span>使用钉钉登录</button>}{providers?.legacy_login_enabled && <details className="legacy-login"><summary>应急账号登录</summary><form onSubmit={submitLegacy}><label>工作区标识<input value={tenantId} onChange={(event) => setTenantId(event.target.value)} required /></label><label>用户名<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required /></label><label>密码<input type="password" value={legacyPassword} onChange={(event) => setLegacyPassword(event.target.value)} autoComplete="current-password" required /></label><button className="primary-button" disabled={busy}>{busy ? "正在验证…" : "应急登录"}</button></form></details>}{providers && !providers.email_login_enabled && !dingtalk && !providers.legacy_login_enabled && <div className="form-error">当前没有可用的登录方式，请联系平台管理员。</div>}</>}
      <small className="security-note">工作区和角色只由服务端邀请及成员关系确定，浏览器不会保存登录凭证。</small>
    </div>
  </div>;
}

function ConversationList({ items, selectedId, onSelect }: { items: InboxConversation[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return <section className="conversation-list"><div className="panel-title"><strong>会话</strong><span>{items.length} 条</span></div><div className="conversation-scroll">{items.map((item) => { const risk = formatRiskLevel(item.risk_level); return <button className={`conversation-card ${selectedId === item.conversation_id ? "selected" : ""}`} key={item.conversation_id} onClick={() => onSelect(item.conversation_id)}><span className="visitor-avatar">{item.customer_display_name?.slice(0, 2) || "访客"}</span><div className="conversation-copy"><div><strong>{item.customer_display_name || "匿名访客"}</strong><time>{relativeTime(item.updated_at)}</time></div><VisitorNetworkMeta item={item} compact /><p>{item.last_message_preview || "暂无消息摘要"}</p><div className="card-meta"><StatusPill conversation={item} /><span className={`priority-badge ${item.priority}`}>{formatPriority(item.priority)}</span><SlaBadge dueAt={item.sla_due_at} />{item.unread_count > 0 && <span className="unread-badge">{item.unread_count}</span>}{item.identity_verified && <span className="verified">✓ 已验证</span>}{item.risk_level > 0 && <span className="risk" title={`${risk.description} ${risk.action}`}>{risk.label}</span>}</div></div></button>; })}{items.length === 0 && <EmptyState text="没有符合条件的会话" />}</div></section>;
}

function ConversationPanel({ workspace, loading, currentUser, configuration, onAction, onRouting, onCreateHandoff, onMarkKnowledgeGap, onNote, onSend }: { workspace: Workspace | null; loading: boolean; currentUser: AdminUser; configuration: SupportConfiguration; onAction: (action: "takeover" | "release-to-ai" | "resolve") => Promise<void>; onRouting: (values: { assigned_agent_id: string | null; queue_id: string | null; priority: "low" | "normal" | "high" | "urgent"; tags: string[] }) => Promise<void>; onCreateHandoff: () => Promise<void>; onMarkKnowledgeGap: (category: "missing_knowledge" | "incorrect_answer") => Promise<void>; onNote: (content: string) => Promise<void>; onSend: (content: string) => Promise<void> }) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<"reply" | "note">("reply");
  const [routingOpen, setRoutingOpen] = useState(false);
  const [tagText, setTagText] = useState("");
  useEffect(() => {
    setDraft(workspace?.handoff_context?.reply_draft || "");
    setMode("reply");
    setTagText(workspace?.conversation.tags.join(", ") || "");
  }, [workspace?.conversation.conversation_id]);
  if (loading) return <section className="conversation-pane"><EmptyState text="正在加载会话…" /></section>;
  if (!workspace) return <section className="conversation-pane"><EmptyState text="选择一个会话开始处理" /></section>;
  const conversation = workspace.conversation; const ownedByMe = conversation.ownership_mode === "human" && conversation.assigned_agent_id === currentUser.user_id;
  const submit = async () => {
    if (!draft.trim() || (mode === "reply" && !ownedByMe)) return;
    setSending(true);
    try {
      if (mode === "note") await onNote(draft.trim());
      else await onSend(draft.trim());
      setDraft("");
    } finally { setSending(false); }
  };
  const saveRouting = async (next: Partial<{ assigned_agent_id: string | null; queue_id: string | null; priority: "low" | "normal" | "high" | "urgent" }>) => {
    await onRouting({
      assigned_agent_id: next.assigned_agent_id === undefined ? conversation.assigned_agent_id : next.assigned_agent_id,
      queue_id: next.queue_id === undefined ? conversation.queue_id : next.queue_id,
      priority: next.priority || conversation.priority,
      tags: tagText.split(",").map((tag) => tag.trim()).filter(Boolean),
    });
  };
  return <section className="conversation-pane">
    <div className="conversation-header"><div><strong>{conversation.customer_display_name || "匿名访客"}</strong><StatusPill conversation={conversation} /><span className={`priority-badge ${conversation.priority}`}>{formatPriority(conversation.priority)}</span><SlaBadge dueAt={conversation.sla_due_at} /><small>{conversation.channel} · {conversation.site_id || "未绑定网站"} · {conversation.identity_verified ? "身份已确认" : "身份未确认"}</small><VisitorNetworkMeta item={conversation} /></div><div>{conversation.ownership_mode !== "human" && conversation.status !== "resolved" && <button className="primary-small" onClick={() => onAction("takeover")}>接管</button>}{conversation.status !== "resolved" && <button className="outline-small" onClick={() => onAction("resolve")}>解决</button>}<button className="outline-small" onClick={() => setRoutingOpen((value) => !value)}>{terminology.assignmentSettings}</button></div></div>
    {routingOpen && <div className="routing-panel"><label>负责人<select value={conversation.assigned_agent_id || ""} onChange={(event) => void saveRouting({ assigned_agent_id: event.target.value || null })}><option value="">未分配</option>{configuration.agents.map((agent) => <option value={agent.agent_id} key={agent.agent_id}>{agent.display_name}</option>)}</select></label><label title={helpText.supportGroup}>{terminology.supportGroup}<select value={conversation.queue_id || ""} onChange={(event) => void saveRouting({ queue_id: event.target.value || null })}><option value="">未指定</option>{configuration.queues.filter((queue) => queue.status !== "disabled" && (queue.site_id === null || queue.site_id === conversation.site_id)).map((queue) => <option value={queue.queue_id} key={queue.queue_id}>{queue.name}</option>)}</select></label><label>优先级<select value={conversation.priority} onChange={(event) => void saveRouting({ priority: event.target.value as "low" | "normal" | "high" | "urgent" })}><option value="low">低</option><option value="normal">普通</option><option value="high">高</option><option value="urgent">紧急</option></select></label><label className="routing-tags">标签<input value={tagText} onChange={(event) => setTagText(event.target.value)} onBlur={() => void saveRouting({})} placeholder="售前, 物流, VIP" /></label><div className="routing-actions"><button className="ghost-link" onClick={() => void onCreateHandoff()}>转交人工</button>{ownedByMe && <button className="ghost-link" onClick={() => onAction("release-to-ai")}>交还 AI</button>}</div></div>}
    {workspace.handoff_context && <HandoffSummary context={workspace.handoff_context} onMarkKnowledgeGap={onMarkKnowledgeGap} />}
    <div className="message-stream">{workspace.messages.map((message) => <div className={`message-row ${message.message_type === "internal_note" ? "internal-note" : message.role}`} key={message.message_id}><div className="message-author">{message.message_type === "internal_note" ? "内部备注" : roleName(message.role)}<time>{formatTime(message.created_at)}</time></div><div className="message-bubble">{message.content}</div>{Array.isArray(message.metadata.citations) && message.metadata.citations.length > 0 && <div className="citations">引用：{message.metadata.citations.join(" · ")}</div>}</div>)}</div>
    <div className={`composer ${mode === "note" ? "note-mode" : ""}`}><div className="composer-tabs"><button className={mode === "reply" ? "active" : ""} onClick={() => setMode("reply")}>回复客户</button><button className={mode === "note" ? "active" : ""} onClick={() => setMode("note")}>内部备注</button>{configuration.canned_replies.length > 0 && <select value="" onChange={(event) => { const reply = configuration.canned_replies.find((item) => item.reply_id === event.target.value); if (reply) setDraft(reply.content); }}><option value="">快捷回复</option>{configuration.canned_replies.map((reply) => <option value={reply.reply_id} key={reply.reply_id}>/{reply.shortcut} · {reply.title}</option>)}</select>}</div><textarea value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={mode === "note" ? "仅客服可见，不会发送给访客" : ownedByMe ? "输入人工回复，Ctrl + Enter 发送" : "接管会话后可发送人工回复"} disabled={(mode === "reply" && !ownedByMe) || sending} onKeyDown={(event) => { if (event.ctrlKey && event.key === "Enter") void submit(); }} /><div className="composer-footer"><span>{mode === "note" ? "内部备注仅对客服可见并写入审计日志" : "回复将以人工客服身份发送"}</span><button className="send-button" disabled={!draft.trim() || sending || (mode === "reply" && !ownedByMe)} onClick={() => void submit()}>{sending ? "处理中" : mode === "note" ? "保存备注" : "发送"}</button></div></div>
  </section>;
}

function HandoffSummary({ context, onMarkKnowledgeGap }: { context: NonNullable<Workspace["handoff_context"]>; onMarkKnowledgeGap: (category: "missing_knowledge" | "incorrect_answer") => Promise<void> }) {
  const risk = formatRiskLevel(context.risk_level);
  return <details className="handoff-summary" open><summary>转人工处理摘要 · {risk.label}</summary><div><p><strong>客户问题：</strong>{context.unresolved_question || context.summary}</p>{context.user_intent && <p><strong>咨询类型：</strong>{context.user_intent}</p>}{context.ai_attempt && <p><strong>系统已尝试：</strong>{context.ai_attempt}</p>}<p><strong>建议下一步：</strong>{context.suggested_next_action || risk.action}</p><p><strong>处理要求：</strong>{risk.description}</p>{(context.failed_tools.length > 0 || context.knowledge_sources.length > 0) && <details className="handoff-technical"><summary>查看处理记录</summary>{context.failed_tools.length > 0 && <p><strong>未完成的查询：</strong>{context.failed_tools.join("、")}</p>}{context.knowledge_sources.length > 0 && <p><strong>参考内容：</strong>{context.knowledge_sources.join("、")}</p>}</details>}<div className="handoff-feedback"><button onClick={() => void onMarkKnowledgeGap("missing_knowledge")}>记录知识缺口</button><button onClick={() => void onMarkKnowledgeGap("incorrect_answer")}>记录回答错误</button></div></div></details>;
}

function CustomerPanel({ workspace, memory, onViewCustomer }: { workspace: Workspace | null; memory: MemoryItem[]; onViewCustomer: (customerId: string) => void }) {
  if (!workspace) return <aside className="customer-panel"><EmptyState text="客户上下文" /></aside>;
  const item = workspace.conversation;
  const handoff = workspace.handoff_context;
  return <aside className="customer-panel">
    <div className="profile-card">
      <div className="profile-avatar">{initials(item.customer_display_name || "访客")}</div>
      <h3>{item.customer_display_name || "匿名访客"}</h3>
      <p>{item.customer_id || "临时会话身份"}</p>
      <span className={item.identity_verified ? "trust-badge trusted" : "trust-badge"}>{item.identity_verified ? "✓ 可信身份" : "! 未验证身份"}</span>
      {item.customer_id && item.identity_verified && <button className="profile-link" onClick={() => onViewCustomer(item.customer_id || "")}>查看完整客户档案</button>}
    </div>
    <ContextSection title="访客网络">
      <div className="visitor-network-detail"><VisitorNetworkMeta item={item} /></div>
      <ContextRow label="IP 地址" value={item.visitor_ip_address || "未记录"} mono />
      <ContextRow label="国家代码" value={item.visitor_country_code || "--"} />
    </ContextSection>
    <ContextSection title="本次咨询">
      <ContextRow label="当前意图" value={handoff?.user_intent || item.handoff_reason || "常规咨询"} />
      <ContextRow label="来源渠道" value={item.channel || terminology.websiteChat} />
      <ContextRow label="站点" value={item.site_id || "未绑定"} />
      {handoff?.suggested_next_action && <div className="next-action-callout"><span>建议下一步</span><p>{handoff.suggested_next_action}</p></div>}
    </ContextSection>
    <ContextSection title="处理状态">
      <ContextRow label="处理状态" value={formatOwnership(item.ownership_mode)} />
      <ContextRow label="负责人" value={item.assigned_agent_id || "未分配"} />
      <ContextRow label={terminology.supportGroup} value={item.queue_id || "未指定"} />
      <ContextRow label="优先级" value={formatPriority(item.priority)} />
      <ContextRow label="风险判断" value={formatRiskLevel(item.risk_level).label} />
      {item.tags.length > 0 && <div className="context-tags">{item.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
    </ContextSection>
    <ContextSection title="客户记忆"><div className="memory-list">{!item.customer_id ? <p className="muted-copy">匿名访客不创建长期记忆。</p> : memory.length === 0 ? <p className="muted-copy">暂无经过验证的长期记忆。</p> : memory.map((entry) => <div className="memory-card" key={entry.memory_id}><span>{memoryKind(entry.kind)}</span><p>{entry.content}</p><small>可信度 {Math.round(entry.confidence * 100)}% · {entry.source_type}</small></div>)}</div></ContextSection>
    <details className="safety-boundary"><summary>回复前需要注意什么</summary><ul className="safety-list"><li>订单与支付信息必须查询最新记录</li><li>历史偏好不能代替客户身份确认</li><li>信息不足或内容冲突时交由人工处理</li></ul></details>
  </aside>;
}

function ContextSection({ title, children }: { title: string; children: React.ReactNode }) { return <section className="context-section"><h4>{title}</h4>{children}</section>; }
function ContextRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div className="context-row"><span>{label}</span><strong className={mono ? "mono-value" : undefined}>{value}</strong></div>; }
function VisitorNetworkMeta({ item, compact = false }: { item: InboxConversation; compact?: boolean }) { const country = countryDetails(item.visitor_country_code); return <div className={`conversation-location ${compact ? "compact" : ""}`} title={`${country.name} (${country.code}) · ${item.visitor_ip_address || "IP 未记录"}`}>{country.flagCode ? <span className={`presence-flag fi fi-${country.flagCode}`} aria-hidden="true" /> : <Globe2 className="presence-flag-unknown" aria-hidden="true" />}<span>{country.name}</span><span className="location-ip"><MapPin aria-hidden="true" /><code>{item.visitor_ip_address || "IP 未记录"}</code></span></div>; }
function StatusPill({ conversation }: { conversation: InboxConversation }) { const value = conversation.status === "resolved" ? "已解决" : formatOwnership(conversation.ownership_mode); return <span className={`status-pill ${conversation.ownership_mode}`}>{value}</span>; }
function SlaBadge({ dueAt }: { dueAt: string | null }) { if (!dueAt) return null; const remaining = new Date(dueAt).getTime() - Date.now(); const overdue = remaining <= 0; return <span className={`sla-badge ${overdue ? "overdue" : "due"}`} title={helpText.responseDeadline}>{overdue ? `${terminology.responseOverdue} ${formatCompactDuration(-remaining)}` : `还剩 ${formatCompactDuration(remaining)}`}</span>; }
function EmptyState({ text }: { text: string }) { return <div className="empty-state"><span>◎</span><p>{text}</p></div>; }
function LoadingScreen() { return <div className="loading-screen"><img className="loading-logo" src="/supportos-logo.svg" alt="SupportOS" /><p>正在初始化客服工作台…</p></div>; }
function externalLoginError(value: string) { return ({ authorization_denied: "钉钉授权未完成，请重试。", invalid_login_state: "登录请求已过期或已使用，请重新登录。", workspace_access_denied: "身份已验证，但尚未分配可访问的工作区。", identity_provider_unavailable: "钉钉登录暂时不可用，请稍后重试或使用应急账号。" } as Record<string, string>)[value] || "登录失败，请重试。"; }
function audienceTabFromLocation(): AudienceTab {
  if (window.location.pathname === "/customers") return "customers";
  const value = new URLSearchParams(window.location.search).get("tab");
  return value === "customers" || value === "high-intent" ? value : "live";
}
function contentTabFromLocation(): ContentTab {
  if (window.location.pathname === "/automation") return "automation";
  if (window.location.pathname === "/knowledge") return "knowledge";
  const value = new URLSearchParams(window.location.search).get("tab");
  return value === "widget" || value === "automation" ? value : "knowledge";
}
function initials(value: string) { return value.trim().slice(0, 2).toUpperCase(); }
function roleName(role: string) { return role === "user" ? "客户" : role === "assistant" ? "AI 客服" : role === "agent" ? "人工客服" : "系统"; }
function memoryKind(value: string) { return ({ preference: "客户偏好", verified_product: "已验证产品", troubleshooting: "排障记录", resolution: "解决方案" } as Record<string, string>)[value] || value; }
function formatTime(value: string | null) { return value ? new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : ""; }
function relativeTime(value: string) { const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000); if (seconds < 60) return "刚刚"; if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`; if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`; return `${Math.floor(seconds / 86400)} 天`; }
function isSlaOverdue(value: string | null, now: number) { return Boolean(value && new Date(value).getTime() <= now); }
function compareInboxPriority(left: InboxConversation, right: InboxConversation, now: number) { const leftOverdue = isSlaOverdue(left.sla_due_at, now); const rightOverdue = isSlaOverdue(right.sla_due_at, now); if (leftOverdue !== rightOverdue) return leftOverdue ? -1 : 1; const leftDue = left.sla_due_at ? new Date(left.sla_due_at).getTime() : Number.POSITIVE_INFINITY; const rightDue = right.sla_due_at ? new Date(right.sla_due_at).getTime() : Number.POSITIVE_INFINITY; if (leftDue !== rightDue) return leftDue - rightDue; const weights = { urgent: 0, high: 1, normal: 2, low: 3 }; if (weights[left.priority] !== weights[right.priority]) return weights[left.priority] - weights[right.priority]; return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(); }
function formatCompactDuration(milliseconds: number) { const minutes = Math.max(1, Math.floor(milliseconds / 60_000)); if (minutes < 60) return `${minutes} 分钟`; const hours = Math.floor(minutes / 60); if (hours < 24) return `${hours} 小时`; return `${Math.floor(hours / 24)} 天`; }


