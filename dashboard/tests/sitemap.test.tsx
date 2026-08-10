import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  parseSitemapUrls,
  sitemapReasonLabel,
  sitemapRootUrls,
  sitemapWarningLabel,
  validateSitemapUrls,
} from "../src/content/sitemap";
import { SitemapSourceFields } from "../src/siteWebSourceEditor";

describe("sitemap source input", () => {
  it("trims, removes blank lines, and deduplicates URLs without changing valid dynamic paths", () => {
    expect(parseSitemapUrls([
      " https://example.com/sitemap_index.xml ",
      "",
      "https://example.com/custom-map?type=products",
      "https://example.com/sitemap_index.xml",
    ].join("\n"))).toEqual([
      "https://example.com/sitemap_index.xml",
      "https://example.com/custom-map?type=products",
    ]);
  });

  it("accepts same-origin HTTPS URLs and rejects unsafe or incomplete manual settings", () => {
    expect(validateSitemapUrls(
      ["https://example.com/custom-map?type=products"],
      "https://example.com",
      "manual",
    )).toBeNull();
    expect(validateSitemapUrls([], "https://example.com", "manual")).toContain("至少需要");
    expect(validateSitemapUrls(
      ["https://cdn.example.com/sitemap.xml"],
      "https://example.com",
      "hybrid",
    )).toContain("已验证站点");
    expect(validateSitemapUrls(
      ["https://cdn.example.com/sitemap.xml"],
      "https://example.com",
      "hybrid",
      ["https://example.com", "https://cdn.example.com"],
    )).toBeNull();
    expect(validateSitemapUrls(
      ["https://example.com:8443/sitemap.xml"],
      "https://example.com",
      "hybrid",
    )).toContain("标准 HTTPS 端口");
  });

  it("renders native, labelled controls for keyboard and assistive-technology users", () => {
    const html = renderToStaticMarkup(<SitemapSourceFields
      idPrefix="brand-cn"
      mode="hybrid"
      sitemapText="https://example.com/sitemap.xml"
      validationStatus="unvalidated"
      validatedAt={null}
      onModeChange={vi.fn()}
      onSitemapTextChange={vi.fn()}
    />);

    expect(html).toContain("<fieldset");
    expect(html).toContain("<legend>发现方式</legend>");
    expect(html.match(/type="radio"/g)).toHaveLength(3);
    expect(html).toContain('role="radiogroup"');
    expect(html).toContain('aria-label="站点地图发现方式"');
    expect(html).toContain('id="brand-cn-sitemap-urls"');
    expect(html).toContain('aria-describedby="brand-cn-sitemap-help"');
  });

  it("uses operator-friendly Chinese labels for warnings and blockers", () => {
    expect(sitemapWarningLabel("configured_sitemap_fallback_used")).toContain("自动发现");
    expect(sitemapWarningLabel("sitemap_retry_recovered")).toContain("重试后已恢复");
    expect(sitemapReasonLabel("sitemap_tree_incomplete")).toContain("子地图");
    expect(sitemapReasonLabel("manifest_url_limit_reached")).toBe(
      "页面数量超过预检安全上限，未生成完整同步范围",
    );
  });

  it("does not turn an explicit empty root list into a legacy sitemap entry", () => {
    expect(sitemapRootUrls({
      root_sitemap_urls: [],
      root_sitemap_url: "https://example.com/sitemap.xml",
    })).toEqual([]);
    expect(sitemapRootUrls({
      root_sitemap_url: "https://example.com/legacy-sitemap.xml",
    })).toEqual(["https://example.com/legacy-sitemap.xml"]);
  });
});
