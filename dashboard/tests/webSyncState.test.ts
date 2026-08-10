import { afterEach, describe, expect, it, vi } from "vitest";

import type { WebSyncJob } from "../src/types";
import {
  effectiveExecutionState,
  executionStateLabel,
  executionStateSummary,
  isActiveWebSyncJob,
  mergeWebSyncJobs,
  startWebSyncPolling,
} from "../src/webSyncState";

afterEach(() => {
  vi.useRealTimers();
});

function job(overrides: Partial<WebSyncJob> = {}): WebSyncJob {
  return {
    job_id: "job-1",
    site_id: "site-1",
    base_url: "https://example.com",
    status: "preparing",
    trigger: "manual",
    mode: "shadow",
    publication_status: "not_requested",
    manifest_id: "manifest-1",
    sample_size: 20,
    phase: "preparing",
    manifest_version: 1,
    manifest_fingerprint: "fingerprint",
    prepared_count: 0,
    expected_count: 20,
    completed_count: 0,
    succeeded_count: 0,
    not_modified_count: 0,
    excluded_item_count: 0,
    failed_item_count: 0,
    canceled_item_count: 0,
    requested_by: "operator",
    requested_at: "2026-08-06T00:00:00Z",
    started_at: null,
    heartbeat_at: null,
    completed_at: null,
    cancel_requested_at: null,
    blocked_at: null,
    retention_expires_at: null,
    attempt_count: 0,
    max_attempts: 3,
    max_pages: 20,
    duplicate_product_policy: "block",
    duplicate_product_order: "ordinal",
    error_code: null,
    error_message: null,
    report: null,
    ...overrides,
  };
}

describe("web-sync job snapshots", () => {
  it("keeps a newer enqueue response when a stale poll response arrives", () => {
    const current = job({
      prepared_count: 8,
      state_version: 4,
      updated_at: "2026-08-06T00:00:04Z",
    });
    const stale = job({ prepared_count: 0, state_version: 3, updated_at: "2026-08-06T00:00:03Z" });

    expect(mergeWebSyncJobs([current], [stale])).toEqual([current]);
  });

  it("accepts a newer state version even when an earlier snapshot is retained locally", () => {
    const current = job({ prepared_count: 2, state_version: 4 });
    const newer = job({ prepared_count: 20, state_version: 5, started_at: "2026-08-06T00:01:00Z" });

    expect(mergeWebSyncJobs([current], [newer])).toEqual([newer]);
  });

  it("uses timestamps and monotonic counters while talking to an older API", () => {
    const current = job({
      status: "running",
      phase: "processing",
      prepared_count: 20,
      completed_count: 5,
      updated_at: "2026-08-06T00:01:00Z",
    });
    const stale = job({
      status: "preparing",
      prepared_count: 0,
      completed_count: 0,
      updated_at: "2026-08-06T00:00:30Z",
    });

    expect(mergeWebSyncJobs([current], [stale])).toEqual([current]);
  });

  it("normalizes unclaimed preparation as waiting for a worker", () => {
    const waiting = job();
    expect(effectiveExecutionState(waiting)).toBe("waiting_for_worker");
    expect(executionStateSummary(waiting)).toContain("等待后台处理服务");
  });

  it("exposes retry and recovery states in operator-facing copy", () => {
    expect(executionStateSummary(job({ execution_state: "waiting_retry" }))).toContain("自动重试");
    expect(executionStateSummary(job({ execution_state: "recovery_pending" }))).toContain("恢复");
  });

  it("describes staged-data cleanup as controlled recovery", () => {
    const cleanup = job({
      status: "cleanup_pending",
      execution_state: "recovery",
      reason_code: "staging_cleanup_pending",
    });

    expect(executionStateLabel(cleanup)).toBe("正在清理未发布数据");
    expect(executionStateSummary(cleanup)).toContain("当前线上知识继续正常使用");
  });

  it("keeps staging cleanup pending jobs active until the worker finishes cleanup", () => {
    expect(isActiveWebSyncJob(job({ status: "cleanup_pending" }))).toBe(true);
    expect(isActiveWebSyncJob(job({ status: "succeeded" }))).toBe(false);
  });
});

describe("web-sync polling session", () => {
  it("single-flights manual refreshes and aborts the active request on stop", async () => {
    vi.useFakeTimers();
    let resolveTask: (() => void) | undefined;
    let signal: AbortSignal | undefined;
    const task = vi.fn((nextSignal: AbortSignal) => {
      signal = nextSignal;
      return new Promise<void>((resolve) => { resolveTask = resolve; });
    });

    const session = startWebSyncPolling(task, 1000);
    await Promise.resolve();
    expect(task).toHaveBeenCalledTimes(1);

    const first = session.refresh();
    const second = session.refresh();
    expect(first).toBe(second);
    expect(task).toHaveBeenCalledTimes(1);

    session.stop();
    expect(signal?.aborted).toBe(true);
    resolveTask?.();
    await first;
    vi.advanceTimersByTime(5000);
    expect(task).toHaveBeenCalledTimes(1);
  });

  it("starts the next delay only after the current request finishes", async () => {
    vi.useFakeTimers();
    const resolvers: Array<() => void> = [];
    const task = vi.fn(() => new Promise<void>((resolve) => { resolvers.push(resolve); }));
    const session = startWebSyncPolling(task, 1000);
    await Promise.resolve();

    vi.advanceTimersByTime(5000);
    expect(task).toHaveBeenCalledTimes(1);

    resolvers[0]?.();
    await vi.advanceTimersByTimeAsync(999);
    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(task).toHaveBeenCalledTimes(2);

    session.stop();
    resolvers[1]?.();
  });
});
