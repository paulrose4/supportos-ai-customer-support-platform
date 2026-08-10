import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Image as ImageIcon, MessageCircle, Monitor, Smartphone, Trash2, Upload, X } from "lucide-react";

import {
  deleteAutomationRule,
  getWidgetConfiguration,
  listWidgetAssets,
  listAutomationExecutions,
  listAutomationRules,
  listKnowledgeGaps,
  listPresence,
  publishWidgetVersion,
  resolveKnowledgeGap,
  rollbackWidgetVersion,
  saveAutomationRule,
  saveWidgetDraft,
  testAutomationRule,
  uploadWidgetAsset,
} from "./api";
import type {
  AutomationExecution,
  AutomationRule,
  KnowledgeGap,
  Site,
  SupportConfiguration,
  WidgetConfig,
  WidgetConfigurationState,
  WidgetAsset,
  VisitorPresence,
} from "./types";
import { formatPriority, formatRiskLevel, terminology } from "./content/terminology";

const emptyConditions: AutomationRule["conditions"] = {
  site_id: null,
  page_path_prefix: null,
  business_hours: null,
  user_intent: null,
  minimum_risk_level: null,
  authenticated: null,
  minimum_dwell_seconds: null,
  has_assignee: null,
  has_ticket: null,
};

export function AutomationPage({ sites, configuration }: { sites: Site[]; configuration: SupportConfiguration }) {
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [executions, setExecutions] = useState<AutomationExecution[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [name, setName] = useState("");
  const [siteId, setSiteId] = useState(sites[0]?.site_id || "");
  const [path, setPath] = useState("");
  const [intent, setIntent] = useState("");
  const [businessHours, setBusinessHours] = useState("");
  const [minimumRisk, setMinimumRisk] = useState("");
  const [minimumDwell, setMinimumDwell] = useState("");
  const [queueId, setQueueId] = useState("");
  const [priority, setPriority] = useState("");
  const [tags, setTags] = useState("");
  const [createTicket, setCreateTicket] = useState(false);
  const [directHandoff, setDirectHandoff] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const [nextRules, nextExecutions] = await Promise.all([
      listAutomationRules(),
      listAutomationExecutions(),
    ]);
    setRules(nextRules);
    setExecutions(nextExecutions);
  }

  useEffect(() => { void refresh().catch((reason: Error) => setError(reason.message)); }, []);

  const conditions = useMemo<AutomationRule["conditions"]>(() => ({
    ...emptyConditions,
    site_id: siteId || null,
    page_path_prefix: path || null,
    business_hours: businessHours === "" ? null : businessHours === "online",
    user_intent: intent || null,
    minimum_risk_level: minimumRisk === "" ? null : Number(minimumRisk),
    minimum_dwell_seconds: minimumDwell === "" ? null : Number(minimumDwell),
  }), [businessHours, intent, minimumDwell, minimumRisk, path, siteId]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      await saveAutomationRule({
        rule_id: null,
        name,
        enabled: true,
        sort_order: (rules.length + 1) * 100,
        conditions,
        actions: {
          queue_id: queueId || null,
          priority: (priority || null) as AutomationRule["actions"]["priority"],
          tags: tags.split(",").map((value) => value.trim()).filter(Boolean),
          create_ticket: createTicket,
          direct_handoff: directHandoff,
        },
      });
      setName(""); setPath(""); setTags(""); setCreateTicket(false); setDirectHandoff(false);
      setNotice("规则已保存，新的客服窗口消息会按当前顺序处理。");
      setEditorOpen(false);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存规则失败"); }
    finally { setBusy(false); }
  }

  async function testCurrent() {
    setError(""); setNotice("");
    try {
      const result = await testAutomationRule(conditions, {
        site_id: siteId,
        page_path: path || "/products/example",
        within_business_hours: businessHours !== "offline",
        user_intent: intent || "other",
        risk_level: minimumRisk ? Number(minimumRisk) : 0,
        authenticated: false,
        dwell_seconds: minimumDwell ? Number(minimumDwell) : 0,
        has_assignee: false,
        has_ticket: false,
      });
      setNotice(result.matched ? "测试命中：所有条件满足。" : `测试未命中：${result.reasons.join("；")}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "测试失败"); }
  }

  async function toggle(rule: AutomationRule) {
    await saveAutomationRule({ ...rule, enabled: !rule.enabled });
    await refresh();
  }

  async function remove(ruleId: string) {
    if (!window.confirm("确认删除这条规则？历史执行记录会保留。")) return;
    await deleteAutomationRule(ruleId);
    await refresh();
  }

  return <div className="page-canvas automation-page">
    <section className="surface-panel automation-list">
      <div className="section-heading"><div><h3>自动处理规则</h3><p>规则会在客服窗口消息保存后按顺序判断，不会因访客单纯浏览而主动执行</p></div><button className="primary-small" onClick={() => setEditorOpen(true)}>新建规则</button></div>
      {rules.map((rule) => {
        const ruleExecutions = executions.filter((item) => item.rule_id === rule.rule_id);
        const matches = ruleExecutions.filter((item) => item.matched).length;
        return <article className="automation-rule-row" key={rule.rule_id}>
          <div className="automation-rule-copy"><strong>{rule.name}</strong><small>{ruleSentence(rule, sites, configuration)}</small><span>顺序 {rule.sort_order} · 最近评估 {ruleExecutions.length} 次 · 命中 {matches} 次</span></div>
          <span className={`state-badge ${rule.enabled ? "green" : "neutral"}`}>{rule.enabled ? "启用" : "停用"}</span>
          <button className="secondary-button" onClick={() => void toggle(rule)}>{rule.enabled ? "停用" : "启用"}</button>
          <button className="danger-outline" onClick={() => void remove(rule.rule_id)}>删除</button>
        </article>;
      })}
      {!rules.length && <div className="automation-empty"><p>尚未创建自动化规则。</p><button className="secondary-button" onClick={() => setEditorOpen(true)}>创建第一条规则</button></div>}
      {error && <div className="inline-error">{error}</div>}{notice && <div className="inline-success">{notice}</div>}
    </section>
    {editorOpen && <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditorOpen(false); }}>
      <aside className="automation-drawer" role="dialog" aria-modal="true" aria-labelledby="automation-editor-title">
      <div className="section-heading"><div><h3 id="automation-editor-title">新建自动处理规则</h3><p>设置消息进入系统后需要执行的确定性条件和动作</p></div><button className="icon-close" onClick={() => setEditorOpen(false)} aria-label="关闭规则编辑器">×</button></div>
      <form onSubmit={submit}>
        <label><span>规则名称</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label><span>站点</span><select value={siteId} onChange={(event) => setSiteId(event.target.value)} required>{sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.name}</option>)}</select></label>
        <label><span>页面路径前缀</span><input value={path} onChange={(event) => setPath(event.target.value)} placeholder="/products/" /></label>
        <label><span>工作时间</span><select value={businessHours} onChange={(event) => setBusinessHours(event.target.value)}><option value="">不限</option><option value="online">工作时间内</option><option value="offline">工作时间外</option></select></label>
        <label><span>咨询类型</span><select value={intent} onChange={(event) => setIntent(event.target.value)}><option value="">不限</option><option value="knowledge">知识咨询</option><option value="order">订单</option><option value="logistics">物流</option><option value="refund">退款</option><option value="ticket">工单</option></select></label>
        <label><span>最低风险等级</span><select value={minimumRisk} onChange={(event) => setMinimumRisk(event.target.value)}><option value="">不限</option>{[0, 1, 2, 3].map((level) => <option value={level} key={level}>{formatRiskLevel(level).label}</option>)}</select></label>
        <label><span>页面停留时间（秒）</span><input type="number" min="0" max="86400" value={minimumDwell} onChange={(event) => setMinimumDwell(event.target.value)} /></label>
        <label><span>{terminology.supportGroup}</span><select value={queueId} onChange={(event) => setQueueId(event.target.value)}><option value="">不修改</option>{configuration.queues.filter((queue) => queue.status !== "disabled" && (queue.site_id === null || queue.site_id === siteId)).map((queue) => <option value={queue.queue_id} key={queue.queue_id}>{queue.name}</option>)}</select></label>
        <label><span>优先级</span><select value={priority} onChange={(event) => setPriority(event.target.value)}><option value="">不修改</option><option value="normal">普通</option><option value="high">高</option><option value="urgent">紧急</option></select></label>
        <label><span>添加标签</span><input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="物流, VIP" /></label>
        <label className="automation-check"><input type="checkbox" checked={createTicket} onChange={(event) => setCreateTicket(event.target.checked)} /><span>创建异步支持工单</span></label>
        <label className="automation-check"><input type="checkbox" checked={directHandoff} onChange={(event) => setDirectHandoff(event.target.checked)} /><span>将会话转入等待人工</span></label>
        <div className="automation-form-actions"><button type="button" className="secondary-button" onClick={() => setEditorOpen(false)}>取消</button><button type="button" className="secondary-button" onClick={testCurrent}>测试规则</button><button type="submit" className="primary-small" disabled={busy}>{busy ? "保存中…" : "保存规则"}</button></div>
      </form>
      <p className="definition-note">{automationSentence({ sites, siteId, path, businessHours, intent, minimumRisk, minimumDwell, queueId, priority, tags, createTicket, directHandoff, configuration })}</p>
      </aside>
    </div>}
    <section className="surface-panel automation-log"><div className="section-heading"><div><h3>执行记录</h3><p>保留会话、命中原因和实际动作，便于审计与排错</p></div></div><div className="table-shell"><table><thead><tr><th>时间</th><th>规则</th><th>会话</th><th>结果</th><th>动作/原因</th></tr></thead><tbody>{executions.map((item) => <tr key={item.execution_id}><td>{formatTime(item.occurred_at)}</td><td>{rules.find((rule) => rule.rule_id === item.rule_id)?.name || item.rule_id}</td><td><code>{item.conversation_id.slice(0, 12)}</code></td><td><span className={`state-badge ${item.matched ? "green" : "neutral"}`}>{item.matched ? "命中" : "未命中"}</span></td><td>{(item.actions_applied.length ? item.actions_applied : item.reasons).join("、")}</td></tr>)}</tbody></table></div></section>
  </div>;
}

function automationSentence({
  sites,
  siteId,
  path,
  businessHours,
  intent,
  minimumRisk,
  minimumDwell,
  queueId,
  priority,
  tags,
  createTicket,
  directHandoff,
  configuration,
}: {
  sites: Site[];
  siteId: string;
  path: string;
  businessHours: string;
  intent: string;
  minimumRisk: string;
  minimumDwell: string;
  queueId: string;
  priority: string;
  tags: string;
  createTicket: boolean;
  directHandoff: boolean;
  configuration: SupportConfiguration;
}) {
  const conditions = [
    `访客位于 ${sites.find((site) => site.site_id === siteId)?.name || "所选站点"}`,
    path ? `页面地址以 ${path} 开头` : "任意页面",
    businessHours === "online" ? "处于工作时间" : businessHours === "offline" ? "处于非工作时间" : "任意时间",
    intent ? `咨询类型为 ${intentLabel(intent)}` : "任意咨询类型",
    minimumRisk ? `风险达到${formatRiskLevel(Number(minimumRisk)).label}` : null,
    minimumDwell ? `停留超过 ${minimumDwell} 秒` : null,
  ].filter(Boolean);
  const actions = [
    queueId ? `分配给 ${configuration.queues.find((queue) => queue.queue_id === queueId)?.name || "所选客服分组"}` : null,
    priority ? `设为${formatPriority(priority)}优先级` : null,
    tags.trim() ? `添加标签 ${tags}` : null,
    directHandoff ? "转入等待人工" : null,
    createTicket ? "创建异步支持工单" : null,
  ].filter(Boolean);
  return `当${conditions.join("，并且")}时，${actions.length ? actions.join("，并") : "保留当前分配设置"}。`;
}

function ruleSentence(
  rule: AutomationRule,
  sites: Site[],
  configuration: SupportConfiguration,
) {
  const conditions = [
    rule.conditions.site_id ? `站点为 ${sites.find((site) => site.site_id === rule.conditions.site_id)?.name || rule.conditions.site_id}` : "任意站点",
    rule.conditions.page_path_prefix ? `页面以 ${rule.conditions.page_path_prefix} 开头` : null,
    rule.conditions.user_intent ? `咨询类型为 ${intentLabel(rule.conditions.user_intent)}` : null,
    rule.conditions.minimum_risk_level !== null ? `风险不低于 ${formatRiskLevel(rule.conditions.minimum_risk_level).label}` : null,
    rule.conditions.minimum_dwell_seconds !== null ? `停留至少 ${rule.conditions.minimum_dwell_seconds} 秒` : null,
  ].filter(Boolean);
  const actions = [
    rule.actions.queue_id ? `分配给 ${configuration.queues.find((queue) => queue.queue_id === rule.actions.queue_id)?.name || rule.actions.queue_id}` : null,
    rule.actions.priority ? `设为${formatPriority(rule.actions.priority)}优先级` : null,
    rule.actions.tags.length ? `添加标签 ${rule.actions.tags.join("、")}` : null,
    rule.actions.direct_handoff ? "转入等待人工" : null,
    rule.actions.create_ticket ? "创建异步工单" : null,
  ].filter(Boolean);
  return `当${conditions.join("，且")}时，${actions.join("，并")}。`;
}

function intentLabel(value: string) {
  return ({ knowledge: "知识咨询", order: "订单", logistics: "物流", refund: "退款", ticket: "工单" } as Record<string, string>)[value] || value;
}

export function WidgetConfigurationManagement({ sites }: { sites: Site[] }) {
  const [siteId, setSiteId] = useState(sites[0]?.site_id || "");
  const [state, setState] = useState<WidgetConfigurationState | null>(null);
  const [config, setConfig] = useState<WidgetConfig | null>(null);
  const [assets, setAssets] = useState<WidgetAsset[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState<"launcher" | "avatar" | null>(null);
  const [dirty, setDirty] = useState(false);
  const [syncAvatar, setSyncAvatar] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewMode, setPreviewMode] = useState<"desktop" | "mobile">("desktop");
  const [loading, setLoading] = useState(false);
  const [runtimeObservation, setRuntimeObservation] = useState<VisitorPresence | null>(null);
  const loadRequestRef = useRef(0);

  async function load(id: string) {
    if (!id) return;
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    setError("");
    try {
      const [next, nextAssets, presence] = await Promise.all([
        getWidgetConfiguration(id),
        listWidgetAssets(id),
        listPresence(300).catch(() => []),
      ]);
      if (requestId !== loadRequestRef.current) return;
      const nextConfig = next.draft?.config || next.published?.config || null;
      setState(next);
      setAssets(nextAssets);
      setRuntimeObservation(latestRuntimeObservation(presence, id));
      setConfig(nextConfig);
      setSyncAvatar(Boolean(
        nextConfig?.launcher_asset_id
        && nextConfig.agent_avatar_asset_id === nextConfig.launcher_asset_id,
      ));
      setDirty(false);
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (!siteId && sites[0]) setSiteId(sites[0].site_id);
  }, [siteId, sites]);
  useEffect(() => { void load(siteId).catch((reason: Error) => setError(reason.message)); }, [siteId]);
  useEffect(() => {
    if (!siteId) return;
    const timer = window.setInterval(() => {
      void listPresence(300)
        .then((presence) => setRuntimeObservation(latestRuntimeObservation(presence, siteId)))
        .catch(() => undefined);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [siteId]);
  useEffect(() => {
    function protectUnsavedChanges(event: BeforeUnloadEvent) {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", protectUnsavedChanges);
    return () => window.removeEventListener("beforeunload", protectUnsavedChanges);
  }, [dirty]);
  if (!sites.length) return null;

  function update<K extends keyof WidgetConfig>(key: K, value: WidgetConfig[K]) {
    setConfig((current) => current ? { ...current, [key]: value } : current);
    setDirty(true);
  }
  function changeSite(nextSiteId: string) {
    if (busy || uploading || loading) return;
    if (dirty && !window.confirm("当前站点有尚未保存的修改，确认切换站点？")) return;
    setNotice("");
    setState(null);
    setConfig(null);
    setAssets([]);
    setRuntimeObservation(null);
    setSiteId(nextSiteId);
  }
  function selectAsset(purpose: "launcher" | "avatar", asset: WidgetAsset | null) {
    setConfig((current) => {
      if (!current) return current;
      if (purpose === "launcher") {
        return {
          ...current,
          launcher_asset_id: asset?.asset_id || null,
          agent_avatar_asset_id: syncAvatar ? asset?.asset_id || null : current.agent_avatar_asset_id,
        };
      }
      return { ...current, agent_avatar_asset_id: asset?.asset_id || null };
    });
    setDirty(true);
    setNotice(asset ? "图片已选择，请保存草稿后再发布。" : "已恢复默认图标，请保存草稿。 ");
  }
  async function uploadAsset(purpose: "launcher" | "avatar", file: File) {
    if (!file.type.match(/^image\/(png|jpeg|webp)$/)) {
      setError("仅支持 PNG、JPEG 或 WebP 图片。");
      return;
    }
    if (file.size > 2_000_000) {
      setError("图片不能超过 2 MB。");
      return;
    }
    setUploading(purpose); setError(""); setNotice("");
    try {
      const asset = await uploadWidgetAsset(siteId, file, purpose);
      setAssets((current) => [asset, ...current.filter((item) => item.asset_id !== asset.asset_id)]);
      selectAsset(purpose, asset);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "图片上传失败");
    } finally {
      setUploading(null);
    }
  }
  async function save() {
    if (!config) return;
    setBusy(true); setError(""); setNotice("");
    try { const next = await saveWidgetDraft(siteId, config); setState(next); setDirty(false); setNotice("草稿已保存，线上客服窗口未改变。") }
    catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  }
  async function publish() {
    if (!state?.draft) return;
    if (!window.confirm(`确认把当前草稿发布到“${sites.find((site) => site.site_id === siteId)?.name || siteId}”？`)) return;
    setBusy(true); setError(""); setNotice("");
    try { const next = await publishWidgetVersion(siteId, state.draft.version_id); setState(next); setConfig(next.published?.config || config); setDirty(false); setNotice("新版本已发布，访客端通常数秒内刷新，最长约 60 秒。") }
    catch (reason) { setError(reason instanceof Error ? reason.message : "发布失败"); }
    finally { setBusy(false); }
  }
  async function restore(versionId: string) {
    if (!window.confirm("确认将此历史版本重新发布？")) return;
    setBusy(true); setError(""); setNotice("");
    try { const next = await rollbackWidgetVersion(siteId, versionId); setState(next); setConfig(next.published?.config || config); setDirty(false); setNotice("历史版本已作为新版本恢复并发布。"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "恢复历史版本失败"); }
    finally { setBusy(false); }
  }
  const dailyHoursValue = config?.business_hours.mon || "09:00-18:00";
  const assetUrl = (assetId: string | null) => assets.find((item) => item.asset_id === assetId)?.url || null;
  const launcherUrl = config ? assetUrl(config.launcher_asset_id) : null;
  const avatarUrl = config ? assetUrl(config.agent_avatar_asset_id) || config.agent_avatar_url : null;
  const previewVisible = previewMode === "desktop" || Boolean(config?.mobile_enabled);
  const publishedVersion = state?.published || null;
  const runtimeReportMatchesPublished = Boolean(
    publishedVersion
    && runtimeObservation?.config_version
    && runtimeObservation.config_version === publishedVersion.version_id,
  );
  const runtimeReportLabel = !runtimeObservation
    ? "等待浏览器运行上报"
    : !runtimeObservation.config_version
      ? "浏览器未上报配置版本（仅供参考）"
      : runtimeReportMatchesPublished
        ? "浏览器上报版本相符（仅供参考）"
        : "浏览器上报版本不同（仅供参考）";
  const runtimeReportTone = !runtimeObservation || !runtimeObservation.config_version
    ? "unknown"
    : runtimeReportMatchesPublished ? "current" : "stale";
  const connectorLabel = runtimeObservation?.connector_type === "wordpress" ? "WordPress"
    : runtimeObservation?.connector_type === "static_php" ? "静态 PHP"
      : runtimeObservation?.connector_type === "cloudflare_worker" ? "Cloudflare Worker"
        : runtimeObservation?.connector_type === "legacy" ? "旧版连接器"
          : runtimeObservation?.connector_type === "public" ? "公共脚本" : "未知连接器";
  return <section className="surface-panel settings-card span-two widget-config-management"><div className="section-heading"><div><h3>网站客服窗口配置</h3><p>草稿与线上版本相互隔离，发布后访客才会看到变化</p></div><div className="widget-config-heading-actions">{dirty && <span className="widget-unsaved-dot">尚未保存</span>}<select value={siteId} disabled={busy || Boolean(uploading) || loading} onChange={(event) => changeSite(event.target.value)}>{sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.name}</option>)}</select></div></div>
    {publishedVersion && <div className={`widget-runtime-status ${runtimeReportTone}`}><div><strong>线上版本 v{publishedVersion.version_number}</strong><code title={publishedVersion.version_id}>{publishedVersion.version_id.slice(0, 12)}</code><small>发布后最迟约 60 秒完成外观刷新</small></div><div><strong>{runtimeReportLabel}</strong><span>{runtimeObservation ? `${connectorLabel}${runtimeObservation.connector_version ? ` ${runtimeObservation.connector_version}` : ""} · Runtime ${runtimeObservation.runtime_version?.slice(0, 12)}` : "近 5 分钟暂无浏览器运行上报"}</span>{runtimeObservation?.config_version && <code title={runtimeObservation.config_version}>{runtimeObservation.config_version.slice(0, 12)}</code>}<small>运行版本来自浏览器上报，不代表服务端发布确认</small></div></div>}
    {config && <div className="widget-config-layout"><div className="widget-config-form">
      <div className="widget-image-settings">
        <WidgetAssetControl title="悬浮入口图标" purpose="launcher" selectedId={config.launcher_asset_id} assets={assets} busy={uploading !== null} onUpload={(file) => void uploadAsset("launcher", file)} onSelect={(asset) => selectAsset("launcher", asset)} />
        <div className="widget-image-options"><span>图片显示方式</span><div className="segmented-control" role="group" aria-label="图片显示方式"><button type="button" className={config.launcher_image_fit === "contain" ? "active" : ""} onClick={() => update("launcher_image_fit", "contain")}>完整显示</button><button type="button" className={config.launcher_image_fit === "cover" ? "active" : ""} onClick={() => update("launcher_image_fit", "cover")}>裁剪填满</button></div><label className="automation-check"><input type="checkbox" checked={syncAvatar} onChange={(event) => { const checked = event.target.checked; setSyncAvatar(checked); if (checked) update("agent_avatar_asset_id", config.launcher_asset_id); }} /><span>同时用于客服头像</span></label></div>
        {!syncAvatar && <WidgetAssetControl title="客服头像" purpose="avatar" selectedId={config.agent_avatar_asset_id} assets={assets} busy={uploading !== null} onUpload={(file) => void uploadAsset("avatar", file)} onSelect={(asset) => selectAsset("avatar", asset)} />}
      </div>
      <label><span>客服名称</span><input value={config.agent_name} onChange={(event) => update("agent_name", event.target.value)} /></label>
      <label><span>在线状态文案</span><input value={config.online_message} onChange={(event) => update("online_message", event.target.value)} /></label>
      <label><span>欢迎语</span><textarea value={config.welcome_message} onChange={(event) => update("welcome_message", event.target.value)} /></label>
      <label><span>离线文案</span><textarea value={config.offline_message} onChange={(event) => update("offline_message", event.target.value)} /></label>
      <label><span>时区</span><input value={config.business_timezone} onChange={(event) => update("business_timezone", event.target.value)} /></label>
      <label><span>每日营业时间</span><input value={dailyHoursValue} onChange={(event) => update("business_hours", { mon: event.target.value, tue: event.target.value, wed: event.target.value, thu: event.target.value, fri: event.target.value, sat: event.target.value, sun: event.target.value })} placeholder="全天在线：00:00-24:00" /></label>
      <label><span>节假日</span><input value={config.holidays.join(",")} onChange={(event) => update("holidays", event.target.value.split(",").map((value) => value.trim()).filter(Boolean))} placeholder="2026-10-01,2026-10-02" /></label>
      <label><span>主色</span><input type="color" value={config.primary_color} onChange={(event) => update("primary_color", event.target.value)} /></label>
      <label><span>位置</span><select value={config.position} onChange={(event) => update("position", event.target.value as "left" | "right")}><option value="right">右侧</option><option value="left">左侧</option></select></label>
      <label><span>默认语言</span><input value={config.default_language} onChange={(event) => update("default_language", event.target.value)} /></label>
      <label><span>客户称呼方式</span><select value={config.customer_address_mode} onChange={(event) => update("customer_address_mode", event.target.value as "formal" | "neutral" | "friendly")}><option value="formal">正式</option><option value="neutral">自然</option><option value="friendly">亲切</option></select></label>
      <label><span>转人工超时（秒）</span><input type="number" min="30" max="3600" value={config.handoff_timeout_seconds} onChange={(event) => update("handoff_timeout_seconds", Number(event.target.value))} /></label>
      <label className="automation-check"><input type="checkbox" checked={config.introduce_on_first_turn} onChange={(event) => update("introduce_on_first_turn", event.target.checked)} /><span>首轮介绍客服身份</span></label>
      <label className="automation-check"><input type="checkbox" checked={config.offline_form_enabled} onChange={(event) => update("offline_form_enabled", event.target.checked)} /><span>启用离线留言</span></label>
      <label className="automation-check"><input type="checkbox" checked={config.mobile_enabled} onChange={(event) => update("mobile_enabled", event.target.checked)} /><span>移动端显示</span></label>
      <label className="automation-check"><input type="checkbox" checked={config.csat_enabled} onChange={(event) => update("csat_enabled", event.target.checked)} /><span>解决后展示满意度</span></label>
      <div className="widget-config-actions"><button className="secondary-button" disabled={busy || Boolean(uploading) || !dirty} onClick={save}>{busy ? "处理中…" : "保存草稿"}</button><button className="primary-small" disabled={busy || Boolean(uploading) || dirty || !state?.draft} title={dirty ? "请先保存草稿" : undefined} onClick={publish}>发布草稿</button></div>
    </div><div className="widget-preview-column"><div className="widget-preview-toolbar"><div className="segmented-control" role="group" aria-label="预览设备"><button type="button" title="桌面端" aria-label="桌面端" className={previewMode === "desktop" ? "active" : ""} onClick={() => setPreviewMode("desktop")}><Monitor aria-hidden="true" /></button><button type="button" title="移动端" aria-label="移动端" className={previewMode === "mobile" ? "active" : ""} onClick={() => setPreviewMode("mobile")}><Smartphone aria-hidden="true" /></button></div><div className="segmented-control" role="group" aria-label="预览状态"><button type="button" className={!previewOpen ? "active" : ""} onClick={() => setPreviewOpen(false)}>关闭状态</button><button type="button" className={previewOpen ? "active" : ""} onClick={() => setPreviewOpen(true)}>打开状态</button></div></div><div className={`widget-preview-stage ${previewMode} ${config.position}`} style={{ "--preview-primary": config.primary_color } as React.CSSProperties}>{previewVisible ? <>{previewOpen && <div className="widget-preview"><div className="widget-preview-header">{avatarUrl ? <img src={avatarUrl} alt="" /> : <span className="widget-preview-avatar"><MessageCircle aria-hidden="true" /></span>}<div><strong>{config.agent_name}</strong><small>{config.online_message}</small></div><button type="button" title="关闭" aria-label="关闭" onClick={() => setPreviewOpen(false)}><X aria-hidden="true" /></button></div><div className="widget-preview-body"><p>{config.welcome_message}</p></div><div className="widget-preview-input">输入您的问题… <button type="button">发送</button></div></div>}<button type="button" className={`widget-preview-launcher fit-${config.launcher_image_fit}`} title={previewOpen ? "关闭客服" : "打开客服"} aria-label={previewOpen ? "关闭客服" : "打开客服"} onClick={() => setPreviewOpen((current) => !current)}>{previewOpen ? <X aria-hidden="true" /> : launcherUrl ? <img src={launcherUrl} alt="" /> : <MessageCircle aria-hidden="true" />}</button></> : <span className="widget-preview-disabled">移动端已关闭</span>}</div></div></div>}
    {error && <div className="inline-error">{error}</div>}{notice && <div className="inline-success">{notice}</div>}
    {state && <div className="widget-version-list">{state.versions.slice(0, 8).map((version) => <div key={version.version_id}><strong>v{version.version_number}</strong><span className={`state-badge ${version.status === "published" ? "green" : version.status === "draft" ? "amber" : "neutral"}`}>{version.status === "published" ? "已发布" : version.status === "draft" ? "草稿" : "历史"}</span><small>{formatTime(version.created_at)}</small>{version.status === "archived" && <button className="table-action" onClick={() => void restore(version.version_id)}>恢复</button>}</div>)}</div>}
  </section>;
}

function latestRuntimeObservation(
  presence: VisitorPresence[],
  siteId: string,
): VisitorPresence | null {
  return presence
    .filter((item) => item.site_id === siteId && item.runtime_version)
    .sort((left, right) => Date.parse(right.last_seen_at) - Date.parse(left.last_seen_at))[0]
    || null;
}

function WidgetAssetControl({ title, purpose, selectedId, assets, busy, onUpload, onSelect }: { title: string; purpose: "launcher" | "avatar"; selectedId: string | null; assets: WidgetAsset[]; busy: boolean; onUpload: (file: File) => void; onSelect: (asset: WidgetAsset | null) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const selected = assets.find((asset) => asset.asset_id === selectedId) || null;
  const recent = assets.filter((asset) => asset.purpose === purpose || asset.asset_id === selectedId).slice(0, 6);
  function takeFile(files: FileList | null) { const file = files?.item(0); if (file) onUpload(file); }
  return <div className="widget-asset-control" tabIndex={0} onPaste={(event) => takeFile(event.clipboardData.files)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); takeFile(event.dataTransfer.files); }}><div className="widget-asset-heading"><div className="widget-asset-current">{selected ? <img src={selected.url} alt="" /> : <ImageIcon aria-hidden="true" />}</div><div><strong>{title}</strong><small>{selected ? `${selected.width} × ${selected.height}` : "使用系统默认图标"}</small></div><div className="widget-asset-actions"><button type="button" className="secondary-small" disabled={busy} onClick={() => inputRef.current?.click()}><Upload aria-hidden="true" />{busy ? "上传中…" : "上传图片"}</button>{selected && <button type="button" className="icon-command" title="恢复默认" aria-label="恢复默认" onClick={() => onSelect(null)}><Trash2 aria-hidden="true" /></button>}</div></div><input ref={inputRef} className="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => { takeFile(event.target.files); event.target.value = ""; }} />{recent.length > 0 && <div className="widget-asset-library">{recent.map((asset) => <button type="button" key={asset.asset_id} className={asset.asset_id === selectedId ? "selected" : ""} title={`选择 ${formatTime(asset.created_at)}`} onClick={() => onSelect(asset)}><img src={asset.url} alt="" />{asset.asset_id === selectedId && <span><Check aria-hidden="true" /></span>}</button>)}</div>}</div>;
}

export function KnowledgeGapManagement() {
  const [items, setItems] = useState<KnowledgeGap[]>([]);
  const [error, setError] = useState("");
  async function refresh() { setItems(await listKnowledgeGaps()); }
  useEffect(() => { void refresh().catch((reason: Error) => setError(reason.message)); }, []);
  async function resolve(item: KnowledgeGap) {
    const note = window.prompt("请输入本次知识修复或审核说明：");
    if (!note) return;
    await resolveKnowledgeGap(item.gap_id, note); await refresh();
  }
  return <section className="surface-panel settings-card span-two knowledge-gap-management"><div className="section-heading"><div><h3>AI 与知识缺口</h3><p>客服标记的缺失知识和错误回答进入这里，修复后必须人工关闭</p></div></div>{error && <div className="inline-error">{error}</div>}<div className="table-shell"><table><thead><tr><th>状态</th><th>类型</th><th>问题摘要</th><th>会话</th><th>时间</th><th></th></tr></thead><tbody>{items.map((item) => <tr key={item.gap_id}><td><span className={`state-badge ${item.status === "open" ? "amber" : "green"}`}>{item.status === "open" ? "待修复" : "已关闭"}</span></td><td>{item.category === "incorrect_answer" ? "回答错误" : "知识缺失"}</td><td>{item.summary}</td><td><code>{item.conversation_id.slice(0, 12)}</code></td><td>{formatTime(item.created_at)}</td><td>{item.status === "open" && <button className="table-action" onClick={() => void resolve(item)}>标记已修复</button>}</td></tr>)}</tbody></table></div>{!items.length && <p className="muted-copy">暂无知识缺口记录。</p>}</section>;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
