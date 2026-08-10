import { useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  BookOpen,
  Building2,
  ChevronDown,
  CircleGauge,
  Clock3,
  Eye,
  ExternalLink,
  FileText,
  Globe2,
  Inbox,
  Laptop,
  MapPin,
  MessagesSquare,
  MonitorSmartphone,
  Search,
  ShieldCheck,
  Settings,
  UserRound,
  Users,
} from "lucide-react";
import "flag-icons/css/flag-icons.min.css";

import {
  changePassword,
  createSupportQueue,
  createCannedReply,
  createManagedSite,
  createTenantInvitation,
  getSupportAnalytics,
  getExperienceSummary,
  getSystemStatus,
  issueSiteVerificationChallenge,
  listCustomerConversations,
  listCustomers,
  listAdminSessions,
  listAuditEvents,
  createAdminUser,
  listAdminUsers,
  listManagedSites,
  listTenantInvitations,
  listMemory,
  listSupportQueueMembers,
  resetAdminUserPassword,
  revokeAdminSession,
  revokeTenantInvitation,
  rotateManagedSiteKey,
  updateAdminUser,
  updateManagedSite,
  verifyManagedSite,
  updateSupportQueue,
  updateSupportQueueMembers,
} from "./api";
import {
  KnowledgeGapManagement,
  WidgetConfigurationManagement,
} from "./customerExperiencePages";
export { AutomationPage } from "./customerExperiencePages";
import type {
  AdminSessionItem,
  AdminUser,
  AuditEvent,
  InboxConversation,
  ManagedSite,
  SiteVerificationChallenge,
  MemoryItem,
  TenantInvitation,
  Site,
  SupportAnalytics,
  CustomerExperienceSummary,
  CustomerDirectoryItem,
  LeadNextAction,
  SystemStatus,
  SupportConfiguration,
  SupportQueue,
  VisitorPresence,
  PresenceLoadState,
} from "./types";
import {
  formatAccountDisplayName,
  formatOwnership,
  formatPriority,
  formatRiskLevel,
  formatRoleLabel,
  terminology,
} from "./content/terminology";
import { helpText } from "./content/helpText";
import { metricHelp } from "./content/metricDefinitions";
import { SiteWebSourceEditor } from "./siteWebSourceEditor";

export type PageId =
  | "overview"
  | "inbox"
  | "audience"
  | "content"
  | "reports"
  | "platform"
  | "settings";

export const pageConfiguration: Record<
  PageId,
  { label: string; icon: LucideIcon; path: string; scope: string; eyebrow: string }
> = {
  overview: { label: "概览", icon: CircleGauge, path: "/", scope: "support:inbox:read", eyebrow: "独立站运营台" },
  inbox: { label: "收件箱", icon: Inbox, path: "/inbox", scope: "support:inbox:read", eyebrow: "客服运营工作台" },
  audience: { label: "访客与客户", icon: Users, path: "/audience", scope: "support:inbox:read", eyebrow: "访客与客户运营" },
  content: { label: "内容与自动化", icon: BookOpen, path: "/content", scope: "knowledge:read", eyebrow: "客户体验配置" },
  reports: { label: "分析", icon: BarChart3, path: "/reports", scope: "support:inbox:read", eyebrow: "运营洞察中心" },
  platform: { label: "平台管理", icon: ShieldCheck, path: "/platform", scope: "platform:access", eyebrow: "平台运营控制台" },
  settings: { label: "设置", icon: Settings, path: "/settings", scope: "sites:read", eyebrow: "工作区管理中心" },
};

export function pageFromPath(pathname: string): PageId {
  if (["/visitors", "/customers", "/audience"].includes(pathname)) return "audience";
  if (["/automation", "/knowledge", "/content"].includes(pathname)) return "content";
  if (pathname === "/tickets") return "inbox";
  const matched = Object.entries(pageConfiguration).find(([, value]) => value.path === pathname)?.[0] as PageId | undefined;
  return matched || "overview";
}

interface CommonPageProps {
  inbox: InboxConversation[];
  sites: Site[];
  user: AdminUser;
  presence: VisitorPresence[];
  presenceLoadState: PresenceLoadState;
  presenceUpdatedAt: number | null;
  onRetryPresence: () => void;
  onOpenConversation: (conversationId: string) => void;
}

export type AudienceTab = "live" | "high-intent" | "customers";

export function AudiencePage({
  tab,
  onTabChange,
  selectedSiteId,
  preferredCustomerId,
  ...props
}: CommonPageProps & {
  tab: AudienceTab;
  onTabChange: (tab: AudienceTab) => void;
  selectedSiteId: string;
  preferredCustomerId: string | null;
}) {
  return <div className="workspace-tabs-shell">
    <nav className="workspace-tabs" aria-label="访客与客户视图">
      <button className={tab === "live" ? "active" : ""} onClick={() => onTabChange("live")}>实时访客</button>
      <button className={tab === "high-intent" ? "active" : ""} onClick={() => onTabChange("high-intent")}>购买机会</button>
      <button className={tab === "customers" ? "active" : ""} onClick={() => onTabChange("customers")}>客户档案</button>
    </nav>
    {tab === "live" && <VisitorsPage {...props} />}
    {tab === "high-intent" && <PurchaseOpportunitiesPage {...props} />}
    {tab === "customers" && <CustomersPage {...props} selectedSiteId={selectedSiteId} preferredCustomerId={preferredCustomerId} />}
  </div>;
}

export const PURCHASE_OPPORTUNITY_FRESHNESS_WINDOW_MS = 5 * 60_000;

const SERVER_CLOCK_SKEW_TOLERANCE_MS = 60_000;
const operationPriorityRank: Record<NonNullable<VisitorPresence["operation_priority"]>, number> = {
  P0: 0,
  P1: 1,
  P2: 2,
};
const freshnessRank: Record<NonNullable<VisitorPresence["freshness"]>, number> = {
  current: 0,
  aging: 1,
  stale: 2,
  expired: 3,
  unknown: 4,
};

function isRecentServerTimestamp(value: string | null | undefined, now: number): boolean {
  if (!value) return false;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return false;
  const age = now - timestamp;
  return age >= -SERVER_CLOCK_SKEW_TOLERANCE_MS
    && age <= PURCHASE_OPPORTUNITY_FRESHNESS_WINDOW_MS;
}

export function isPurchaseOpportunityActionable(
  item: VisitorPresence,
  now = Date.now(),
): boolean {
  if (item.queue_eligible !== true) return false;
  if (item.freshness !== "current" && item.freshness !== "aging") return false;
  return isRecentServerTimestamp(item.last_seen_at, now)
    && isRecentServerTimestamp(item.scored_at, now);
}

export function actionablePurchaseOpportunities(
  presence: VisitorPresence[],
  now = Date.now(),
): VisitorPresence[] {
  return presence
    .filter((item) => isPurchaseOpportunityActionable(item, now))
    .sort((left, right) => {
      const priorityDifference = (operationPriorityRank[left.operation_priority || "P2"])
        - (operationPriorityRank[right.operation_priority || "P2"]);
      if (priorityDifference !== 0) return priorityDifference;
      const freshnessDifference = freshnessRank[left.freshness || "unknown"]
        - freshnessRank[right.freshness || "unknown"];
      if (freshnessDifference !== 0) return freshnessDifference;
      const scoreDifference = (right.commercial_intent ?? 0) - (left.commercial_intent ?? 0);
      if (scoreDifference !== 0) return scoreDifference;
      return Date.parse(right.last_seen_at) - Date.parse(left.last_seen_at);
    });
}

export function purchaseOpportunityConversationIds(
  presence: VisitorPresence[],
  now = Date.now(),
): Set<string> {
  return new Set(
    actionablePurchaseOpportunities(presence, now)
      .filter((item) => item.conversation_id)
      .map((item) => item.conversation_id as string),
  );
}

