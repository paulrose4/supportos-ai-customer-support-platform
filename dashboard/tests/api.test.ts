import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getSiteWebSourceConfig,
  listWebSyncJobItems,
  listWidgetAssets,
  queryPlatformSites,
  queryPlatformTenantSites,
  selfServiceSignup,
  uploadWidgetAsset,
  updateSiteWebSourceConfig,
} from "../src/api";

const signupValues = {
  display_name: "Test Owner",
  email: "owner@example.com",
  enterprise_code: "A".repeat(24),
  password: "a-secure-password",
  workspace_name: "Test Workspace",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("selfServiceSignup", () => {
  it("preserves the JSON content type when adding the idempotency key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ status: "pending_verification", status_token: "token", expires_at: "soon" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await selfServiceSignup(signupValues, "idempotency-key-1234567890");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Idempotency-Key")).toBe("idempotency-key-1234567890");
  });

  it("renders FastAPI validation details instead of object coercion", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            {
              type: "model_attributes_type",
              loc: ["body"],
              msg: "Input should be a valid dictionary or object to extract fields from",
            },
          ],
        }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      selfServiceSignup(signupValues, "idempotency-key-1234567890"),
    ).rejects.toMatchObject({
      message: "提交格式无效，请刷新页面后重试",
      status: 422,
    });
  });
});

describe("listWebSyncJobItems", () => {
  it("keeps page checkpoints paginated for full-manifest audits", async () => {
    const response = { items: [], offset: 200, next_offset: 300 };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listWebSyncJobItems("job/full", 200, 100)).resolves.toEqual(response);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/v1/knowledge/web-sync-jobs/job%2Ffull/items?offset=200&limit=100",
    );
  });
});

describe("managed widget images", () => {
  it("keeps uploads binary and applies an idempotency key", async () => {
    const responseAsset = {
      asset_id: "asset-a",
      site_id: "site/a",
      purpose: "launcher",
      status: "active",
      width: 128,
      height: 128,
      source_content_type: "image/png",
      source_byte_size: 10,
      created_at: "2026-08-05T00:00:00Z",
      url: "/v1/widget-media/asset-a?size=128",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(responseAsset), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "upload-idempotency-key" });
    const file = new File(["png-content"], "launcher.png", { type: "image/png" });

    await expect(uploadWidgetAsset("site/a", file, "launcher")).resolves.toEqual(responseAsset);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(url).toBe(
      "/v1/admin/customer-experience/sites/site%2Fa/widget-assets?purpose=launcher",
    );
    expect(init.body).toBe(file);
    expect(headers.get("Content-Type")).toBe("image/png");
    expect(headers.get("X-Idempotency-Key")).toBe("upload-idempotency-key");
  });

  it("keeps each site's asset library isolated by URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listWidgetAssets("site/b")).resolves.toEqual([]);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/v1/admin/customer-experience/sites/site%2Fb/widget-assets",
    );
  });
});

describe("site web source configuration", () => {
  const config = {
    site_id: "brand/cn",
    discovery_mode: "hybrid" as const,
    explicit_sitemap_urls: ["https://example.com/custom-map?type=products"],
    allowed_sitemap_origins: ["https://example.com"],
    config_version: 2,
    validation_status: "unvalidated" as const,
    validated_at: null,
    updated_by: "operator",
    updated_at: "2026-08-05T12:00:00Z",
  };

  it("loads a site-scoped source configuration", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(config), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getSiteWebSourceConfig("brand/cn")).resolves.toEqual(config);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/v1/admin/site-management/brand%2Fcn/web-source",
    );
  });

  it("saves the discovery mode and all explicit sitemap URLs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(config), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await updateSiteWebSourceConfig("brand/cn", {
      discovery_mode: "hybrid",
      explicit_sitemap_urls: config.explicit_sitemap_urls,
      expected_config_version: 2,
    });

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/admin/site-management/brand%2Fcn/web-source");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({
      discovery_mode: "hybrid",
      explicit_sitemap_urls: config.explicit_sitemap_urls,
      expected_config_version: 2,
    });
  });
});

describe("platform site directory", () => {
  it("serializes cross-workspace search, status, verification, and cursor filters", async () => {
    const response = { items: [], total: 0, next_cursor: null };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(queryPlatformSites({
      q: "store name",
      tenantId: "tenant-b",
      status: "disabled",
      verificationStatus: "pending",
      includeDisabled: true,
      cursor: "next/page",
      limit: 50,
    })).resolves.toEqual(response);

    const url = new URL(String(fetchMock.mock.calls[0]?.[0]), "https://dashboard.test");
    expect(url.pathname).toBe("/v1/platform/sites");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      q: "store name",
      status: "disabled",
      verification_status: "pending",
      include_disabled: "true",
      cursor: "next/page",
      limit: "50",
      tenant_id: "tenant-b",
    });
  });

  it("encodes the workspace identity for drawer site pagination", async () => {
    const response = { items: [], total: 0, next_cursor: null };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(queryPlatformTenantSites("tenant/a", {
      includeDisabled: true,
      limit: 50,
    })).resolves.toEqual(response);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/v1/platform/tenants/tenant%2Fa/sites?include_disabled=true&limit=50",
    );
  });
});
