import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import {
  cancelWebSyncJob,
  enqueueWebSyncJob,
  getLatestWebCrawlManifest,
  getSiteKnowledgeReadiness,
  getWebSyncAvailability,
  listWebSyncJobItems,
  listWebSyncJobs,
  reconcileStaleWebSyncVersions,
  retryWebSyncJob,
  runWebCrawlPreflight,
} from "./api";
import type {
  AdminUser,
  Site,
  SiteKnowledgeReadiness,
  WebCrawlManifest,
  WebSyncAvailability,
  WebSyncJob,
  WebSyncJobItem,
} from "./types";
import { helpText } from "./content/helpText";
import {
  sitemapAttemptOutcomeLabel,
  sitemapAttemptSourceLabel,
  sitemapCoverageLabel,
  sitemapDiscoveryModeLabel,
  sitemapReasonLabel,
  sitemapRootUrls,
  sitemapWarningLabel,
} from "./content/sitemap";
import { formatSyncMode, terminology } from "./content/terminology";
import { knowledgeJobStatusLabel, knowledgeJobSummary } from "./content/statusMessages";
import {
  effectiveExecutionState,
  executionStateLabel,
  executionStateSummary,
  isActiveWebSyncJob,
  isAbortError,
  mergeWebSyncJobs,
  startWebSyncPolling,
  type WebSyncPollSession,
} from "./webSyncState";

type SampleSize = 20 | 100 | 200 | 500;
type SyncMode = "shadow" | "production";
type ResourceKey = "jobs" | "manifest" | "readiness" | "availability";
type ResourceErrors = Partial<Record<ResourceKey, string>>;

