import type { WebSyncJob } from "./types";

/**
 * A single poll session owns its timer and in-flight request.  Calling
 * refresh while a request is active joins that request instead of starting a
 * second fetch.  This is deliberately independent of React so it can be
 * exercised with fake timers and reused by other views.
 */
export interface WebSyncPollSession {
  refresh: () => Promise<void>;
  stop: () => void;
}

export function startWebSyncPolling(
  task: (signal: AbortSignal) => Promise<void>,
  intervalMs = 5000,
): WebSyncPollSession {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let activeController: AbortController | null = null;
  let inFlight: Promise<void> | null = null;

  const refresh = (): Promise<void> => {
    if (stopped) return Promise.resolve();
    if (inFlight) return inFlight;

    const controller = new AbortController();
    activeController = controller;
    let request: Promise<void>;
    request = Promise.resolve()
      .then(() => task(controller.signal))
      .finally(() => {
        if (inFlight === request) inFlight = null;
        if (activeController === controller) activeController = null;
      });
    inFlight = request;
    return request;
  };

  const schedule = async () => {
    try {
      await refresh();
    } catch {
      // The task owns resource-level error reporting. Keep the scheduler alive.
    }
    if (!stopped) timer = setTimeout(() => void schedule(), intervalMs);
  };

  void schedule();

  return {
    refresh,
    stop: () => {
      stopped = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
      activeController?.abort();
      activeController = null;
    },
  };
}

export function isAbortError(reason: unknown): boolean {
  if (typeof DOMException !== "undefined" && reason instanceof DOMException) {
    return reason.name === "AbortError";
  }
  return typeof reason === "object"
    && reason !== null
    && "name" in reason
    && (reason as { name?: unknown }).name === "AbortError";
}

function stateVersion(job: WebSyncJob): number | null {
  return typeof job.state_version === "number" && Number.isFinite(job.state_version)
    ? job.state_version
    : null;
}

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function terminalStatus(status: WebSyncJob["status"]): boolean {
  return status === "succeeded" || status === "failed" || status === "canceled";
}

function olderJob(current: WebSyncJob, incoming: WebSyncJob): boolean {
  const currentVersion = stateVersion(current);
  const incomingVersion = stateVersion(incoming);
  if (currentVersion !== null && incomingVersion !== null) {
    return incomingVersion < currentVersion;
  }
  // During a rolling API deployment, an old response can omit the new
  // version field. Keep the versioned state until an authoritative response
  // catches up.
  if (currentVersion !== null && incomingVersion === null) return true;
  if (currentVersion === null && incomingVersion !== null) return false;

  const currentUpdated = timestamp(current.updated_at);
  const incomingUpdated = timestamp(incoming.updated_at);
  if (currentUpdated !== null && incomingUpdated !== null) {
    if (incomingUpdated < currentUpdated) return true;
    if (incomingUpdated > currentUpdated) return false;
  } else if (currentUpdated !== null && incomingUpdated === null) {
    return true;
  }

  if (incoming.attempt_count < current.attempt_count) return true;
  if (incoming.completed_count < current.completed_count) return true;
  if (incoming.prepared_count < current.prepared_count) return true;
  if (terminalStatus(current.status) && !terminalStatus(incoming.status)) return true;
  return false;
}

/** Merge a server snapshot without allowing an older response to regress UI. */
export function mergeWebSyncJobs(
  current: WebSyncJob[],
  incoming: WebSyncJob[],
  limit = 30,
): WebSyncJob[] {
  const byId = new Map(current.map((job) => [job.job_id, job]));
  for (const job of incoming) {
    const previous = byId.get(job.job_id);
    if (!previous || !olderJob(previous, job)) byId.set(job.job_id, job);
  }
  return [...byId.values()]
    .sort((left, right) => {
      const leftTime = timestamp(left.requested_at) ?? 0;
      const rightTime = timestamp(right.requested_at) ?? 0;
      return rightTime - leftTime || left.job_id.localeCompare(right.job_id);
    })
    .slice(0, limit);
}

/** Jobs that still own the site's active-sync guard or need worker recovery. */
export function isActiveWebSyncJob(job: WebSyncJob): boolean {
  return job.status === "preparing"
    || job.status === "queued"
    || job.status === "running"
    || job.status === "blocked"
    || job.status === "cleanup_pending";
}

export type WebSyncExecutionState =
  | "waiting_for_worker"
  | "preparing"
  | "waiting_retry"
  | "recovery"
  | "recovery_pending"
  | "stalled"
  | "attention_required"
  | "processing"
  | "finalizing"
  | "terminal"
  | string;

export function effectiveExecutionState(job: WebSyncJob): WebSyncExecutionState | null {
  if (job.execution_state) return job.execution_state;
  // Preserve useful behavior with responses from older servers.
  if (job.status === "preparing" && !job.started_at) {
    return "waiting_for_worker";
  }
  return null;
}

export function executionStateLabel(job: WebSyncJob): string | null {
  const state = effectiveExecutionState(job);
  if (state === "recovery" && job.reason_code === "staging_cleanup_pending") {
    return "正在清理未发布数据";
  }
  return ({
    waiting_for_worker: "等待后台处理服务",
    preparing: "正在准备",
    waiting_retry: "等待自动重试",
    recovery: "等待后台服务恢复",
    recovery_pending: "等待后台服务恢复",
    stalled: "进度暂未更新",
    attention_required: "需要处理",
    processing: "正在处理",
    finalizing: "正在发布",
    terminal: "已结束",
  } as Record<string, string>)[state || ""] || null;
}

export function executionStateSummary(job: WebSyncJob): string | null {
  const state = effectiveExecutionState(job);
  if (state === "waiting_for_worker") return "任务已提交，正在等待后台处理服务接单。";
  if (state === "waiting_retry") return "部分页面将在稍后自动重试，已完成页面会继续保留。";
  if (state === "recovery" && job.reason_code === "staging_cleanup_pending") {
    return "正在清理本次未发布数据，当前线上知识继续正常使用。";
  }
  if (state === "recovery" || state === "recovery_pending") {
    return "后台处理服务暂时不可用，恢复后会继续本次任务。";
  }
  if (state === "stalled") return "任务进度长时间未更新，请检查后台处理服务。";
  if (state === "attention_required") return "任务需要处理后才能继续。";
  return null;
}