function PurchaseOpportunitiesPage({
  sites,
  presence,
  presenceLoadState,
  presenceUpdatedAt,
  onRetryPresence,
  onOpenConversation,
}: CommonPageProps) {
  const now = Date.now();
  const candidates = actionablePurchaseOpportunities(presence, now)
    .map((item) => ({ item, score: item.commercial_intent ?? 0 }));
  const expiredSnapshotCount = presence.filter(
    (item) => item.queue_eligible === true && !isPurchaseOpportunityActionable(item, now),
  ).length;
  const showMetrics = presenceLoadState === "ready" || presenceLoadState === "stale";
  const liveLabel = presenceLoadState === "ready"
    ? "实时更新"
    : presenceLoadState === "stale" && presenceUpdatedAt
      ? `数据暂时滞后 · ${new Date(presenceUpdatedAt).toLocaleTimeString("zh-CN", { hour12: false })}`
      : presenceLoadState === "loading" ? "正在连接" : "连接失败";
  return <PageCanvas>
    <MetricGrid items={[
      [showMetrics ? String(candidates.length) : "—", "当前购买机会"],
      [showMetrics ? String(candidates.filter(({ item }) => item.operation_priority === "P0").length) : "—", "需要立即跟进"],
      [showMetrics ? String(candidates.filter(({ item }) => item.conversation_id).length) : "—", "已关联会话"],
      [showMetrics ? String(candidates.filter(({ item }) => item.confidence_grade === "A").length) : "—", "高可信判断"],
    ]} />
    <section className="presence-panel" aria-labelledby="high-intent-title">
      <div className="presence-toolbar"><div><h2 id="high-intent-title">购买机会</h2><p>购买意向与客服风险分开计算；分数用于排序，不代表成交概率。</p></div><span className={`presence-live ${presenceLoadState}`}><i aria-hidden="true" />{liveLabel}</span></div>
      {presenceLoadState === "stale" && <div className="inline-error">连接暂时中断，以下为最后一次成功获取的数据。</div>}
      {showMetrics && expiredSnapshotCount > 0 && <div className="inline-error">{expiredSnapshotCount} 条服务端机会快照已超过 5 分钟有效期，已移出实时队列且不可操作。</div>}
      {presenceLoadState === "loading" && <PageEmpty title="正在获取购买机会" description="正在连接实时访客服务。" />}
      {presenceLoadState === "error" && <PageEmpty title="暂时无法获取购买机会" description="当前数据请求失败，请稍后重试。" actionLabel="重新加载" onAction={onRetryPresence} />}
      <div className="presence-table-wrap">
        <table className="presence-table intent-table">
          <thead><tr><th>访客</th><th>当前页面</th><th>购买意向</th><th>为什么现在出现</th><th>可信度</th><th>最近活跃</th><th>建议动作</th><th>关联会话</th></tr></thead>
          <tbody>{candidates.map(({ item, score }) => {
            const site = sites.find((candidate) => candidate.site_id === item.site_id);
            const canOpen = Boolean(item.conversation_id);
            return <tr key={`${item.site_id}:${item.visitor_id}`}>
              <td><EntityCell title={shortVisitorId(item.visitor_id)} subtitle={site?.name || item.site_id} /></td>
              <td><PresencePageCell item={item} /></td>
              <td><div className={`intent-score intent-${item.intent_tier || "unknown"}`}><strong>{score}</strong><small>{intentTierLabel(item.intent_tier)}</small></div></td>
              <td><TagList values={purchaseOpportunitySignalLabels(item.signals || [])} /></td>
              <td><div className="intent-confidence"><strong>{item.confidence_grade || "C"}</strong><small>{Math.round((item.confidence || 0) * 100)}%</small></div></td>
              <td><div className="intent-freshness"><strong>{freshnessLabel(item.freshness)}</strong><small>{item.last_seen_at ? `${elapsedTime(item.last_seen_at, now)}前` : "时间未知"}</small></div></td>
              <td><div className="intent-action"><strong>{nextActionLabel(item.next_action)}</strong><small className={`operation-priority ${item.operation_priority || "P2"}`}>{item.operation_priority || "P2"}</small></div></td>
              <td>{canOpen ? <button className="presence-conversation" onClick={() => onOpenConversation(item.conversation_id || "")}><MessagesSquare aria-hidden="true" /><span>打开</span></button> : <span className="presence-browsing"><i aria-hidden="true" />浏览中</span>}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
      {(presenceLoadState === "ready" || presenceLoadState === "stale") && candidates.length === 0 && <PageEmpty title="当前没有购买机会" description={expiredSnapshotCount > 0 ? "上次服务端评分快照已过期，当前没有可操作机会。" : "达到运营阈值且数据新鲜的访客会显示在这里。"} />}
    </section>
  </PageCanvas>;
}

function intentTierLabel(tier: VisitorPresence["intent_tier"]): string {
  switch (tier) {
    case "hot": return "Hot";
    case "warm": return "Warm";
    case "nurture": return "Nurture";
    default: return "Unknown";
  }
}

export function nextActionLabel(action: LeadNextAction | undefined): string {
  if (action === undefined) return "待确认";
  switch (action) {
    case "monitor": return "观察";
    case "contact_now": return "立即联系";
    case "continue_conversation": return "继续会话";
    case "invite_chat": return "邀请咨询";
    case "answer_shipping": return "解答配送";
    case "answer_price": return "解答价格";
    case "answer_payment": return "解答支付";
    case "offer_assistance": return "主动协助";
    case "monitor_closely": return "重点观察";
    default: return unknownNextActionLabel(action);
  }
}

function unknownNextActionLabel(_action: never): string {
  return "待确认";
}

function freshnessLabel(freshness: VisitorPresence["freshness"]): string {
  switch (freshness) {
    case "current": return "实时";
    case "aging": return "近期";
    case "stale": return "已滞后";
    case "expired": return "已过期";
    default: return "未知";
  }
}

const conversationIntentLabels: Record<string, string> = {
  buy: "明确购买意向",
  buy_now: "希望立即购买",
  cart: "讨论加购",
  checkout: "准备结账",
  order: "准备下单",
  payment: "询问支付",
  purchase: "明确购买意向",
  purchase_ready: "已准备购买",
  quote: "请求报价",
  human_handoff: "要求人工",
  contact_human: "要求人工",
  speak_to_human: "要求人工",
  delivery: "询问配送",
  delivery_estimate: "询问送达时间",
  discount: "询问优惠",
  payment_methods: "询问支付方式",
  price: "询问价格",
  product_comparison: "比较商品",
  product_customization: "询问定制",
  product_dimensions: "询问尺寸",
  product_material: "询问材质",
  product_price: "询问价格",
  product_recommendation: "请求选购建议",
  product_stock: "询问库存",
  product_weight: "询问重量",
  shipping: "询问配送",
  shipping_coverage: "询问配送范围",
  shipping_customs: "询问关税",
  stock: "询问库存",
};

export function purchaseOpportunitySignalLabels(signals: string[]): string[] {
  return Array.from(new Set(signals.map(signalLabel)));
}

export function signalLabel(signal: string): string {
  const labels: Record<string, string> = {
    page_taxonomy: "页面类型已确认",
    cart_page: "购物车",
    checkout_page: "结账页",
    product_page: "商品页",
    pricing_page: "价格页",
    shipping_page: "配送信息",
    payment_page: "支付信息",
    comparison_page: "比较商品",
    category_page: "分类浏览",
    page_dwell_15s: "当前页 15 秒+",
    page_dwell_60s: "当前页 1 分钟+",
    page_dwell_180s: "当前页 3 分钟+",
    session_active_15s: "本次有效活跃 15 秒+",
    session_active_60s: "本次有效活跃 1 分钟+",
    session_active_180s: "本次有效活跃 3 分钟+",
    page_views_3: "浏览 3 页+",
    page_views_5: "浏览 5 页+",
    widget_open: "客服窗口已打开",
    conversation_started: "已关联会话",
    fresh_current: "刚刚活跃",
    fresh_aging: "近期离开",
    unknown_page: "页面待识别",
    stale_data: "数据已过期",
  };
  const intent = signal.startsWith("conversation_intent:")
    ? signal.slice("conversation_intent:".length)
    : signal.startsWith("intent_")
      ? signal.slice("intent_".length)
      : null;
  if (intent) return conversationIntentLabels[intent] || "会话意图信号";
  return labels[signal] || signal;
}

export function VisitorsPage({
  sites,
  presence,
  presenceLoadState,
  presenceUpdatedAt,
  onOpenConversation,
  onRetryPresence,
}: CommonPageProps) {
  const [presenceQuery, setPresenceQuery] = useState("");
  const now = Date.now();
  const visiblePresence = useMemo(() => {
    const normalized = presenceQuery.trim().toLowerCase();
    if (!normalized) return presence;
    return presence.filter((item) =>
      [
        item.visitor_id,
        item.ip_address,
        item.country_code,
        countryDetails(item.country_code).name,
        item.page_title,
        item.page_path,
        item.referrer,
        item.browser,
        item.operating_system,
        item.timezone,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(normalized),
    );
  }, [presence, presenceQuery]);
  const onlinePresence = presence.filter(
    (item) => now - new Date(item.last_seen_at).getTime() <= 60_000,
  );
  const activePresence = visiblePresence.filter(
    (item) => now - new Date(item.last_seen_at).getTime() <= 60_000,
  );
  const idlePresence = visiblePresence.filter(
    (item) => now - new Date(item.last_seen_at).getTime() > 60_000,
  );
  const linkedConversationCount = onlinePresence.filter((item) => item.conversation_id).length;
  const openWidgetCount = onlinePresence.filter((item) => item.widget_state === "open").length;
  const totalPageViews = presence.reduce((total, item) => total + (item.page_view_count ?? 1), 0);
  const showMetrics = presenceLoadState === "ready" || presenceLoadState === "stale";
  const liveLabel = presenceLoadState === "ready"
    ? "实时更新"
    : presenceLoadState === "stale" && presenceUpdatedAt
      ? `连接中断 · ${new Date(presenceUpdatedAt).toLocaleTimeString("zh-CN", { hour12: false })}`
      : presenceLoadState === "loading" ? "正在连接" : "连接失败";

  return <PageCanvas>
    <MetricGrid items={[
      [showMetrics ? String(onlinePresence.length) : "—", "当前在线"],
      [showMetrics ? String(openWidgetCount) : "—", "已打开客服窗口"],
      [showMetrics ? String(linkedConversationCount) : "—", "已发起会话"],
      [showMetrics ? String(totalPageViews) : "—", "本次访问页数"],
    ]} />
    <section className="presence-panel" aria-labelledby="live-visitors-title">
      <div className="presence-toolbar">
        <div>
          <h2 id="live-visitors-title">实时访客</h2>
          <p>60 秒内为在线 · 5 分钟内显示最近离开</p>
        </div>
        <label className="presence-search">
          <Search aria-hidden="true" />
          <input value={presenceQuery} onChange={(event) => setPresenceQuery(event.target.value)} placeholder="搜索 IP、页面或设备" />
        </label>
        <span className={`presence-live ${presenceLoadState}`}><i aria-hidden="true" />{liveLabel}</span>
      </div>
      {presenceLoadState === "stale" && <div className="inline-error">连接暂时中断，正在显示最后一次成功获取的数据。</div>}
      <PresenceGroup
        title="活跃"
        tone="active"
        items={activePresence}
        total={visiblePresence.length}
        sites={sites}
        onOpenConversation={onOpenConversation}
      />
      <PresenceGroup
        title="最近离开"
        tone="idle"
        items={idlePresence}
        total={visiblePresence.length}
        sites={sites}
        onOpenConversation={onOpenConversation}
      />
      {presenceLoadState === "loading" && <PageEmpty title="正在获取在线访客" description="正在连接实时访客服务。" />}
      {presenceLoadState === "error" && <PageEmpty title="暂时无法获取在线访客" description="当前数据请求失败，请稍后重试。" actionLabel="重新加载" onAction={onRetryPresence} />}
      {presenceLoadState === "ready" && presence.length === 0 && <PageEmpty title="当前没有在线访客" description="访客进入已启用页面 Presence 的网站后，会显示在这里。" />}
      {(presenceLoadState === "ready" || presenceLoadState === "stale") && presence.length > 0 && visiblePresence.length === 0 && <PageEmpty title="没有匹配的访客" description="请调整搜索条件。" />}
    </section>
  </PageCanvas>;
}

function PresenceGroup({
  title,
  tone,
  items,
  total,
  sites,
  onOpenConversation,
}: {
  title: string;
  tone: "active" | "idle";
  items: VisitorPresence[];
  total: number;
  sites: Site[];
  onOpenConversation: (conversationId: string) => void;
}) {
  const [visibleLimit, setVisibleLimit] = useState(200);
  if (items.length === 0) return null;
  const renderedItems = items.slice(0, visibleLimit);
  const remainingItems = Math.max(0, items.length - renderedItems.length);
  return <section className={`presence-group ${tone}`}>
    <header><span aria-hidden="true" /><strong>{title}</strong><small>{items.length} / {total}</small></header>
    <div className="presence-table-wrap">
      <table className="presence-table">
        <thead><tr>
          <th><PresenceColumnLabel icon={UserRound} label="访客" /></th>
          <th><PresenceColumnLabel icon={MapPin} label="IP 地址" /></th>
          <th><PresenceColumnLabel icon={FileText} label="当前页面" /></th>
          <th><PresenceColumnLabel icon={Globe2} label="访问来源" /></th>
          <th><PresenceColumnLabel icon={MonitorSmartphone} label="设备环境" /></th>
          <th><PresenceColumnLabel icon={Clock3} label="当前页停留" /></th>
          <th><PresenceColumnLabel icon={Eye} label="浏览页数" /></th>
          <th><PresenceColumnLabel icon={MessagesSquare} label="关联会话" /></th>
        </tr></thead>
        <tbody>{renderedItems.map((item) => {
          const site = sites.find((candidate) => candidate.site_id === item.site_id);
          const pageUrl = site ? absolutePageUrl(site.base_url, item.page_path) : null;
          const country = countryDetails(item.country_code);
          const canOpenConversation = Boolean(item.conversation_id);
          return <tr key={`${item.site_id}:${item.visitor_id}`}>
            <td><div className="presence-visitor"><span className="presence-country" title={`${country.name} (${country.code})`}>{country.flagCode ? <span className={`presence-flag fi fi-${country.flagCode}`} aria-hidden="true" /> : <Globe2 className="presence-flag-unknown" aria-hidden="true" />}<em>{country.name}</em></span><div><strong>{shortVisitorId(item.visitor_id)}</strong><small>{site?.name || item.site_id}</small></div></div></td>
            <td><div className="presence-detail presence-ip-cell"><MapPin aria-hidden="true" /><code className="presence-ip">{item.ip_address || "未识别"}</code></div></td>
            <td><PresencePageCell item={item} pageUrl={pageUrl} /></td>
            <td><div className="presence-detail"><Globe2 aria-hidden="true" /><span title={item.referrer || ""}>{referrerLabel(item.referrer)}</span></div></td>
            <td><div className="presence-device"><Laptop aria-hidden="true" /><div><span>{deviceEnvironmentLabel(item.browser, item.operating_system)}</span><small>{deviceDetailsLabel(item.device_type, item.language)}</small></div></div></td>
            <td><div className="presence-detail"><Clock3 aria-hidden="true" /><span>{formatDuration(currentPageDwellSeconds(item))}</span></div></td>
            <td><div className="presence-detail"><Eye aria-hidden="true" /><strong className="presence-number">{item.page_view_count ?? 1}</strong></div></td>
            <td>{canOpenConversation ? <button className="presence-conversation" onClick={() => onOpenConversation(item.conversation_id || "")} title="打开会话"><MessagesSquare aria-hidden="true" /><span>打开</span></button> : <span className="presence-browsing"><i aria-hidden="true" />浏览中</span>}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    {remainingItems > 0 && <div className="presence-table-pagination">
      <span>已显示 {renderedItems.length} / {items.length}</span>
      <button
        type="button"
        className="secondary-small"
        onClick={() => setVisibleLimit((current) => current + 200)}
      >
        <ChevronDown aria-hidden="true" />
        再显示 {Math.min(200, remainingItems)} 位
      </button>
    </div>}
  </section>;
}

function PresenceColumnLabel({ icon: Icon, label }: { icon: LucideIcon; label: string }) {
  return <span className="presence-column-label"><Icon aria-hidden="true" />{label}</span>;
}

function PresencePageCell({ item, pageUrl }: { item: VisitorPresence; pageUrl?: string | null }) {
  const pageLabel = item.page_title || item.page_path;
  const fullUrl = pageUrl || item.page_path;
  return <div className="presence-page">
    {pageUrl ? <a className="presence-page-link" href={pageUrl} target="_blank" rel="noreferrer" title={fullUrl} aria-label={`打开当前页面：${pageLabel}`}>
      <span className="presence-page-title">{pageLabel}</span>
      <ExternalLink aria-hidden="true" />
    </a> : <strong className="presence-page-title" title={fullUrl}>{pageLabel}</strong>}
    <small title={fullUrl}>{item.page_path}</small>
  </div>;
}

function shortVisitorId(visitorId: string) {
  return visitorId.length > 20 ? `${visitorId.slice(0, 18)}…` : visitorId;
}

function absolutePageUrl(baseUrl: string, pagePath: string) {
  try { return new URL(pagePath, baseUrl).toString(); } catch { return null; }
}

export function countryDetails(countryCode: string | null | undefined) {
  const code = (countryCode || "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) {
    return { code: "--", flagCode: null, name: "未知地区" };
  }
  let name = code;
  try {
    name = new Intl.DisplayNames(["zh-CN"], { type: "region" }).of(code) || code;
  } catch {
    name = code;
  }
  return { code, flagCode: code.toLowerCase(), name };
}

function ConversationVisitorLocation({ item }: { item: InboxConversation }) {
  const country = countryDetails(item.visitor_country_code);
  return <div className="conversation-location history-location" title={`${country.name} (${country.code}) · ${item.visitor_ip_address || "IP 未记录"}`}>
    {country.flagCode ? <span className={`presence-flag fi fi-${country.flagCode}`} aria-hidden="true" /> : <Globe2 className="presence-flag-unknown" aria-hidden="true" />}
    <span>{country.name}</span>
    <code>{item.visitor_ip_address || "IP 未记录"}</code>
  </div>;
}

function referrerLabel(referrer: string | null | undefined) {
  if (!referrer) return "直接访问";
  try { return new URL(referrer).hostname.replace(/^www\./, ""); } catch { return "未知来源"; }
}

function deviceEnvironmentLabel(
  browser: string | null | undefined,
  operatingSystem: string | null | undefined,
) {
  const browserName = browser
    ? browser === "其他浏览器" ? browser : `${browser} 浏览器`
    : "浏览器未知";
  const systemName = operatingSystem
    ? operatingSystem === "其他系统" ? operatingSystem : `${operatingSystem} 系统`
    : "系统未知";
  return `${browserName} · ${systemName}`;
}

function deviceDetailsLabel(
  deviceType: string | null | undefined,
  language: string | null | undefined,
) {
  const deviceNames: Record<string, string> = {
    "桌面设备": "电脑端",
    "移动设备": "手机端",
    "平板": "平板端",
  };
  const labels = [deviceType ? deviceNames[deviceType] || deviceType : "设备类型未知"];
  if (language) {
    try {
      labels.push(new Intl.DisplayNames(["zh-CN"], { type: "language" }).of(language) || language);
    } catch {
      labels.push(language);
    }
  }
  return labels.join(" · ");
}

function elapsedTime(firstSeenAt: string, now: number) {
  const seconds = Math.max(0, Math.floor((now - new Date(firstSeenAt).getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}
export function CustomersPage({
  onOpenConversation,
  selectedSiteId,
  preferredCustomerId,
}: CommonPageProps & { selectedSiteId: string; preferredCustomerId: string | null }) {
  const [query, setQuery] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null);
  const [customers, setCustomers] = useState<CustomerDirectoryItem[]>([]);
  const [conversations, setConversations] = useState<InboxConversation[]>([]);
  const [customerNextCursor, setCustomerNextCursor] = useState<string | null>(null);
  const [customerTotal, setCustomerTotal] = useState<number | null>(null);
  const [conversationNextCursor, setConversationNextCursor] = useState<string | null>(null);
  const [conversationTotal, setConversationTotal] = useState<number | null>(null);
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const visibleCustomers = customers.filter((item) => `${item.display_name} ${item.customer_id}`.toLowerCase().includes(query.trim().toLowerCase()));
  const selected = customers.find((item) => item.customer_id === selectedCustomerId) || customers[0] || null;

  useEffect(() => {
    setLoading(true);
    setError("");
    listCustomers({ siteId: selectedSiteId || undefined, search: query.trim() || undefined })
      .then((result) => {
        setCustomers(result.items);
        setCustomerNextCursor(result.next_cursor);
        setCustomerTotal(result.total);
        setSelectedCustomerId((current) => preferredCustomerId || (current && result.items.some((item) => item.customer_id === current) ? current : result.items[0]?.customer_id || null));
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, [preferredCustomerId, query, selectedSiteId]);

  useEffect(() => {
    if (!selected) { setMemory([]); setConversations([]); return; }
    Promise.all([
      listCustomerConversations(selected.customer_id, selectedSiteId || undefined),
      listMemory(selected.customer_id),
    ]).then(([nextConversations, nextMemory]) => {
      setConversations(nextConversations.items);
      setConversationNextCursor(nextConversations.next_cursor);
      setConversationTotal(nextConversations.total);
      setMemory(nextMemory);
    }).catch((reason: Error) => setError(reason.message));
  }, [selected?.customer_id, selectedSiteId]);

  return <PageCanvas>
    <MetricGrid items={[
      [String(customerTotal ?? customers.length), "可信客户"],
      [String(customers.filter((item) => item.conversation_count > 1).length), "重复咨询客户"],
      [String(customers.reduce((total, item) => total + item.conversation_count, 0)), "关联会话"],
      [String(customers.filter((item) => item.last_conversation_at && Date.now() - new Date(item.last_conversation_at).getTime() < 7 * 86400_000).length), "近 7 天活跃"],
    ]} />
    {error && <InlineError message={error} />}
    <div className="split-page">
      <section className="surface-panel customer-directory"><Toolbar><div className="search-box wide">⌕ <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索客户 ID 或名称…" /></div></Toolbar>
        <div className="directory-list">{visibleCustomers.map((customer) => <button className={`directory-item ${selected?.customer_id === customer.customer_id ? "active" : ""}`} key={customer.customer_id} onClick={() => setSelectedCustomerId(customer.customer_id)}><span className="mini-avatar">{initials(customer.display_name)}</span><div><strong>{customer.display_name}</strong><small>{customer.customer_id}</small></div><b>{customer.conversation_count}</b></button>)}</div>
        {customerNextCursor && <button className="secondary-button" onClick={() => listCustomers({ siteId: selectedSiteId || undefined, search: query.trim() || undefined, cursor: customerNextCursor }).then((result) => { setCustomers((current) => [...current, ...result.items]); setCustomerNextCursor(result.next_cursor); }).catch((reason: Error) => setError(reason.message))}>加载更多客户</button>}
        {!loading && visibleCustomers.length === 0 && <PageEmpty title="没有匹配的客户" description="这里只显示已建立可信身份的客户。" />}
      </section>
      <section className="surface-panel detail-panel">{selected ? <>
        <div className="detail-heading"><div className="large-avatar">{initials(selected.display_name)}</div><div><h2>{selected.display_name}</h2><p>{selected.customer_id}</p></div><StateBadge value="可信身份" tone="green" /></div>
        <SectionTitle title="会话历史" description={`${conversationTotal ?? selected.conversation_count} 条会话`} />
        <div className="compact-list">{conversations.map((conversation) => <button key={conversation.conversation_id} onClick={() => onOpenConversation(conversation.conversation_id)}><div><strong>{conversation.last_message_preview || "暂无摘要"}</strong><small>{ownershipLabel(conversation.ownership_mode)} · {relativeTime(conversation.updated_at)}</small></div><span>查看</span></button>)}</div>
        {conversationNextCursor && <button className="secondary-button" onClick={() => listCustomerConversations(selected.customer_id, selectedSiteId || undefined, conversationNextCursor).then((result) => { setConversations((current) => [...current, ...result.items]); setConversationNextCursor(result.next_cursor); }).catch((reason: Error) => setError(reason.message))}>加载更多会话</button>}
        <SectionTitle title="客户记忆" description="仅展示经过同意或人工确认的长期记忆" />
        {memory.length ? <div className="memory-grid">{memory.map((item) => <div className="memory-card" key={item.memory_id}><span>{memoryKind(item.kind)}</span><p>{item.content}</p><small>可信度 {Math.round(item.confidence * 100)}%</small></div>)}</div> : <p className="muted-copy">暂无可用长期记忆。</p>}
      </> : <PageEmpty title={loading ? "正在加载客户" : "暂无可信客户"} description="完成可信身份识别后，客户会出现在这里。" />}</section>
    </div>
  </PageCanvas>;
}

export function ReportsPage({ selectedSiteId }: CommonPageProps & { selectedSiteId: string }) {
  const [days, setDays] = useState(30);
  const [analytics, setAnalytics] = useState<SupportAnalytics | null>(null);
  const [experience, setExperience] = useState<CustomerExperienceSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    Promise.all([getSupportAnalytics(days, selectedSiteId || undefined), getExperienceSummary(days, selectedSiteId || undefined)])
      .then(([nextAnalytics, nextExperience]) => { setAnalytics(nextAnalytics); setExperience(nextExperience); })
      .catch((reason: Error) => setError(reason.message));
  }, [days, selectedSiteId]);

  const percent = (value: number) => Math.round(value * 100) + "%";
  const denominator = Math.max(analytics?.agent_runs || 0, analytics?.conversations || 0, 1);
  return <PageCanvas>
    <Toolbar>
      <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
        <option value={7}>最近 7 天</option><option value={30}>最近 30 天</option><option value={90}>最近 90 天</option>
      </select>
      <span className="scope-note">站点范围由顶部全局选择器统一控制</span>
    </Toolbar>
    {error && <div className="form-error">{error}</div>}
    <MetricGrid items={analytics ? [
      [String(analytics.conversations), "会话数"],
      [percent(analytics.eligible_ai_answer_rate), "可处理咨询 AI 回复率"],
      [String(analytics.forced_order_handoffs), "订单强制转人工"],
      [percent(analytics.resolution_rate), "会话解决率"],
    ] : [["—", "会话数"], ["—", "可处理咨询 AI 回复率"], ["—", "订单强制转人工"], ["—", "会话解决率"]]} />
    <MetricGrid items={analytics ? [
      [formatDuration(analytics.first_response_p95_seconds), "95% 会话的响应时间"],
      [formatDuration(analytics.average_human_response_seconds), "人工首次回复时间"],
      [formatDuration(analytics.average_resolution_seconds), "平均解决时间"],
      [String(analytics.unread_conversations), "未读会话"],
      [String(analytics.waiting_human_conversations), "等待人工"],
    ] : [["—", "95% 会话的响应时间"], ["—", "人工首次回复时间"], ["—", "平均解决时间"], ["—", "未读会话"], ["—", "等待人工"]]} />
    <MetricGrid items={experience ? [[experience.average_satisfaction ? experience.average_satisfaction.toFixed(1) + "/5" : "—", "客户满意度"], [String(experience.satisfaction_count), "收到的评价"], [(experience.satisfaction_response_rate * 100).toFixed(1) + "%", "评价提交率"], [String(experience.open_knowledge_gaps), "待修复知识缺口"]] : [["—", "客户满意度"], ["—", "收到的评价"], ["—", "评价提交率"], ["—", "待修复知识缺口"]]} />
    <div className="report-grid">
      <section className="surface-panel chart-panel"><SectionTitle title="真实回复漏斗" description="根据真实会话、运行和消息记录计算，帮助判断 AI 与人工处理效果" />
        {analytics && <div className="bar-chart">
          {[["智能体运行", analytics.agent_runs], ["AI 有效回答", analytics.ai_answers], ["人工转接", analytics.handoffs], ["人工已回复会话", analytics.human_replied_conversations], ["已解决会话", analytics.resolved_conversations]].map(([label, count]) => <div className="bar-row" key={String(label)}><span>{label}</span><div><i className="indigo" style={{ width: Math.max(3, (Number(count) / denominator) * 100) + "%" }} /></div><b>{count}</b></div>)}
        </div>}
      </section>
      <section className="surface-panel report-note"><h3>怎么看这些数字</h3><p>优先关注回复速度、解决率和客户满意度。可处理咨询 AI 回复率会排除必须人工处理的订单、物流、退款、取消、支付和地址问题。</p><details className="advanced-diagnostics"><summary>查看指标定义</summary><dl><div><dt>95% 会话的响应时间</dt><dd>{metricHelp.firstResponse95}</dd></div><div><dt>人工首次回复时间</dt><dd>{metricHelp.humanResponse}</dd></div><div><dt>平均解决时间</dt><dd>{metricHelp.resolutionTime}</dd></div><div><dt>客户满意度</dt><dd>{metricHelp.satisfaction}</dd></div></dl></details></section>
    </div>
  </PageCanvas>;
}

interface SettingsPageProps extends CommonPageProps {
  supportConfiguration: SupportConfiguration;
  onSupportConfigurationChanged: () => Promise<void>;
  onPasswordChanged: () => void;
  onSitesChanged: () => Promise<void>;
}

export function SettingsPage({ sites, user, supportConfiguration, onSupportConfigurationChanged, onPasswordChanged, onSitesChanged }: SettingsPageProps) {
  const [settingsSection, setSettingsSection] = useState<"workspace" | "team" | "experience" | "security">("workspace");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const accountDisplayName = formatAccountDisplayName(user.display_name, user.roles);
  const securityChecks = [
    ["登录会话保护", true], ["连续登录失败保护", true], ["浏览器会话安全", true], ["工作区数据隔离", true], ["角色权限控制", true], ["安全连接", window.location.protocol === "https:"],
  ] as const;

  async function submitPasswordChange(event: React.FormEvent) {
    event.preventDefault();
    setPasswordError("");
    if (newPassword.length < 12) {
      setPasswordError("新密码至少需要 12 个字符。");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("两次输入的新密码不一致。");
      return;
    }
    setChangingPassword(true);
    try {
      await changePassword({ current_password: currentPassword, new_password: newPassword });
      onPasswordChanged();
    } catch (error) {
      setPasswordError(error instanceof Error ? error.message : "修改密码失败");
    } finally {
      setChangingPassword(false);
    }
  }

  return <PageCanvas>
    <nav className="settings-page-nav" aria-label="设置分类">
      <button className={settingsSection === "workspace" ? "active" : ""} onClick={() => setSettingsSection("workspace")}><Building2 aria-hidden="true" /><span><strong>工作区与站点</strong><small>站点、工作区与连接配置</small></span></button>
      <button className={settingsSection === "team" ? "active" : ""} onClick={() => setSettingsSection("team")}><Users aria-hidden="true" /><span><strong>团队与权限</strong><small>成员、邀请与角色范围</small></span></button>
      <button className={settingsSection === "experience" ? "active" : ""} onClick={() => setSettingsSection("experience")}><MessagesSquare aria-hidden="true" /><span><strong>客服体验</strong><small>网站客服窗口、快捷回复与知识缺口</small></span></button>
      <button className={settingsSection === "security" ? "active" : ""} onClick={() => setSettingsSection("security")}><ShieldCheck aria-hidden="true" /><span><strong>安全与审计</strong><small>登录、会话、备份与日志</small></span></button>
    </nav>
    <div className="settings-grid">
      {settingsSection === "workspace" && <>
        <section className="surface-panel settings-card"><SectionTitle title="当前管理员" description="身份来自可信会话，不来自聊天内容" /><div className="account-card"><div className="large-avatar">{initials(accountDisplayName)}</div><div><h3>{accountDisplayName}</h3><p>{user.username} · {terminology.workspace}</p></div></div><TagList values={user.roles.length > 0 ? user.roles.map(formatRoleLabel) : ["未分配角色"]} /></section>
        <section className="surface-panel settings-card"><SectionTitle title="当前工作区" description="全局站点与工作区范围由顶部选择器控制" /><div className="workspace-summary"><strong>{sites.length}</strong><span>个已配置站点</span><small>{user.tenant_id}</small></div></section>
        {user.scopes.includes("sites:manage") ? <SiteManagement onSitesChanged={onSitesChanged} /> : <section className="surface-panel settings-card span-two"><SectionTitle title="站点工作空间" description="当前账号仅有查看权限" /><DataTable headers={["站点", "Site ID", "地址", "状态"]}>{sites.map((site) => <tr key={site.site_id}><td>{site.name}</td><td><code>{site.site_id}</code></td><td>{site.base_url || "尚未配置"}</td><td><StateBadge value={site.status} tone={site.status === "active" ? "green" : "neutral"} /></td></tr>)}</DataTable></section>}
      </>}
      {settingsSection === "team" && <>
        <section className="surface-panel settings-card span-two"><SectionTitle title="权限范围" description="当前账号的权限由工作区角色确定性映射" /><TagList values={user.scopes} /></section>
        {user.scopes.includes("support:inbox:manage") && <SupportQueueManagement sites={sites} configuration={supportConfiguration} onChanged={onSupportConfigurationChanged} />}
        {user.scopes.includes("users:manage") && <InvitationManagement currentUser={user} />}
        {user.scopes.includes("users:manage") && user.authentication_method === "local" ? <TeamManagement currentUser={user} /> : <section className="surface-panel settings-card span-two"><SectionTitle title="团队管理" description="当前账号没有本地团队管理权限" /><p className="muted-copy">成员与角色调整需要工作区管理员权限。</p></section>}
      </>}
      {settingsSection === "experience" && <>
        <CannedReplyManagement configuration={supportConfiguration} onChanged={onSupportConfigurationChanged} />
        {user.scopes.includes("sites:manage") && <WidgetConfigurationManagement sites={sites} />}
        <KnowledgeGapManagement />
      </>}
      {settingsSection === "security" && <>
        {["local", "email_password"].includes(user.authentication_method) ? <section className="surface-panel settings-card span-two"><SectionTitle title="修改登录密码" description="修改成功后会撤销当前账号的全部会话，并要求重新登录" /><form className="password-form" onSubmit={submitPasswordChange}><label><span>当前密码</span><input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label><label><span>新密码</span><input type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={12} required /></label><label><span>确认新密码</span><input type="password" autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} minLength={12} required /></label>{passwordError && <div className="inline-error">{passwordError}</div>}<div className="password-form-footer"><small>至少 12 个字符；建议使用密码管理器生成随机密码。</small><button className="primary-small" type="submit" disabled={changingPassword || !currentPassword || !newPassword || !confirmPassword}>{changingPassword ? "修改中…" : "修改密码并退出"}</button></div></form></section> : <section className="surface-panel settings-card span-two"><SectionTitle title="外部统一身份" description="身份由公司登录提供方验证，本系统只保存工作区成员关系和本地安全会话" /><div className="identity-summary"><StateBadge value="身份已验证" tone="blue" /><span>切换工作区会轮换会话并清空原工作区连接。</span></div></section>}
        <SessionManagement onCurrentSessionRevoked={onPasswordChanged} />
        {user.scopes.includes("audit:read") && <SystemStatusPanel />}
        {user.scopes.includes("audit:read") && <AuditLogPanel />}
        <section className="surface-panel settings-card span-two"><SectionTitle title="安全检查" description="正式公网部署前必须全部通过" /><div className="security-checks">{securityChecks.map(([label, ok]) => <div key={label}><span className={ok ? "check-ok" : "check-warning"}>{ok ? "✓" : "!"}</span><strong>{label}</strong><small>{ok ? "已启用" : "部署时配置"}</small></div>)}</div></section>
      </>}
    </div>
  </PageCanvas>;
}

function SupportQueueManagement({ sites, configuration, onChanged }: { sites: Site[]; configuration: SupportConfiguration; onChanged: () => Promise<void> }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [siteId, setSiteId] = useState("");
  const [selectedQueue, setSelectedQueue] = useState<SupportQueue | null>(null);
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function loadMembers(queue: SupportQueue) {
    setSelectedQueue(queue); setError("");
    try { setMemberIds((await listSupportQueueMembers(queue.queue_id)).items.map((item) => item.agent_id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "读取客服分组成员失败"); }
  }

  async function createQueue(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      await createSupportQueue({ name, description, site_id: siteId || null, idempotency_key: crypto.randomUUID() });
      setName(""); setDescription(""); setSiteId(""); setNotice("客服分组已创建。"); await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建客服分组失败"); }
    finally { setBusy(false); }
  }

  async function saveMembers() {
    if (!selectedQueue) return;
    setBusy(true); setError(""); setNotice("");
    try { await updateSupportQueueMembers(selectedQueue.queue_id, memberIds); setNotice("分组成员已更新。"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "更新分组成员失败"); }
    finally { setBusy(false); }
  }

  async function changeQueue(queue: SupportQueue, values: { status?: "active" | "disabled"; is_default?: boolean }) {
    setBusy(true); setError(""); setNotice("");
    try {
      await updateSupportQueue(queue.queue_id, { ...values, idempotency_key: crypto.randomUUID() });
      setNotice(values.is_default ? "默认客服分组已更新。" : "客服分组状态已更新。");
      if (values.status === "disabled" && selectedQueue?.queue_id === queue.queue_id) setSelectedQueue(null);
      await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "更新客服分组失败"); }
    finally { setBusy(false); }
  }

  return <section className="surface-panel settings-card span-two queue-management">
    <SectionTitle title="客服分组" description="按售前、售后、物流或投诉组织队列与成员" />
    {error && <InlineError message={error} />}{notice && <div className="inline-success">{notice}</div>}
    <form className="queue-create-form" onSubmit={createQueue}><label><span>分组名称</span><input value={name} onChange={(event) => setName(event.target.value)} required maxLength={120} /></label><label><span>用途说明</span><input value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} /></label><label><span>绑定站点</span><select value={siteId} onChange={(event) => setSiteId(event.target.value)}><option value="">全部站点</option>{sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.name}</option>)}</select></label><button className="primary-small" disabled={busy}>新建分组</button></form>
    <div className="queue-management-layout"><div className="queue-list">{configuration.queues.map((queue) => <article key={queue.queue_id} className={selectedQueue?.queue_id === queue.queue_id ? "selected" : ""}><button className="queue-main" onClick={() => void loadMembers(queue)}><strong>{queue.name}</strong><small>{queue.description || "未填写说明"}</small></button><div className="queue-actions">{queue.is_default ? <StateBadge value="默认" tone="blue" /> : <button className="secondary-small" disabled={busy || queue.status === "disabled"} onClick={() => void changeQueue(queue, { is_default: true })}>设为默认</button>}<button className="secondary-small" disabled={busy || queue.is_default} onClick={() => void changeQueue(queue, { status: queue.status === "disabled" ? "active" : "disabled" })}>{queue.status === "disabled" ? "启用" : "停用"}</button></div></article>)}</div>
      {selectedQueue && <div className="queue-members"><h4>{selectedQueue.name}成员</h4>{configuration.agents.map((agent) => <label key={agent.agent_id}><input type="checkbox" checked={memberIds.includes(agent.agent_id)} onChange={(event) => setMemberIds((items) => event.target.checked ? [...items, agent.agent_id] : items.filter((id) => id !== agent.agent_id))} /><span>{agent.display_name}</span></label>)}{configuration.agents.length === 0 && <p className="muted-copy">暂无可分配的有效客服。</p>}<button className="primary-small" disabled={busy} onClick={() => void saveMembers()}>保存成员</button></div>}
    </div>
  </section>;
}

function CannedReplyManagement({ configuration, onChanged }: { configuration: SupportConfiguration; onChanged: () => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [shortcut, setShortcut] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await createCannedReply({ title, shortcut, content });
      setTitle(""); setShortcut(""); setContent("");
      await onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建快捷回复失败");
    } finally { setBusy(false); }
  }
  return <section className="surface-panel settings-card span-two canned-reply-management"><SectionTitle title="快捷回复" description="客服在会话输入区可以直接插入；内容仅由人工确认后发送" /><form onSubmit={submit}><label><span>名称</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：物流查询" required /></label><label><span>快捷指令</span><input value={shortcut} onChange={(event) => setShortcut(event.target.value)} placeholder="shipping" pattern="[A-Za-z0-9_-]+" required /></label><label className="reply-content"><span>回复内容</span><textarea value={content} onChange={(event) => setContent(event.target.value)} required /></label><button className="primary-small" disabled={busy} type="submit">{busy ? "创建中…" : "创建快捷回复"}</button></form>{error && <InlineError message={error} />}<div className="canned-reply-list">{configuration.canned_replies.map((reply) => <article key={reply.reply_id}><strong>{reply.title}</strong><code>/{reply.shortcut}</code><p>{reply.content}</p></article>)}{configuration.canned_replies.length === 0 && <p className="muted-copy">暂无快捷回复。</p>}</div></section>;
}

function SessionManagement({ onCurrentSessionRevoked }: { onCurrentSessionRevoked: () => void }) {
  const [sessions, setSessions] = useState<AdminSessionItem[]>([]);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  async function refreshSessions() {
    try {
      setSessions(await listAdminSessions());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取登录会话失败");
    }
  }

  useEffect(() => {
    void refreshSessions();
  }, []);

  async function revokeSession(item: AdminSessionItem) {
    const prompt = item.is_current
      ? "退出当前设备后需要重新登录，确认继续吗？"
      : "确认让这个登录会话立即失效吗？";
    if (!window.confirm(prompt)) return;
    setBusyId(item.session_id);
    try {
      const result = await revokeAdminSession(item.session_id);
      if (result.was_current) {
        onCurrentSessionRevoked();
        return;
      }
      await refreshSessions();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "撤销登录会话失败");
    } finally {
      setBusyId("");
    }
  }

  return <section className="surface-panel settings-card span-two session-management">
    <SectionTitle title="登录设备与会话" description="仅显示脱敏来源指纹；可选择性让异常登录立即失效" />
    {error && <InlineError message={error} />}
    <DataTable headers={["登录时间", "最近活动", "到期时间", "来源指纹", "状态", ""]}>
      {sessions.map((item) => {
        const expired = new Date(item.expires_at).getTime() <= Date.now();
        const active = !item.revoked_at && !expired;
        return <tr key={item.session_id}>
          <td>{formatDateTime(item.created_at)}</td>
          <td>{relativeTime(item.last_seen_at)}</td>
          <td>{formatDateTime(item.expires_at)}</td>
          <td><code>{item.source_fingerprint_prefix}</code></td>
          <td><StateBadge value={item.is_current ? "当前设备" : active ? "有效" : "已失效"} tone={item.is_current || active ? "green" : "neutral"} /></td>
          <td><button className={item.is_current ? "danger-outline" : "table-action"} disabled={!active || busyId === item.session_id} onClick={() => void revokeSession(item)}>{busyId === item.session_id ? "处理中…" : item.is_current ? "退出当前设备" : "强制下线"}</button></td>
        </tr>;
      })}
    </DataTable>
    {sessions.length === 0 && !error && <PageEmpty title="暂无登录会话" description="成功登录后会在这里显示当前设备。" />}
  </section>;
}

function AuditLogPanel() {
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [eventType, setEventType] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [correlationId, setCorrelationId] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function loadAudit(append: boolean, cursor?: string) {
    setLoading(true);
    try {
      const result = await listAuditEvents({
        eventType: eventType.trim() || undefined,
        resourceType: resourceType.trim() || undefined,
        correlationId: correlationId.trim() || undefined,
        cursor,
      });
      setItems((current) => append ? [...current, ...result.items] : result.items);
      setNextCursor(result.next_cursor);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取审计日志失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAudit(false);
  }, []);

  return <section className="surface-panel settings-card span-two audit-log-panel">
    <SectionTitle title="审计日志" description="按工作区隔离，并自动隐藏密码、令牌和站点密钥等敏感字段" />
    <div className="audit-filters">
      <label><span>事件类型</span><input value={eventType} onChange={(event) => setEventType(event.target.value)} placeholder="例如 admin_user.updated" /></label>
      <label><span>资源类型</span><input value={resourceType} onChange={(event) => setResourceType(event.target.value)} placeholder="例如 admin_user" /></label>
      <label><span>{terminology.requestTraceId}</span><input value={correlationId} onChange={(event) => setCorrelationId(event.target.value)} placeholder="精确查询一次请求" title={helpText.requestTraceId} /></label>
      <button className="primary-small" disabled={loading} onClick={() => void loadAudit(false)}>{loading ? "查询中…" : "查询"}</button>
    </div>
    {error && <InlineError message={error} />}
    <DataTable headers={["时间", "事件", "操作者", "资源", terminology.requestTraceId, "详情"]}>
      {items.map((item) => <tr key={item.event_id}>
        <td>{formatDateTime(item.created_at)}</td>
        <td><EntityCell title={auditEventLabel(item.event_type)} subtitle={item.event_type} /></td>
        <td><code>{item.actor_subject_id || "system"}</code></td>
        <td><EntityCell title={item.resource_type} subtitle={item.resource_id} /></td>
        <td><code>{item.correlation_id || "-"}</code></td>
        <td><details className="audit-details"><summary>查看</summary><pre>{JSON.stringify(item.details, null, 2)}</pre></details></td>
      </tr>)}
    </DataTable>
    {items.length === 0 && !loading && !error && <PageEmpty title="没有匹配的审计记录" description="调整筛选条件后重新查询。" />}
    {nextCursor && <div className="audit-load-more"><button className="secondary-button" disabled={loading} onClick={() => void loadAudit(true, nextCursor)}>{loading ? "加载中…" : "加载更多"}</button></div>}
  </section>;
}

function SystemStatusPanel() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const result = await getSystemStatus();
        if (!cancelled) {
          setStatus(result);
          setError("");
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "读取系统状态失败");
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return <section className="surface-panel settings-card span-two system-status-panel">
    <SectionTitle title="系统运行状态" description="每 30 秒检查客服服务、请求错误率和备份状态" />
    {error && <InlineError message={error} />}
    {!status ? <PageEmpty title="正在读取系统状态" description="稍候将显示依赖健康和请求指标。" /> : <>
      <div className="status-summary">
        <div className={status.is_ready ? "status-hero healthy" : "status-hero unhealthy"}><span>{status.is_ready ? "✓" : "!"}</span><div><strong>{status.is_ready ? "客服服务正常" : "客服服务异常"}</strong><small>{status.failed_dependencies.length ? "需要关注：" + status.failed_dependencies.join("、") : "当前没有发现会影响客服使用的问题"}</small></div></div>
        <MetricGrid items={[
          [String(status.metrics.request_count || 0), "进程启动后请求"],
          [String(status.metrics.responses_5xx || 0), "服务器错误"],
          [(status.metrics.average_latency_ms || 0).toFixed(1) + " ms", "平均响应延迟"],
          [(status.metrics.maximum_latency_ms || 0).toFixed(1) + " ms", "最大响应延迟"],
        ]} />
      </div>
      <details className="advanced-diagnostics"><summary>高级诊断</summary><p>{helpText.advancedDiagnostics}</p><div className="runtime-grid">
        <RuntimeItem label="运行环境" value={status.configuration.app_env} healthy={status.configuration.app_env === "production"} />
        <RuntimeItem label="管理员认证" value={status.configuration.auth_mode} healthy={status.configuration.auth_mode === "session"} />
        <RuntimeItem label="聊天模型" value={status.configuration.llm_provider} healthy={status.configuration.llm_provider !== "fake"} />
        <RuntimeItem label="Embedding" value={status.configuration.embedding_provider} healthy={status.configuration.embedding_provider !== "fake"} />
        <RuntimeItem label="实时消息" value={status.configuration.realtime_backend} healthy={status.configuration.horizontal_scaling_ready} />
        <RuntimeItem label="在线状态" value={status.configuration.presence_backend} healthy={status.configuration.horizontal_scaling_ready} />
      </div>
      <div className="backup-status-grid">
        {status.backups.map((backup) => <div className={backup.state === "current" ? "backup-status-card current" : "backup-status-card warning"} key={backup.artifact_type}>
          <span>{backup.state === "current" ? "✓" : "!"}</span>
          <div><strong>{backup.artifact_type === "postgres" ? "PostgreSQL 备份" : "Qdrant 备份"}</strong><small>{backup.state === "missing" ? "尚未生成备份状态" : backup.state === "stale" ? "备份已超过时效" : "备份在有效时限内"}</small></div>
          <dl><dt>最近完成</dt><dd>{backup.completed_at ? formatDateTime(backup.completed_at) : "-"}</dd><dt>文件大小</dt><dd>{backup.size_bytes === null ? "-" : formatBytes(backup.size_bytes)}</dd><dt>恢复验证</dt><dd>{backup.restore_verified_at ? formatDateTime(backup.restore_verified_at) : "尚未演练"}</dd></dl>
        </div>)}
      </div></details>
      {!status.configuration.horizontal_scaling_ready && <div className="architecture-warning"><strong>当前必须保持单 API 实例</strong><p>实时消息与在线状态仍使用进程内存。升级为 Redis/NATS 前，不可横向扩容 API。</p></div>}
    </>}
  </section>;
}

function RuntimeItem({ label, value, healthy }: { label: string; value: string; healthy: boolean }) {
  return <div className="runtime-item"><span className={healthy ? "check-ok" : "check-warning"}>{healthy ? "✓" : "!"}</span><div><strong>{label}</strong><small>{value}</small></div></div>;
}

function SiteManagement({ onSitesChanged }: { onSitesChanged: () => Promise<void> }) {
  const [sites, setSites] = useState<ManagedSite[]>([]);
  const [siteId, setSiteId] = useState("");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [primaryLanguage, setPrimaryLanguage] = useState("en");
  const [revealedKey, setRevealedKey] = useState<{ siteId: string; key: string } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setSites(await listManagedSites());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载站点失败");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function createSite(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await createManagedSite({
        site_id: siteId,
        name,
        base_url: baseUrl,
        primary_language: primaryLanguage,
      });
      setSiteId("");
      setName("");
      setBaseUrl("");
      setPrimaryLanguage("en");
      setNotice("站点已创建。复制下方安装代码并放入网站公共页脚即可启用客服。");
      await Promise.all([refresh(), onSitesChanged()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建站点失败");
    } finally {
      setBusy(false);
    }
  }

  return <section className="surface-panel settings-card span-two site-management">
    <SectionTitle title="网站接入" description="创建网站后复制一行安装代码，无需配置服务器密钥或 Cloudflare Worker" />
    <form className="site-create-form" onSubmit={createSite}>
      <label><span>Site ID</span><input value={siteId} onChange={(event) => setSiteId(event.target.value.toLowerCase())} placeholder="例如 brand-cn" pattern="[a-z0-9-]+" required /></label>
      <label><span>站点名称</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label>
      <label><span>网站地址</span><input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://www.example.com" required /></label>
      <label><span>网站主语言</span><select value={primaryLanguage} onChange={(event) => setPrimaryLanguage(event.target.value)}><LanguageOptions /></select></label>
      <button className="primary-small" disabled={busy} type="submit">{busy ? "创建中…" : "创建站点"}</button>
    </form>
    {error && <InlineError message={error} />}
    {notice && <div className="inline-success">{notice}</div>}
    {revealedKey && <div className="secret-reveal"><div><strong>{revealedKey.siteId} 的网站接入密钥</strong><code>{revealedKey.key}</code></div><button className="primary-small" type="button" onClick={() => navigator.clipboard.writeText(revealedKey.key)}>复制密钥</button><button className="ghost-button" type="button" onClick={() => setRevealedKey(null)}>我已保存</button></div>}
    <div className="managed-site-list">
      {sites.map((item) => <ManagedSiteRow key={item.site_id} item={item} onChanged={async () => { await Promise.all([refresh(), onSitesChanged()]); }} onReveal={setRevealedKey} onError={setError} onNotice={setNotice} />)}
    </div>
  </section>;
}

function ManagedSiteRow({ item, onChanged, onReveal, onError, onNotice }: { item: ManagedSite; onChanged: () => Promise<void>; onReveal: (value: { siteId: string; key: string }) => void; onError: (message: string) => void; onNotice: (message: string) => void }) {
  const [name, setName] = useState(item.name);
  const [baseUrl, setBaseUrl] = useState(item.base_url);
  const [status, setStatus] = useState<"active" | "disabled">(item.status);
  const [primaryLanguage, setPrimaryLanguage] = useState(item.primary_language);
  const [busy, setBusy] = useState(false);
  const [verificationMethod, setVerificationMethod] = useState<"dns_txt" | "script">("dns_txt");
  const [challenge, setChallenge] = useState<SiteVerificationChallenge | null>(null);

  async function copyInstallCode() {
    await navigator.clipboard.writeText(item.install_code);
    onNotice("安装代码已复制。将它放到网站公共页脚的 </body> 前即可。");
  }

  async function save() {
    setBusy(true);
    onError("");
    onNotice("");
    try {
      await updateManagedSite(item.site_id, {
        name,
        base_url: baseUrl,
        status,
        primary_language: primaryLanguage,
      });
      onNotice("站点 " + item.site_id + " 已更新；停用后网站客服窗口将无法连接。");
      await onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "更新站点失败");
    } finally {
      setBusy(false);
    }
  }

  async function rotateKey() {
    if (!window.confirm("轮换后旧密钥会立即失效。确认继续吗？")) return;
    const siteKey = generateSiteKey();
    setBusy(true);
    onError("");
    onNotice("");
    try {
      await rotateManagedSiteKey(item.site_id, siteKey);
      onReveal({ siteId: item.site_id, key: siteKey });
      onNotice("密钥已轮换。请立即复制新密钥并更新网站接入组件配置。");
      await onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "轮换密钥失败");
    } finally {
      setBusy(false);
    }
  }

  async function startVerification() {
    setBusy(true);
    onError("");
    try {
      setChallenge(await issueSiteVerificationChallenge(item.site_id, verificationMethod));
      onNotice("验证挑战已生成。完成 DNS 或安装脚本配置后，点击检查验证。");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "生成验证挑战失败");
    } finally {
      setBusy(false);
    }
  }

  async function checkVerification() {
    setBusy(true);
    onError("");
    try {
      await verifyManagedSite(item.site_id, verificationMethod);
      setChallenge(null);
      onNotice("站点已验证，Widget 和知识同步已解锁。");
      await onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "站点验证失败");
    } finally {
      setBusy(false);
    }
  }

  return <article className="managed-site-card">
    <div className="managed-site-heading"><div><strong>{item.name}</strong><small>{item.public_widget_id} · 每日额度 {item.widget_daily_message_limit}</small></div><StateBadge value={item.status === "active" ? "启用" : "停用"} tone={item.status === "active" ? "green" : "neutral"} /></div>
    <div className="managed-site-fields"><label><span>名称</span><input value={name} onChange={(event) => setName(event.target.value)} /></label><label><span>地址</span><input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label><label><span>网站主语言</span><select value={primaryLanguage} onChange={(event) => setPrimaryLanguage(event.target.value)}><LanguageOptions /></select></label><label><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value as "active" | "disabled")}><option value="active">启用</option><option value="disabled">停用</option></select></label></div>
    <div className="install-code-block"><div><span>安装代码</span><code>{item.install_code}</code><small>允许域名：{item.allowed_origins.join("、")}</small></div><button className="primary-small" type="button" onClick={copyInstallCode}>复制安装代码</button></div>
    <div className="managed-site-actions"><button className="secondary-button" disabled={busy} type="button" onClick={save}>保存站点</button><details className="advanced-connector"><summary>网站接入高级设置</summary><button className="danger-outline" disabled={busy} type="button" onClick={rotateKey}>{item.credential_key_prefix ? "轮换私有密钥" : "生成私有密钥"}</button></details></div>
    <div className="site-verification-panel"><div><strong>站点验证</strong><StateBadge value={item.verification_status === "verified" ? "已验证" : "待验证"} tone={item.verification_status === "verified" ? "green" : "amber"} /></div>{item.verification_status !== "verified" && <div className="site-verification-controls"><select value={verificationMethod} onChange={(event) => setVerificationMethod(event.target.value as "dns_txt" | "script")}><option value="dns_txt">DNS TXT</option><option value="script">安装脚本</option></select><button className="secondary-button" disabled={busy} type="button" onClick={() => void startVerification()}>生成挑战</button>{challenge && <button className="primary-small" disabled={busy} type="button" onClick={() => void checkVerification()}>检查验证</button>}</div>}{challenge && <div className="site-verification-challenge"><small>{challenge.method === "dns_txt" ? `TXT ${challenge.dns_name}` : `GET ${challenge.script_path}`}</small><code>{challenge.method === "dns_txt" ? challenge.dns_value : challenge.script_value}</code><small>有效期至 {formatDateTime(challenge.expires_at)}</small></div>}</div>
    <SiteWebSourceEditor siteId={item.site_id} baseUrl={item.base_url} />
  </article>;
}

