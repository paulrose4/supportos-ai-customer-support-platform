import { AlertTriangle, ArrowRight, Bot, CircleAlert, Clock3, Users } from "lucide-react";

import type { InboxConversation, Site, VisitorPresence } from "./types";
import { formatRiskLevel, terminology } from "./content/terminology";

type QueueFilter = "overdue" | "queued" | "unread" | "mine";

interface OverviewPageProps {
  inbox: InboxConversation[];
  counts: { all: number; mine: number; waiting_human: number; sla_risk: number; unread: number; priority_risk: number; resolved: number } | null;
  presence: VisitorPresence[];
  sites: Site[];
  selectedSiteId: string;
  onOpenConversation: (conversationId: string) => void;
  onShowQueue: (filter: QueueFilter) => void;
}

export function OverviewPage({
  inbox,
  counts,
  presence,
  sites,
  selectedSiteId,
  onOpenConversation,
  onShowQueue,
}: OverviewPageProps) {
  const now = Date.now();
  const overdue = inbox.filter((item) => isOverdue(item.sla_due_at, now));
  const queued = inbox.filter((item) => item.ownership_mode === "queued");
  const unread = inbox.filter((item) => item.unread_count > 0);
  const urgent = inbox.filter((item) => item.priority === "urgent" || item.risk_level >= 2);
  const active = inbox.filter(
    (item) =>
      item.last_message_at && now - new Date(item.last_message_at).getTime() < 30 * 60_000,
  );
  const onlineVisitors = presence.filter(
    (item) => now - new Date(item.last_seen_at).getTime() <= 60_000,
  );
  const resolvedToday = inbox.filter(
    (item) =>
      item.status === "resolved" &&
      item.resolved_at &&
      now - new Date(item.resolved_at).getTime() < 24 * 60 * 60_000,
  );
  const overdueCount = counts?.sla_risk ?? overdue.length;
  const queuedCount = counts?.waiting_human ?? queued.length;
  const unreadCount = counts?.unread ?? unread.length;
  const highPriority = [...overdue, ...queued, ...urgent]
    .filter((item, index, items) => items.findIndex((candidate) => candidate.conversation_id === item.conversation_id) === index)
    .sort((left, right) => rankConversation(left, now) - rankConversation(right, now))
    .slice(0, 5);
  const siteName = selectedSiteId
    ? sites.find((site) => site.site_id === selectedSiteId)?.name || "当前站点"
    : "全部站点";

  return (
    <div className="page-canvas overview-page">
      <section className="overview-intro" aria-labelledby="overview-title">
        <div>
          <p className="page-kicker">运营驾驶舱</p>
          <h2 id="overview-title">优先处理正在影响客户体验的事项</h2>
          <p>当前范围：{siteName} · 实时会话与在线访客</p>
        </div>
        <div className="overview-live-status">
          <span aria-hidden="true" />
          实时更新中
        </div>
      </section>

      <section className="priority-grid" aria-label="今日优先处理">
        <PriorityCard
          count={overdueCount}
          label={terminology.responseOverdue}
          description={overdueCount ? (overdue[0] ? `最早已超时 ${waitingTime(overdue[0].sla_due_at, now)}` : "存在需要立即处理的超时会话") : "当前没有超时会话"}
          tone="critical"
          icon={<Clock3 aria-hidden="true" />}
          onClick={() => onShowQueue("overdue")}
        />
        <PriorityCard
          count={queuedCount}
          label="等待人工"
          description={queuedCount ? "优先接管高风险或订单类咨询" : "当前待处理列表已清空"}
          tone="warning"
          icon={<Users aria-hidden="true" />}
          onClick={() => onShowQueue("queued")}
        />
        <PriorityCard
          count={unreadCount}
          label="尚未阅读"
          description={unreadCount ? "新消息需要确认并继续处理" : "所有会话均已阅读"}
          tone="info"
          icon={<CircleAlert aria-hidden="true" />}
          onClick={() => onShowQueue("unread")}
        />
        <PriorityCard
          count={onlineVisitors.length}
          label="在线访客"
          description={active.length ? `${active.length} 条会话在 30 分钟内活跃` : "暂无近期会话"}
          tone="neutral"
          icon={<Bot aria-hidden="true" />}
          onClick={() => onShowQueue("mine")}
        />
      </section>

      <section className="overview-work-grid">
        <section className="surface-panel priority-queue" aria-labelledby="priority-queue-title">
          <div className="section-heading compact-heading">
            <div>
              <p className="page-kicker">优先处理</p>
              <h3 id="priority-queue-title">下一件应该处理的事</h3>
            </div>
            <button className="quiet-action" onClick={() => onShowQueue("queued")}>
              打开收件箱 <ArrowRight aria-hidden="true" />
            </button>
          </div>
          {highPriority.length ? (
            <div className="priority-list">
              {highPriority.map((item) => (
                <button
                  className="priority-row"
                  key={item.conversation_id}
                  onClick={() => onOpenConversation(item.conversation_id)}
                >
                  <span className={`priority-marker ${priorityTone(item, now)}`} aria-hidden="true" />
                  <span className="priority-row-copy">
                    <strong>{item.customer_display_name || "匿名访客"}</strong>
                    <small>{item.last_message_preview || "等待客服继续处理"}</small>
                  </span>
                  <span className="priority-row-meta">
                    <b>{priorityLabel(item, now)}</b>
                    <small>{relativeTime(item.updated_at)}</small>
                  </span>
                  <ArrowRight aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : (
            <div className="overview-empty">
              <Bot aria-hidden="true" />
              <strong>当前没有需要立即处理的会话</strong>
              <span>新的转人工请求和回复超时提醒会出现在这里。</span>
            </div>
          )}
        </section>

        <aside className="overview-insights" aria-label="运营洞察">
          <section className="surface-panel insight-panel">
            <div className="insight-icon warning"><AlertTriangle aria-hidden="true" /></div>
            <div>
              <p>风险提示</p>
              <strong>
                {urgent.length
                  ? `${urgent.length} 条会话带有高优先级或风险标记`
                  : "当前没有高风险会话"}
              </strong>
              <span>风险、身份与订单类请求应由人工确认后继续处理。</span>
            </div>
          </section>
          <section className="surface-panel insight-panel">
            <div className="insight-icon success"><Bot aria-hidden="true" /></div>
            <div>
              <p>处理进度</p>
              <strong>{counts?.resolved ?? resolvedToday.length} 条会话已解决</strong>
              <span>持续关注未读消息与等待人工处理列表，避免新请求积压。</span>
            </div>
          </section>
          <section className="surface-panel operating-metrics">
            <div className="section-heading compact-heading">
              <div><p className="page-kicker">运营脉冲</p><h3>会话处理分布</h3></div>
            </div>
            <MetricBar label="AI 自动处理" value={inbox.filter((item) => item.ownership_mode === "ai").length} total={inbox.length} tone="blue" />
            <MetricBar label="人工处理中" value={inbox.filter((item) => item.ownership_mode === "human").length} total={inbox.length} tone="green" />
            <MetricBar label="等待人工" value={queuedCount} total={counts?.all ?? inbox.length} tone="amber" />
          </section>
        </aside>
      </section>
    </div>
  );
}

function PriorityCard({
  count,
  label,
  description,
  tone,
  icon,
  onClick,
}: {
  count: number;
  label: string;
  description: string;
  tone: "critical" | "warning" | "info" | "neutral";
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button className={`priority-card ${tone}`} onClick={onClick}>
      <span className="priority-card-icon">{icon}</span>
      <span className="priority-card-copy">
        <strong>{count}</strong>
        <span>{label}</span>
        <small>{description}</small>
      </span>
      <ArrowRight aria-hidden="true" />
    </button>
  );
}

function MetricBar({ label, value, total, tone }: { label: string; value: number; total: number; tone: string }) {
  const percent = total ? Math.round((value / total) * 100) : 0;
  return <div className="operating-metric"><div><span>{label}</span><strong>{value}</strong></div><i><em className={tone} style={{ width: `${percent}%` }} /></i><small>{percent}% 的当前会话</small></div>;
}

function isOverdue(value: string | null, now: number) {
  return Boolean(value && new Date(value).getTime() <= now);
}

function rankConversation(item: InboxConversation, now: number) {
  if (isOverdue(item.sla_due_at, now)) return 0;
  if (item.priority === "urgent" || item.risk_level >= 2) return 1;
  if (item.ownership_mode === "queued") return 2;
  return 3;
}

function priorityTone(item: InboxConversation, now: number) {
  if (isOverdue(item.sla_due_at, now)) return "critical";
  if (item.priority === "urgent" || item.risk_level >= 2) return "warning";
  return "info";
}

function priorityLabel(item: InboxConversation, now: number) {
  if (isOverdue(item.sla_due_at, now)) return terminology.responseOverdue;
  if (item.priority === "urgent" || item.risk_level >= 2) return formatRiskLevel(item.risk_level).label;
  if (item.ownership_mode === "queued") return "待接管";
  return "需关注";
}

function relativeTime(value: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} 小时前`;
  return `${Math.floor(minutes / (24 * 60))} 天前`;
}

function waitingTime(value: string | null | undefined, now: number) {
  if (!value) return "未知";
  const minutes = Math.max(1, Math.floor((now - new Date(value).getTime()) / 60_000));
  return minutes < 60 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时`;
}
