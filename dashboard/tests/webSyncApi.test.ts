import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getLatestWebCrawlManifest,
  getSiteKnowledgeReadiness,
  getWebSyncAvailability,
  listWebSyncJobs,
} from "../src/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("web-sync polling API", () => {
  it("passes the poll session AbortSignal to every independent resource request", async () => {
    const fetchMock = vi.fn((path: string) => {
      const body = path.includes("web-sync-jobs") ? { items: [] } : null;
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await Promise.all([
      listWebSyncJobs("site/a", 30, controller.signal),
      getLatestWebCrawlManifest("site/a", controller.signal),
      getSiteKnowledgeReadiness("site/a", controller.signal),
      getWebSyncAvailability("site/a", controller.signal),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.signal).toBe(controller.signal);
    }
  });
});