function LanguageOptions() {
  return <><option value="en">English</option><option value="zh-CN">简体中文</option><option value="ja">日本語</option><option value="ko">한국어</option><option value="es">Español</option><option value="fr">Français</option><option value="de">Deutsch</option><option value="pt">Português</option></>;
}

function generateSiteKey() {
  const bytes = new Uint8Array(32);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function InvitationManagement({ currentUser }: { currentUser: AdminUser }) {
  const [items, setItems] = useState<TenantInvitation[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<(typeof adminRoleOptions)[number]>("support_agent");
  const [expiresInHours, setExpiresInHours] = useState(72);
  const [latestLink, setLatestLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function refresh() {
    setItems(await listTenantInvitations(currentUser.tenant_id));
  }

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [currentUser.tenant_id]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice(""); setLatestLink("");
    try {
      const invitation = await createTenantInvitation(currentUser.tenant_id, {
        email,
        roles: [role],
        expires_in_hours: expiresInHours,
      });
      setLatestLink(invitation.invitation_url);
      setEmail("");
      setNotice(invitation.email_sent ? "邀请邮件已发送。" : "邀请已创建，请复制链接发送给受邀人。");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建邀请失败");
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(latestLink);
      setNotice("邀请链接已复制。");
    } catch {
      setError("浏览器未允许复制，请手动选择链接。");
    }
  }

  async function revoke(item: TenantInvitation) {
    setBusy(true); setError(""); setNotice("");
    try {
      await revokeTenantInvitation(currentUser.tenant_id, item.invitation_id);
      setNotice("邀请已撤销。");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "撤销邀请失败");
    } finally {
      setBusy(false);
    }
  }

  return <section className="surface-panel settings-card span-two invitation-management" id="member-invitations">
    <SectionTitle title="成员邀请" description="邀请绑定邮箱、当前工作区和角色，只能使用一次" />
    <form className="invitation-create-form" onSubmit={submit}>
      <label><span>受邀邮箱</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="off" maxLength={320} required /></label>
      <label><span>工作区角色</span><select value={role} onChange={(event) => setRole(event.target.value as typeof role)}>{adminRoleOptions.map((value) => <option value={value} key={value}>{formatRoleLabel(value)}</option>)}</select></label>
      <label><span>有效时间</span><select value={expiresInHours} onChange={(event) => setExpiresInHours(Number(event.target.value))}><option value={24}>24 小时</option><option value={72}>72 小时</option><option value={168}>7 天</option></select></label>
      <button className="primary-small" disabled={busy}>{busy ? "创建中…" : "创建邀请"}</button>
    </form>
    {error && <InlineError message={error} />}{notice && <div className="inline-success">{notice}</div>}
    {latestLink && <div className="invitation-link"><input value={latestLink} readOnly aria-label="邀请链接" /><button type="button" className="secondary-small" onClick={() => void copyLink()}>复制链接</button></div>}
    <DataTable headers={["邮箱", "角色", "状态", "有效期", "操作"]}>{items.map((item) => {
      const expired = item.status === "pending" && new Date(item.expires_at).getTime() <= Date.now();
      const status = expired ? "expired" : item.status;
      return <tr key={item.invitation_id}><td>{item.email}</td><td>{item.roles.map(formatRoleLabel).join("、")}</td><td><StateBadge value={invitationStatusLabel(status)} tone={status === "pending" ? "blue" : status === "redeemed" ? "green" : "neutral"} /></td><td>{formatDateTime(item.expires_at)}</td><td>{status === "pending" ? <button className="danger-small" disabled={busy} onClick={() => void revoke(item)}>撤销</button> : "—"}</td></tr>;
    })}</DataTable>
  </section>;
}

const adminRoleOptions = ["tenant_owner", "support_manager", "support_agent", "knowledge_admin", "auditor"] as const;

function TeamManagement({ currentUser }: { currentUser: AdminUser }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<(typeof adminRoleOptions)[number]>("support_agent");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function refreshUsers() {
    try {
      setUsers(await listAdminUsers());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载团队失败");
    }
  }

  useEffect(() => {
    void refreshUsers();
  }, []);

  async function submitCreate(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await createAdminUser({ username, display_name: displayName, password, roles: [role] });
      setUsername("");
      setDisplayName("");
      setPassword("");
      setRole("support_agent");
      setNotice("团队成员已创建，可使用设置的临时密码登录。");
      await refreshUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建团队成员失败");
    } finally {
      setBusy(false);
    }
  }

  return <section className="surface-panel settings-card span-two team-management">
    <SectionTitle title="客服团队与权限" description="仅工作区管理员可操作；所有变更都会记录，并让受影响账号重新登录" />
    <form className="team-create-form" onSubmit={submitCreate}>
      <label><span>登录名</span><input value={username} onChange={(event) => setUsername(event.target.value)} maxLength={200} required /></label>
      <label><span>显示名称</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={200} required /></label>
      <label><span>临时密码</span><input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} maxLength={200} required /></label>
      <label><span>初始角色</span><select value={role} onChange={(event) => setRole(event.target.value as typeof role)}>{adminRoleOptions.map((value) => <option value={value} key={value}>{formatRoleLabel(value)}</option>)}</select></label>
      <button className="primary-small" disabled={busy} type="submit">{busy ? "创建中…" : "添加成员"}</button>
    </form>
    {error && <InlineError message={error} />}
    {notice && <div className="inline-success">{notice}</div>}
    <div className="team-list">
      {users.map((item) => <TeamUserRow key={item.user_id} item={item} currentUser={currentUser} onChanged={refreshUsers} onError={setError} onNotice={setNotice} />)}
    </div>
  </section>;
}