export function KnowledgePage({
  sites,
  user,
  selectedSiteId,
}: {
  sites: Site[];
  user: AdminUser;
  selectedSiteId: string;
}) {
  const [siteId, setSiteId] = useState(selectedSiteId || sites[0]?.site_id || "");
  const [jobs, setJobs] = useState<WebSyncJob[]>([]);
  const [manifest, setManifest] = useState<WebCrawlManifest | null>(null);
  const [readiness, setReadiness] = useState<SiteKnowledgeReadiness | null>(null);
  const [availability, setAvailability] = useState<WebSyncAvailability | null>(null);
  const [sampleSize, setSampleSize] = useState<SampleSize | null>(20);
  const [mode, setMode] = useState<SyncMode>("shadow");
  const [busy, setBusy] = useState(false);
  const [preflighting, setPreflighting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeTone, setNoticeTone] = useState<"success" | "warning">("success");
  const [resourceErrors, setResourceErrors] = useState<ResourceErrors>({});
  const [detailJob, setDetailJob] = useState<WebSyncJob | null>(null);
  const [detailItems, setDetailItems] = useState<WebSyncJobItem[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailOffset, setDetailOffset] = useState(0);
  const [detailNextOffset, setDetailNextOffset] = useState<number | null>(null);
  const [cancelTarget, setCancelTarget] = useState<WebSyncJob | null>(null);
  const [canceling, setCanceling] = useState(false);
  const [retryingJobId, setRetryingJobId] = useState<string | null>(null);
  const pollingRef = useRef<{ siteId: string; session: WebSyncPollSession } | null>(null);
  const detailRequestRef = useRef<AbortController | null>(null);
  const actionRequestsRef = useRef<Set<AbortController>>(new Set());
  const currentSiteIdRef = useRef(siteId);
  currentSiteIdRef.current = siteId;
  const canSync = user.scopes.includes("knowledge:sync");

  useEffect(() => {
    if (selectedSiteId) setSiteId(selectedSiteId);
    else if (!siteId && sites[0]) setSiteId(sites[0].site_id);
  }, [selectedSiteId, siteId, sites]);

  const refreshSite = useCallback(async (targetSiteId: string, signal: AbortSignal) => {
    const reportResource = (key: ResourceKey, reason?: unknown) => {
      if (signal.aborted || (reason !== undefined && isAbortError(reason))) return;
      setResourceErrors((current) => {
        if (reason === undefined && !(key in current)) return current;
        const message = reason instanceof Error ? reason.message : "读取同步状态失败";
        if (reason !== undefined && current[key] === message) return current;
        const next = { ...current };
        if (reason === undefined) delete next[key];
        else next[key] = message;
        return next;
      });
    };

    const jobsRequest = listWebSyncJobs(targetSiteId, 30, signal)
      .then((nextJobs) => {
        if (signal.aborted) return;
        setJobs((current) => mergeWebSyncJobs(current, nextJobs, 30));
        setLoading(false);
        reportResource("jobs");
      })
      .catch((reason) => {
        if (!signal.aborted) setLoading(false);
        reportResource("jobs", reason);
      });
    const manifestRequest = getLatestWebCrawlManifest(targetSiteId, signal)
      .then((nextManifest) => {
        if (signal.aborted) return;
        setManifest(nextManifest);
        reportResource("manifest");
      })
      .catch((reason) => reportResource("manifest", reason));
    const readinessRequest = getSiteKnowledgeReadiness(targetSiteId, signal)
      .then((nextReadiness) => {
        if (signal.aborted) return;
        setReadiness(nextReadiness);
        reportResource("readiness");
      })
      .catch((reason) => reportResource("readiness", reason));
    const availabilityRequest = getWebSyncAvailability(targetSiteId, signal)
      .then((nextAvailability) => {
        if (signal.aborted) return;
        setAvailability(nextAvailability);
        reportResource("availability");
      })
      .catch((reason) => reportResource("availability", reason));

    await Promise.allSettled([jobsRequest, manifestRequest, readinessRequest, availabilityRequest]);
  }, []);

  const refresh = useCallback(async () => {
    const current = pollingRef.current;
    if (!siteId || !current || current.siteId !== siteId) return;
    await current.session.refresh();
  }, [siteId]);

  useEffect(() => {
    setLoading(Boolean(siteId));
    setError("");
    setNotice("");
    setResourceErrors({});
    setJobs([]);
    setManifest(null);
    setReadiness(null);
    setAvailability(null);
    setConfirming(false);
    setCancelTarget(null);
    setRetryingJobId(null);
    setDetailJob(null);
    setDetailItems([]);
    detailRequestRef.current?.abort();
    detailRequestRef.current = null;
    for (const controller of actionRequestsRef.current) controller.abort();
    actionRequestsRef.current.clear();
    if (!siteId) {
      setLoading(false);
      return;
    }

    const session = startWebSyncPolling((signal) => refreshSite(siteId, signal), 5000);
    pollingRef.current = { siteId, session };
    return () => {
      session.stop();
      detailRequestRef.current?.abort();
      detailRequestRef.current = null;
      for (const controller of actionRequestsRef.current) controller.abort();
      actionRequestsRef.current.clear();
      if (pollingRef.current?.session === session) pollingRef.current = null;
    };
  }, [siteId, refreshSite]);

  const resourceErrorText = formatResourceErrors(resourceErrors);

  function beginActionRequest() {
    const controller = new AbortController();
    actionRequestsRef.current.add(controller);
    return controller;
  }

  function endActionRequest(controller: AbortController) {
    actionRequestsRef.current.delete(controller);
  }

  const activeJob = jobs.find(isActiveWebSyncJob);
  const latest = jobs[0];
  const report = latest?.report;
  const selectedSite = sites.find((site) => site.site_id === siteId);
  const availableSamples = ([20, 100, 200, 500] as SampleSize[]).filter(
    (size) => size <= (manifest?.url_count ?? 0),
  );
  const availabilityFresh = availabilityIsFresh(availability);
  const tenantCovered = availability?.worker_tenant_covered
    ?? availability?.worker?.tenant_covered
    ?? true;
  const preflightReady = availabilityFresh && capabilityAllowed(
    availability?.capabilities?.preflight,
    availability?.preflight_ready === true,
  );
  const shadowProcessingReady = availabilityFresh && tenantCovered && capabilityAllowed(
    availability?.capabilities?.enqueue_shadow,
    availability?.job_processing_ready === true,
  );
  const productionProcessingReady = availabilityFresh && tenantCovered && capabilityAllowed(
    availability?.capabilities?.enqueue_production,
    availability?.job_processing_ready === true,
  );
  const shadowReady = Boolean(
    manifest?.status === "ready"
      && !manifest.is_expired
      && manifest.source_config_current
      && (sampleSize === null
        ? availability?.full_shadow_enabled
          && manifest.url_count <= availability.manifest_safety_ceiling
        : availableSamples.includes(sampleSize))
      && !activeJob,
  );
  const productionReady = Boolean(
    manifest?.status === "ready"
      && !manifest.is_expired
      && manifest.source_config_current
      && manifest.production_sync_enabled
      && manifest.url_count <= (availability?.manifest_safety_ceiling ?? Number.MAX_SAFE_INTEGER)
      && productionProcessingReady
      && !activeJob,
  );
  const runReady = mode === "shadow"
    ? shadowReady && shadowProcessingReady
    : productionReady;
  const manifestOverLimit = Boolean(
    manifest && availability && manifest.url_count > availability.manifest_safety_ceiling,
  );
  const manifestSourceOutdated = manifest?.source_config_current === false;
  const metrics = useMemo(
    () => [
      [`${report?.processed_page_count ?? latest?.completed_count ?? 0}/${latest?.expected_count ?? 0}`, "已检查页面"],
      [String(report?.produced_document_count ?? report?.document_count ?? 0), "生成文档"],
      [String(report?.product_count ?? 0), "可用商品"],
      [String(report?.failed_page_count ?? latest?.failed_item_count ?? 0), "失败页面"],
      [report?.published ? "已上线" : latest?.mode === "shadow" && latest?.status === "succeeded" ? "检查通过" : "未上线", "线上影响"],
    ],
    [latest?.completed_count, latest?.expected_count, latest?.failed_item_count, latest?.mode, latest?.status, report],
  );

  useEffect(() => {
    if (sampleSize !== null && !availableSamples.includes(sampleSize) && availableSamples[0]) {
      setSampleSize(availableSamples[0]);
    }
  }, [availableSamples, sampleSize]);

  async function preflight() {
    if (!selectedSite || activeJob) return;
    setPreflighting(true);
    setError("");
    setNotice("");
    const controller = beginActionRequest();
    try {
      const result = await runWebCrawlPreflight(selectedSite.site_id, controller.signal);
      if (currentSiteIdRef.current !== selectedSite.site_id) return;
      setManifest(result);
      setNoticeTone(result.status === "ready" ? "success" : "warning");
      setNotice(
        result.status === "ready"
          ? `范围检测完成，已冻结 ${result.url_count} 个主语言 URL。`
          : "范围检测完成，但存在阻断项，请先处理。",
      );
    } catch (reason) {
      if (currentSiteIdRef.current === selectedSite.site_id && !isAbortError(reason)) {
        setError(reason instanceof Error ? reason.message : "站点范围检测失败");
      }
    } finally {
      endActionRequest(controller);
      setPreflighting(false);
    }
  }

  async function enqueue() {
    if (!selectedSite || !manifest || !runReady) return;
    setConfirming(false);
    setBusy(true);
    setError("");
    setNotice("");
    const controller = beginActionRequest();
    try {
      const result = await enqueueWebSyncJob(
        selectedSite.site_id,
        manifest.manifest_id,
        mode,
        mode === "shadow" ? sampleSize : null,
        controller.signal,
      );
      if (currentSiteIdRef.current === result.job.site_id) {
        setJobs((current) => mergeWebSyncJobs(current, [result.job], 30));
      }
      setNoticeTone("success");
      setNotice(result.created ? `${formatSyncMode(mode)}已提交，系统将在后台开始处理。` : "该站点已有任务，已显示现有任务。");
      await refresh();
    } catch (reason) {
      if (currentSiteIdRef.current === selectedSite.site_id && !isAbortError(reason)) {
        setError(reason instanceof Error ? reason.message : `创建${formatSyncMode(mode)}失败`);
      }
    } finally {
      endActionRequest(controller);
      setBusy(false);
    }
  }

  async function openDetails(job: WebSyncJob) {
    setDetailJob(job);
    setDetailItems([]);
    setDetailOffset(0);
    setDetailNextOffset(null);
    await loadDetailPage(job, 0);
  }

  async function loadDetailPage(job: WebSyncJob, offset: number) {
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    detailRequestRef.current = controller;
    setDetailLoading(true);
    try {
      const page = await listWebSyncJobItems(job.job_id, offset, 100, controller.signal);
      if (controller.signal.aborted) return;
      setDetailItems(page.items);
      setDetailOffset(page.offset);
      setDetailNextOffset(page.next_offset);
    } catch (reason) {
      if (!isAbortError(reason)) {
        setError(reason instanceof Error ? reason.message : "读取页面明细失败");
      }
    } finally {
      if (detailRequestRef.current === controller) {
        detailRequestRef.current = null;
        setDetailLoading(false);
      }
    }
  }

  function closeDetails() {
    detailRequestRef.current?.abort();
    detailRequestRef.current = null;
    setDetailLoading(false);
    setDetailJob(null);
  }

  async function cancelJob() {
    if (!cancelTarget) return;
    setCanceling(true);
    const controller = beginActionRequest();
    try {
      const result = await cancelWebSyncJob(cancelTarget.job_id, controller.signal);
      if (currentSiteIdRef.current === result.site_id) {
        setJobs((current) => mergeWebSyncJobs(current, [result], 30));
      }
      setNoticeTone("success");
      setNotice(
        cancelTarget.status === "preparing" || cancelTarget.status === "queued"
          ? "任务已取消。"
          : cancelTarget.status === "blocked"
            ? "清理请求已提交，后台处理服务会删除本次未发布数据，当前线上知识继续使用。"
            : "取消请求已提交，当前页面完成后停止。",
      );
      setCancelTarget(null);
      await refresh();
    } catch (reason) {
      if (currentSiteIdRef.current === cancelTarget.site_id && !isAbortError(reason)) {
        setError(reason instanceof Error ? reason.message : "取消任务失败");
      }
    } finally {
      endActionRequest(controller);
      setCanceling(false);
    }
  }

  async function retryJob(job: WebSyncJob) {
    setRetryingJobId(job.job_id);
    setError("");
    setNotice("");
    const controller = beginActionRequest();
    try {
      const reconcileStale = needsStaleVersionReconciliation(job);
      const result = reconcileStale
        ? await reconcileStaleWebSyncVersions(job.job_id, controller.signal)
        : await retryWebSyncJob(job.job_id, controller.signal);
      if (currentSiteIdRef.current === result.site_id) {
        setJobs((current) => mergeWebSyncJobs(current, [result], 30));
      }
      setNoticeTone("success");
      setNotice(
        reconcileStale
          ? "失效版本引用已对账，受影响页面将重新生成。当前线上知识不受影响。"
          : "失败页面已重新提交，已完成页面会继续复用。当前线上知识不受影响。",
      );
      await refresh();
    } catch (reason) {
      if (currentSiteIdRef.current === job.site_id && !isAbortError(reason)) {
        setError(reason instanceof Error ? reason.message : "重新入队失败");
      }
    } finally {
      endActionRequest(controller);
      setRetryingJobId(null);
    }
  }

  useEffect(() => {
    setDetailJob((current) => {
      if (!current) return null;
      return jobs.find((job) => job.job_id === current.job_id) ?? current;
    });
  }, [jobs]);

  if (!sites.length) {
    return <main className="page-canvas"><section className="surface-panel knowledge-empty"><h3>尚未配置站点</h3><p>创建并启用站点后才能同步网站知识。</p></section></main>;
  }

  return <main className="page-canvas knowledge-page">
    <section className="surface-panel knowledge-control">
      <div className="section-heading">
        <div><h3>网站知识同步</h3><p>{selectedSite?.base_url || "当前站点尚未配置地址"}</p></div>
        <div className="knowledge-actions">
          <select aria-label="选择同步站点" value={siteId} onChange={(event) => setSiteId(event.target.value)}>
            {sites.map((site) => <option key={site.site_id} value={site.site_id}>{site.name}</option>)}
          </select>
          <button className="refresh-button" title="刷新任务状态" onClick={() => void refresh()}>↻</button>
          {canSync && <button className="secondary-small" disabled={preflighting || Boolean(activeJob) || !preflightReady} onClick={() => void preflight()}>{preflighting ? "检测中…" : "重新检测范围"}</button>}
        </div>
      </div>
      {error && <div className="inline-error" role="alert">{error}</div>}
      {resourceErrorText && <div className="inline-warning" role="status">部分状态读取失败：{resourceErrorText}。任务进度仍会继续刷新。</div>}
      {notice && <div className={noticeTone === "warning" ? "inline-warning" : "inline-success"} role="status">{notice}</div>}
      {availability?.blocking_reasons.length ? <div className="inline-error">
        网站同步暂不可用：{formatAvailabilityReasons(availability.blocking_reasons)}。
      </div> : null}
      {readiness && <div className={readiness.ready ? "inline-success" : "inline-error"}>
        {readiness.ready
          ? `客服知识已就绪：${readiness.active_product_count} 件商品，${readiness.active_document_count} 个已发布页面。`
          : `客服知识未就绪：${formatReadinessReasons(readiness.blocking_reasons)}。当前推荐能力会受限。`}
      </div>}

      <div className="knowledge-scope-summary">
        <div><span>范围状态</span><strong>{manifest ? manifest.is_expired ? "已过期" : manifest.status === "ready" ? "可验证" : "已阻断" : "未检测"}</strong></div>
        <div><span>发现方式</span><strong>{sitemapDiscoveryModeLabel(manifest?.discovery_method)}</strong></div>
        <div><span>站点地图入口</span><strong>{manifest ? sitemapRootUrls(manifest).length : "—"}</strong></div>
        <div><span>准备检查的页面</span><strong>{manifest?.url_count ?? "—"}</strong></div>
        <div><span>覆盖状态</span><strong>{sitemapCoverageLabel(manifest?.coverage_status)}</strong></div>
        <div><span>当前线上知识</span><strong>{readiness?.ready ? "可正常使用" : "能力受限"}</strong></div>
      </div>
      {manifest ? <p className="knowledge-locale-list">页面类型：{formatContentKindCounts(manifest.content_kind_counts)}</p> : null}
      {manifest?.translated_locales.length ? <p className="knowledge-locale-list">已排除：{manifest.translated_locales.join("、")}</p> : null}
      {manifest?.warnings?.length ? <div className="inline-warning" role="status">范围提醒：{manifest.warnings.map(sitemapWarningLabel).join("；")}。</div> : null}
      {manifestSourceOutdated ? <div className="inline-error">站点地图配置已变更，请重新检测范围后再创建任务。</div> : null}
      {manifest?.is_expired ? <div className="inline-error">范围清单已过期，请重新检测后再创建任务。</div> : null}
      {manifest?.blocking_reasons.length ? <div className="inline-error">阻断原因：{manifest.blocking_reasons.map(sitemapReasonLabel).join("；")}。</div> : null}
      {manifestOverLimit ? <div className="inline-error">当前清单有 {manifest?.url_count} 页，超过服务器的异常安全熔断值 {availability?.manifest_safety_ceiling} 页。固定样本仍可运行；全量处理需要先完成容量审核并调整 WEB_CRAWLER_MANIFEST_SAFETY_CEILING。</div> : null}
      {manifest && !manifest.production_sync_enabled ? <div className="knowledge-production-gate">暂时不能更新线上知识：{productionGateText(manifest.production_blocking_reasons)}</div> : null}

      <div className="knowledge-run-controls">
        <div className="knowledge-mode-control" role="group" aria-label="知识更新方式"><button className={mode === "shadow" ? "active" : ""} onClick={() => setMode("shadow")}>{terminology.testRun}</button><button className={mode === "production" ? "active" : ""} onClick={() => setMode("production")}>{terminology.publishKnowledge}</button></div>
        {mode === "shadow" ? <label><span>检查页数</span><select value={sampleSize ?? "full"} onChange={(event) => setSampleSize(event.target.value === "full" ? null : Number(event.target.value) as SampleSize)} disabled={!availableSamples.length}>{availableSamples.map((size) => <option key={size} value={size}>{size} 页</option>)}{availability?.full_shadow_enabled && manifest ? <option value="full">全量（{manifest.url_count} 页）</option> : null}</select></label> : <div className="knowledge-production-scope"><span>更新范围</span><strong>{manifest?.url_count ?? 0} 页完整清单</strong></div>}
        {canSync && <button className="primary-small" disabled={busy || !runReady} onClick={() => setConfirming(true)}>{busy ? "提交中…" : activeJob ? "任务进行中" : mode === "shadow" ? "开始试运行" : "更新线上知识"}</button>}
      </div>

      <div className="knowledge-current-state">
        <span className={`state-badge ${jobStatusTone(latest)}`}>{statusLabel(latest)}</span>
        <div className="knowledge-state-copy"><strong>{activeJob ? activeJob.cancel_requested_at ? "正在停止检查" : currentJobSummary(activeJob) : latestStateCopy(latest)}</strong><small>{latest ? `${formatTime(latest.requested_at)} · ${formatSyncMode(latest.mode)}` : "请先检查网站页面范围"}</small>{activeJob?.next_wake_at ? <small>下次自动唤醒：{formatTime(activeJob.next_wake_at)}</small> : null}{activeJob?.status === "blocked" && activeJob.retention_expires_at ? <small>未发布数据保留至 {formatTime(activeJob.retention_expires_at)}，到期后自动清理</small> : null}{activeJob ? <div className="knowledge-progress"><div><span style={{ width: `${progressPercent(activeJob)}%` }} /></div><small>{jobProgressCopy(activeJob)}{jobProgressTiming(activeJob)}</small></div> : null}</div>
        {activeJob && canSync ? <div className="knowledge-current-actions">{canRetryJob(activeJob) ? <button className="primary-small" disabled={retryingJobId === activeJob.job_id || Boolean(activeJob.cancel_requested_at)} onClick={() => void retryJob(activeJob)}>{retryingJobId === activeJob.job_id ? "正在重新提交…" : needsStaleVersionReconciliation(activeJob) ? "重新生成失效页面" : "重试失败页面"}</button> : null}{canCancelJob(activeJob) ? <button className={activeJob.status === "blocked" ? "danger-outline" : "secondary-small"} disabled={Boolean(activeJob.cancel_requested_at) || activeJob.phase === "finalizing"} title={activeJob.phase === "finalizing" ? "正在完成线上更新，当前不可取消" : undefined} onClick={() => setCancelTarget(activeJob)}>{activeJob.phase === "finalizing" ? "正在发布" : activeJob.cancel_requested_at ? "清理中…" : activeJob.status === "blocked" ? "放弃并清理未发布数据" : "停止任务"}</button> : null}</div> : null}
      </div>
    </section>

    <div className="metric-grid knowledge-metrics">{metrics.map(([value, label]) => <section className="metric-card" key={label}><strong>{value}</strong><span>{label}</span></section>)}</div>

    <details className="surface-panel advanced-diagnostics knowledge-diagnostics">
      <summary>高级诊断</summary>
      <p>{helpText.advancedDiagnostics}</p>
      <div className="knowledge-scope-summary">
        <div><span>{terminology.pageInventory}</span><strong>{manifest?.primary_sitemap_urls.length ?? "—"}</strong></div>
        <div><span>排除的翻译页面清单</span><strong>{manifest?.excluded_sitemap_count ?? "—"}</strong></div>
        <div><span>后台处理服务</span><strong>{workerStatusLabel(availability?.worker?.status ?? availability?.worker_status)}</strong></div>
        <div><span>异常安全熔断</span><strong>{availability?.manifest_safety_ceiling ?? "—"}</strong></div>
        <div><span>有明确终态</span><strong>{latest ? `${latest.completed_count}/${latest.expected_count}` : "—"}</strong></div>
        <div><span>成功进入知识库</span><strong>{latest?.succeeded_count ?? "—"}</strong></div>
        <div><span>内容未变化</span><strong>{latest?.not_modified_count ?? "—"}</strong></div>
        <div><span>合法排除</span><strong>{latest?.excluded_item_count ?? "—"}</strong></div>
        <div><span>永久失败</span><strong>{latest?.failed_item_count ?? "—"}</strong></div>
        <div><span>未重新下载的页面（HTTP 304）</span><strong>{report?.http_not_modified_count ?? "—"}</strong></div>
        <div><span>{terminology.verifiedKnowledge}</span><strong>{report?.indexed_chunk_count ?? "—"}</strong></div>
        <div><span>重复商品组</span><strong>{report?.duplicate_product_total ?? report?.duplicate_product_count ?? "—"}</strong></div>
        <div><span>自动排除重复页面</span><strong>{report?.duplicate_product_excluded_count ?? "—"}</strong></div>
        <div><span>未解决商品身份</span><strong>{report?.unresolved_product_identity_count ?? report?.duplicate_product_unresolved_count ?? report?.duplicate_product_count ?? "—"}</strong></div>
        <div><span>发布阻断项</span><strong>{report?.blocking_issue_count ?? 0}</strong></div>
        <div><span>任务编号</span><strong>{latest?.job_id || "—"}</strong></div>
        <div><span>状态版本</span><strong>{latest?.state_version ?? "—"}</strong></div>
        <div><span>最后进度</span><strong>{latest?.last_progress_at ? formatTime(latest.last_progress_at) : "—"}</strong></div>
      </div>
      {manifest && sitemapRootUrls(manifest).length ? <section className="sitemap-diagnostic-section" aria-labelledby="sitemap-roots-title">
        <h4 id="sitemap-roots-title">本次采用的站点地图入口</h4>
        <ul className="sitemap-root-list">{sitemapRootUrls(manifest).map((url) => <li key={url}><code>{url}</code></li>)}</ul>
      </section> : null}
      {manifest?.discovery_attempts?.length ? <section className="sitemap-diagnostic-section" aria-labelledby="sitemap-attempts-title">
        <h4 id="sitemap-attempts-title">站点地图探测记录</h4>
        <div className="sitemap-attempt-table-shell">
          <table>
            <caption>站点地图探测记录</caption>
            <thead><tr><th>来源</th><th>结果</th><th>请求地址</th><th>最终地址</th></tr></thead>
            <tbody>{manifest.discovery_attempts.map((attempt, index) => <tr key={`${attempt.source}-${attempt.url}-${index}`}>
              <td><strong>{sitemapAttemptSourceLabel(attempt.source)}</strong><code>{attempt.source}</code></td>
              <td><strong>{sitemapAttemptOutcomeLabel(attempt.outcome)}</strong><code>{attempt.outcome}</code></td>
              <td><code>{attempt.url}</code></td>
              <td><code>{attempt.final_url || "—"}</code></td>
            </tr>)}</tbody>
          </table>
        </div>
      </section> : null}
    </details>

    <section className="surface-panel knowledge-history">
        <div className="section-heading"><div><h3>更新记录</h3><p>查看知识检查和线上更新是否完成</p></div></div>
      <div className="table-shell"><table><thead><tr><th>状态</th><th>开始时间</th><th>方式</th><th>结果</th><th>进度</th><th>失败页面</th><th>可用商品</th><th>操作</th></tr></thead><tbody>
        {jobs.map((job) => <tr key={job.job_id}>
          <td><span className={`state-badge ${jobStatusTone(job)}`}>{statusLabel(job)}</span></td>
          <td><strong>{formatTime(job.requested_at)}</strong><small className="table-subline">第 {job.attempt_count || 0} 次执行</small></td>
          <td>{job.mode === "shadow" ? job.sample_size === null ? `全量试运行（${job.expected_count} 页）` : `${job.sample_size} 页试运行` : terminology.publishKnowledge}</td>
          <td>{jobResultCopy(job)}</td>
          <td>{job.expected_count ? `${job.completed_count}/${job.expected_count}` : job.report?.document_count ?? "—"}</td>
          <td>{job.failed_item_count || job.report?.failed_count || 0}</td>
          <td>{job.report?.product_count ?? "—"}</td>
          <td><div className="table-actions"><button className="secondary-small" onClick={() => void openDetails(job)}>明细</button>{canSync && canRetryJob(job) ? <button className="secondary-small" disabled={retryingJobId === job.job_id || Boolean(job.cancel_requested_at)} onClick={() => void retryJob(job)}>{needsStaleVersionReconciliation(job) ? "重新生成" : "重试失败页"}</button> : null}{canSync && canCancelJob(job) ? <button className={job.status === "blocked" ? "danger-outline" : "secondary-small"} disabled={Boolean(job.cancel_requested_at) || job.phase === "finalizing"} onClick={() => setCancelTarget(job)}>{job.status === "blocked" ? "清理" : "取消"}</button> : null}</div></td>
        </tr>)}
      </tbody></table></div>
      {!loading && !jobs.length && <p className="muted-copy">当前站点还没有同步记录。</p>}
      {loading && <p className="muted-copy">正在读取同步记录…</p>}
    </section>

    {confirming && manifest && selectedSite ? <div className="modal-backdrop" role="presentation" onMouseDown={() => setConfirming(false)}><section className="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="sync-confirm-title" onMouseDown={(event) => event.stopPropagation()}><h3 id="sync-confirm-title">{mode === "shadow" ? "确认开始试运行" : "确认更新线上知识"}</h3>{mode === "production" ? <p>系统会先完成全部页面检查和数据核对，确认通过后再替换当前线上知识；处理期间访客仍使用旧版本。</p> : <p>{helpText.testRun}</p>}<dl><div><dt>工作区</dt><dd>{user.tenant_id}</dd></div><div><dt>站点</dt><dd>{selectedSite.name} ({selectedSite.site_id})</dd></div><div><dt>页面范围</dt><dd>{manifest.url_count} 个页面</dd></div><div><dt>本次处理</dt><dd>{mode === "shadow" ? sampleSize === null ? `${manifest.url_count} 页全量试运行` : `${sampleSize} 页固定样本` : `${manifest.url_count} 页完整清单`}</dd></div><div><dt>线上影响</dt><dd>{mode === "shadow" ? "不会改变线上知识" : "检查通过后替换线上知识"}</dd></div></dl><div className="dialog-actions"><button className="secondary-small" onClick={() => setConfirming(false)}>取消</button><button className="primary-small" onClick={() => void enqueue()}>确认提交</button></div></section></div> : null}
    {cancelTarget ? <div className="modal-backdrop" role="presentation" onMouseDown={() => setCancelTarget(null)}><section className="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="cancel-job-title" onMouseDown={(event) => event.stopPropagation()}><h3 id="cancel-job-title">{cancelTarget.status === "blocked" ? "放弃并清理未发布数据" : `安全停止${formatSyncMode(cancelTarget.mode)}`}</h3><p>{cancelTarget.status === "blocked" ? "系统会删除本次未发布数据，当前线上知识不会变化，也不能继续原任务。" : cancelTarget.mode === "shadow" ? "系统会让当前页面处理完成，再停止剩余页面。已经完成的结果会保留。" : "系统会让当前页面处理完成，再清理本次未发布数据。当前线上知识不会变化。"}</p><dl><div><dt>已完成</dt><dd>{cancelTarget.completed_count}/{cancelTarget.expected_count} 页</dd></div></dl><details className="advanced-diagnostics"><summary>技术信息</summary><dl><div><dt>任务编号</dt><dd>{cancelTarget.job_id}</dd></div></dl></details><div className="dialog-actions"><button className="secondary-small" disabled={canceling} onClick={() => setCancelTarget(null)}>返回</button><button className="danger-outline" disabled={canceling} onClick={() => void cancelJob()}>{canceling ? "提交中…" : cancelTarget.status === "blocked" ? "确认清理" : "确认停止"}</button></div></section></div> : null}
    {detailJob ? <div className="modal-backdrop" role="presentation" onMouseDown={closeDetails}><section className="knowledge-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="job-detail-title" onMouseDown={(event) => event.stopPropagation()}><div className="section-heading"><div><h3 id="job-detail-title">页面执行明细</h3><p>{detailJob.completed_count}/{detailJob.expected_count} 页已完成，失败 {detailJob.failed_item_count}</p></div><button className="refresh-button" title="关闭" onClick={closeDetails}>×</button></div>{detailLoading ? <p className="muted-copy">正在读取页面状态…</p> : <><div className="job-item-list">{detailItems.map((item) => <article key={item.item_id}><span className={`state-badge ${itemStatusTone(item.status)}`}>{itemStatusLabel(item.status)}</span><div><strong>{item.url}</strong><small>{contentKindLabel(item.content_kind)}{item.outcome_reason ? ` · ${outcomeReasonLabel(item.outcome_reason)}` : ""} · 尝试 {item.attempt_count}/{item.max_attempts}{item.duration_ms !== null ? ` · ${item.duration_ms} ms` : ""}{item.product_key ? ` · ${item.product_key}` : ""}</small>{item.error_message ? <p>{item.error_message}</p> : null}</div></article>)}</div><div className="knowledge-detail-pagination"><span>{detailItems.length ? `${detailOffset + 1}-${detailOffset + detailItems.length}` : "0"} / {detailJob.expected_count}</span><div><button className="icon-command" title="上一页" aria-label="上一页" disabled={detailLoading || detailOffset === 0} onClick={() => void loadDetailPage(detailJob, Math.max(0, detailOffset - 100))}><ChevronLeft aria-hidden="true" /></button><button className="icon-command" title="下一页" aria-label="下一页" disabled={detailLoading || detailNextOffset === null} onClick={() => void loadDetailPage(detailJob, detailNextOffset ?? detailOffset)}><ChevronRight aria-hidden="true" /></button></div></div></>}</section></div> : null}
  </main>;
}

function statusLabel(job?: WebSyncJob) {
  if (!job) return knowledgeJobStatusLabel();
  if (job.cancel_requested_at) return knowledgeJobStatusLabel(job.status, true);
  if (job.stale) return "进度暂未更新";
  const executionState = effectiveExecutionState(job);
  if (executionState && executionState !== "terminal") {
    const label = executionStateLabel(job);
    if (label) return label;
  }
  if (job?.status === "succeeded") return job.mode === "shadow" ? "验证通过" : "已发布";
  return knowledgeJobStatusLabel(job.status === "canceled" ? "cancelled" : job.status);
}

function latestStateCopy(job?: WebSyncJob) {
  if (!job) return "尚未进行知识检查";
  if (job.stale) return "后台处理服务尚未报告新的任务进度。";
  const executionSummary = executionStateSummary(job);
  if (executionSummary && job.status !== "blocked") return executionSummary;
  return knowledgeJobSummary(job.status === "canceled" ? "cancelled" : job.status, job.mode, job.failed_item_count);
}

function currentJobSummary(job: WebSyncJob) {
  if (job.stale) return "后台处理服务尚未报告新的任务进度，请稍后再检查。";
  const executionSummary = executionStateSummary(job);
  if (executionSummary && job.status !== "blocked") return executionSummary;
  return job.status === "blocked"
    ? `${blockedJobSummary(job)}${executionSummary ? `，${executionSummary}` : ""}，当前线上知识继续正常使用。`
    : knowledgeJobSummary(job.status, job.mode, job.failed_item_count);
}

function blockedJobSummary(job: WebSyncJob) {
  if (job.report?.duplicate_product_count) {
    return `有 ${job.report.duplicate_product_count} 个重复商品标识需要处理`;
  }
  if (job.failed_item_count) return `有 ${job.failed_item_count} 个页面需要修复`;
  if (job.report?.errors.finalization) return "发布对账需要重试";
  return "任务未满足发布门槛";
}

function canRetryJob(job: WebSyncJob) {
  const retryFailed = jobActionAllowed(job, "retry_failed");
  const retryFinalization = jobActionAllowed(job, "retry_finalization");
  const reconcileStale = jobActionAllowed(job, "reconcile_stale_versions");
  if (retryFailed !== undefined || retryFinalization !== undefined || reconcileStale !== undefined) {
    return retryFailed === true || retryFinalization === true || reconcileStale === true;
  }
  return job.status === "blocked"
    && (job.failed_item_count > 0 || Boolean(job.report?.errors.finalization));
}

function needsStaleVersionReconciliation(job: WebSyncJob) {
  return jobActionAllowed(job, "reconcile_stale_versions") === true;
}

function canCancelJob(job: WebSyncJob) {
  // Cleanup is recovery work owned by the worker and cannot be interrupted.
  if (job.status === "cleanup_pending") return false;
  const abandon = job.status === "blocked" ? jobActionAllowed(job, "abandon") : undefined;
  if (abandon !== undefined) return abandon;
  const cancel = jobActionAllowed(job, "cancel");
  if (cancel !== undefined) return cancel;
  return job.status === "preparing"
    || job.status === "queued"
    || job.status === "running"
    || job.status === "blocked";
}

function jobActionAllowed(
  job: WebSyncJob,
  action: "cancel" | "retry_failed" | "retry_finalization" | "reconcile_stale_versions" | "abandon",
): boolean | undefined {
  const actions = job.actions;
  if (!actions) return undefined;
  if (Array.isArray(actions)) return actions.includes(action);
  const value = actions[action];
  if (typeof value === "boolean") return value;
  return value?.allowed;
}

function statusTone(status?: WebSyncJob["status"]) {
  if (status === "succeeded") return "green";
  if (status === "preparing" || status === "queued" || status === "running") return "amber";
  if (status === "failed" || status === "blocked") return "red";
  if (status === "canceled") return "neutral";
  return "neutral";
}

function jobStatusTone(job?: WebSyncJob) {
  if (!job) return statusTone();
  const state = effectiveExecutionState(job);
  if (state === "stalled" || state === "attention_required") return "red";
  if (job.stale) return "amber";
  if (state === "waiting_for_worker" || state === "waiting_retry" || state === "recovery" || state === "recovery_pending") return "amber";
  return statusTone(job.status);
}

function progressPercent(job: WebSyncJob) {
  const executionState = effectiveExecutionState(job);
  const current = job.status === "preparing"
    || executionState === "waiting_for_worker"
    || executionState === "preparing"
    ? job.prepared_count
    : job.completed_count;
  return job.expected_count ? Math.min(100, Math.round((current / job.expected_count) * 100)) : 0;
}

function jobProgressCopy(job: WebSyncJob) {
  const executionState = effectiveExecutionState(job);
  if (executionState === "waiting_for_worker") {
    return `${job.prepared_count}/${job.expected_count} 页待准备`;
  }
  if (job.status === "preparing" || executionState === "preparing") {
    return `${job.prepared_count}/${job.expected_count} 页已准备`;
  }
  return `${job.completed_count}/${job.expected_count} 页已处理 · 失败 ${job.failed_item_count}`;
}

function jobResultCopy(job: WebSyncJob) {
  if (job.stale) return "进度暂未更新，请检查后台处理服务";
  const executionSummary = executionStateSummary(job);
  if (executionSummary && job.status !== "blocked") return executionSummary;
  if (job.status === "blocked") return blockedJobSummary(job);
  if (job.report) {
    if (job.mode === "shadow") return "检查完成，未改变线上知识";
    return job.report.published ? "新知识已上线" : "未满足上线条件";
  }
  if (job.error_message) return job.error_message;
  if (job.status === "preparing") return `正在准备 ${job.prepared_count}/${job.expected_count}`;
  if (job.status === "running") return "正在处理";
  if (job.status === "queued") return "等待开始";
  return "—";
}

function jobProgressTiming(job: WebSyncJob) {
  if (job.status !== "running" || !job.started_at || job.completed_count < 1) return "";
  const elapsedSeconds = Math.max(1, (Date.now() - Date.parse(job.started_at)) / 1000);
  const pagesPerSecond = job.completed_count / elapsedSeconds;
  const remainingSeconds = Math.max(0, job.expected_count - job.completed_count) / pagesPerSecond;
  return ` · ${pagesPerSecond.toFixed(2)} 页/秒 · 预计剩余 ${formatDuration(remainingSeconds)}`;
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds)) return "估算中";
  if (seconds < 60) return `${Math.ceil(seconds)} 秒`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} 分钟`;
  return `${Math.ceil(seconds / 3600)} 小时`;
}

function itemStatusLabel(status: WebSyncJobItem["status"]) {
  return { pending: "等待", fetching: "处理中", succeeded: "成功", not_modified: "未变化", excluded: "已排除", failed: "失败", canceled: "已停止" }[status];
}

function outcomeReasonLabel(reason: WebSyncJobItem["outcome_reason"]) {
  if (reason === "indexed") return "已进入知识库";
  if (reason === "not_modified") return "内容未变化";
  if (reason === "canonical_duplicate") return "规范地址重复";
  if (reason === "noindex") return "页面禁止索引";
  if (reason === "gone") return "页面已删除";
  if (reason === "robots_excluded") return "Robots 排除";
  if (reason === "approved_exclusion") return "已批准排除";
  if (reason === "policy_excluded") return "规则排除";
  if (reason === "failed") return "永久失败";
  return "等待处理";
}

function itemStatusTone(status: WebSyncJobItem["status"]) {
  if (status === "succeeded" || status === "not_modified") return "green";
  if (status === "pending" || status === "fetching") return "amber";
  if (status === "failed") return "red";
  return "neutral";
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function productionGateText(reasons: string[]) {
  const labels: Record<string, string> = {
    production_pipeline_not_ready: "分阶段发布与跨存储对账尚未通过上线验证",
    operator_flag_disabled: "运维发布开关尚未启用",
  };
  return reasons.map((reason) => labels[reason] ?? reason).join("；") || "当前环境未开放";
}

function formatAvailabilityReasons(reasons: string[]) {
  const labels: Record<string, string> = {
    crawler_disabled: "服务器尚未启用网站抓取",
    site_inactive: "站点当前未启用",
    site_not_verified: "站点所有权尚未验证，请到“设置 → 站点”完成验证",
    site_base_url_missing: "站点没有配置网站地址",
    web_sync_worker_unavailable: "后台网页同步服务未确认可用（未运行、心跳未配置或健康检查失败）",
  };
  return reasons.map((reason) => labels[reason] ?? reason).join("；");
}

function formatResourceErrors(errors: ResourceErrors) {
  const labels: Record<ResourceKey, string> = {
    jobs: "任务进度",
    manifest: "页面范围",
    readiness: "知识就绪状态",
    availability: "后台服务状态",
  };
  return (Object.entries(errors) as [ResourceKey, string][])
    .map(([key, message]) => `${labels[key]}：${message}`)
    .join("；");
}

function capabilityAllowed(
  value: boolean | { allowed: boolean } | undefined,
  legacyFallback: boolean,
) {
  if (typeof value === "boolean") return value;
  if (value) return value.allowed;
  return legacyFallback;
}

function availabilityIsFresh(availability: WebSyncAvailability | null) {
  if (!availability) return false;
  if (!availability.valid_until) return true;
  const validUntil = Date.parse(availability.valid_until);
  return Number.isFinite(validUntil) && validUntil > Date.now();
}

function workerStatusLabel(status?: string) {
  return {
    healthy: "正常",
    unavailable: "未运行或异常",
    unknown: "未配置检测",
    not_required: "爬虫未启用",
  }[status || "unknown"] || "未知";
}

function contentKindLabel(kind: string) {
  return { product: "商品", guide: "指南", category: "分类", utility: "工具", general: "通用" }[kind] ?? kind;
}

function formatContentKindCounts(counts: Record<string, number>) {
  const entries = Object.entries(counts);
  if (!entries.length) return "未分类";
  return entries.map(([kind, count]) => `${contentKindLabel(kind)} ${count}`).join(" · ");
}

function formatReadinessReasons(reasons: string[]) {
  const labels: Record<string, string> = {
    active_product_catalog_missing: "正式商品目录未发布",
    published_policy_knowledge_missing: "政策知识未发布",
    published_care_knowledge_missing: "护理知识未发布",
    published_care_knowledge_missing_for_site_language: "当前站点语言缺少已发布护理知识",
  };
  return reasons.map((reason) => labels[reason] ?? reason).join("；") || "未知阻断项";
}