function TeamUserRow({ item, currentUser, onChanged, onError, onNotice }: { item: AdminUser; currentUser: AdminUser; onChanged: () => Promise<void>; onError: (message: string) => void; onNotice: (message: string) => void }) {
  const [displayName, setDisplayName] = useState(item.display_name);
  const [role, setRole] = useState(item.roles[0] || "support_agent");
  const [status, setStatus] = useState<"active" | "disabled">(item.status);
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    onError("");
    onNotice("");
    setBusy(true);
    try {
      await updateAdminUser(item.user_id, { display_name: displayName, roles: [role], status });
      onNotice("已更新 " + item.username + "；角色或状态变更会撤销其登录会话。");
      await onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "更新成员失败");
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword() {
    if (newPassword.length < 12) {
      onError("临时密码至少需要 12 个字符。");
      return;
    }
    onError("");
    onNotice("");
    setBusy(true);
    try {
      await resetAdminUserPassword(item.user_id, newPassword);
      setNewPassword("");
      onNotice("已重置 " + item.username + " 的密码并撤销其全部会话。");
      await onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "重置密码失败");
    } finally {
      setBusy(false);
    }
  }

  return <article className="team-user-card">
    <div className="team-user-heading"><span className="mini-avatar">{initials(item.display_name)}</span><div><strong>{item.username}</strong><small>{item.user_id === currentUser.user_id ? "当前账号" : "创建于 " + (item.created_at ? formatDateTime(item.created_at) : "未知")}</small></div><StateBadge value={item.status === "active" ? "启用" : "已停用"} tone={item.status === "active" ? "green" : "neutral"} /></div>
    <div className="team-user-fields">
      <label><span>显示名称</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
      <label><span>角色</span><select value={role} onChange={(event) => setRole(event.target.value)}>{adminRoleOptions.map((value) => <option value={value} key={value}>{formatRoleLabel(value)}</option>)}</select></label>
      <label><span>状态</span><select value={status} disabled={item.user_id === currentUser.user_id} onChange={(event) => setStatus(event.target.value as "active" | "disabled")}><option value="active">启用</option><option value="disabled">停用</option></select></label>
      <button className="secondary-button" type="button" disabled={busy} onClick={save}>保存资料</button>
    </div>
    <div className="team-password-row"><input type="password" autoComplete="new-password" placeholder="输入 12 位以上临时密码" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /><button className="danger-outline" type="button" disabled={busy || !newPassword} onClick={resetPassword}>重置密码</button></div>
  </article>;
}

export function KnowledgeDeferredPage() {
  return <PageCanvas><div className="deferred-card"><span>▤</span><h2>知识库模块已按你的要求暂缓</h2><p>其他运营模块将先完成。等你提供需要测试的网站后，再接入网站内容同步、混合检索和精排验证。</p><div className="deferred-steps"><b>当前不会影响：</b><span>统一收件箱</span><span>实时访客</span><span>人工工单</span><span>客户记忆</span><span>运营报表</span></div></div></PageCanvas>;
}

function PageCanvas({ children }: { children: React.ReactNode }) { return <div className="page-canvas">{children}</div>; }
function Toolbar({ children }: { children: React.ReactNode }) { return <div className="page-toolbar">{children}</div>; }
function MetricGrid({ items }: { items: string[][] }) { return <div className="metric-grid">{items.map(([value, label]) => <div className="metric-card" key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>; }
function EntityCell({ title, subtitle }: { title: string; subtitle: string }) { return <div className="entity-cell"><span className="mini-avatar">{initials(title)}</span><div><strong>{title}</strong><small>{subtitle}</small></div></div>; }
function StateBadge({ value, tone }: { value: string; tone: "green" | "amber" | "red" | "indigo" | "blue" | "neutral" }) { return <span className={`state-badge ${tone}`}>{value}</span>; }
function InlineError({ message }: { message: string }) { return <div className="inline-error">{message}</div>; }
function PageEmpty({ title, description, actionLabel, onAction }: { title: string; description: string; actionLabel?: string; onAction?: () => void }) { return <div className="page-empty"><span>◎</span><h3>{title}</h3><p>{description}</p>{actionLabel && onAction && <button className="primary-small" onClick={onAction}>{actionLabel}</button>}</div>; }
function SectionTitle({ title, description }: { title: string; description: string }) { return <div className="section-heading"><div><h3>{title}</h3><p>{description}</p></div></div>; }
function DataTable({ headers, children }: { headers: string[]; children: React.ReactNode }) { return <div className="table-shell"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{children}</tbody></table></div>; }
function TagList({ values }: { values: string[] }) { return <div className="tag-list">{values.map((value) => <span key={value}>{value}</span>)}</div>; }
function HealthItem({ label, value, total }: { label: string; value: number; total: number }) { const percentage = Math.round((value / total) * 100); return <div className="health-item"><div><span>{label}</span><b>{percentage}%</b></div><i><em style={{ width: `${percentage}%` }} /></i></div>; }

function initials(value: string) { return value.trim().slice(0, 2).toUpperCase(); }
function relativeTime(value: string) { const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000); if (seconds < 60) return "刚刚"; if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`; if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`; return `${Math.floor(seconds / 86400)} 天前`; }
function currentPageDwellSeconds(item: VisitorPresence) { if (item.current_page_dwell_seconds !== undefined) return item.current_page_dwell_seconds; return Math.max(0, (new Date(item.last_seen_at).getTime() - new Date(item.current_page_entered_at || item.last_seen_at).getTime()) / 1000); }
function formatDuration(seconds: number) { if (!seconds) return "—"; if (seconds < 60) return `${Math.round(seconds)} 秒`; if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`; return `${(seconds / 3600).toFixed(1)} 小时`; }
function formatBytes(value: number) { if (value < 1024) return value + " B"; if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB"; if (value < 1024 * 1024 * 1024) return (value / (1024 * 1024)).toFixed(1) + " MB"; return (value / (1024 * 1024 * 1024)).toFixed(1) + " GB"; }
function formatDateTime(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function ownershipLabel(value: string) { return value === "human" ? "人工接管" : value === "queued" ? "等待人工" : "AI 自动处理"; }
function memoryKind(value: string) { return ({ preference: "客户偏好", verified_product: "已验证产品", troubleshooting: "排障记录", resolution: "解决方案" } as Record<string, string>)[value] || value; }
function auditEventLabel(value: string) { return ({ "admin_user.created": "创建管理员", "admin_user.updated": "更新管理员", "admin_user.password_reset": "重置管理员密码", "admin_session.created": "管理员登录", "admin_session.revoked": "登录会话失效", "widget_site.created": "创建站点", "widget_site.updated": "更新站点", "widget_site.key_rotated": "轮换站点密钥" } as Record<string, string>)[value] || value; }
function invitationStatusLabel(value: string) { return ({ pending: "待使用", redeemed: "已使用", revoked: "已撤销", expired: "已过期" } as Record<string, string>)[value] || value; }
