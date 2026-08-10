import type { SiteWebDiscoveryMode } from "../types";

export const sitemapDiscoveryModes: Array<{
  value: SiteWebDiscoveryMode;
  label: string;
  title: string;
}> = [
  { value: "auto", label: "自动发现", title: "从 robots.txt 和常见地址自动寻找站点地图" },
  { value: "hybrid", label: "人工优先", title: "优先使用填写的地址，失效时自动寻找可用入口" },
  { value: "manual", label: "仅人工", title: "只使用填写的地址，任一入口失效都会阻断同步" },
];

export function parseSitemapUrls(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

export function validateSitemapUrls(
  urls: string[],
  baseUrl: string,
  mode: SiteWebDiscoveryMode,
  allowedSitemapOrigins: string[] = [],
): string | null {
  if (mode === "manual" && urls.length === 0) return "仅人工模式至少需要填写一个站点地图地址";
  if (urls.length > 10) return "每个站点最多可配置 10 个站点地图地址";

  let expectedSite: URL;
  try {
    expectedSite = new URL(baseUrl);
  } catch {
    return "请先保存有效的网站地址";
  }
  if (expectedSite.protocol !== "https:" || expectedSite.port) {
    return "网站地址必须使用标准 HTTPS 端口后才能配置站点地图";
  }
  const expectedOrigin = expectedSite.origin;
  const allowedOrigins = new Set(allowedSitemapOrigins);
  allowedOrigins.add(expectedOrigin);

  for (const value of urls) {
    if (value.length > 2048) return "单个站点地图地址不能超过 2048 个字符";
    if (/\s/.test(value)) return `站点地图地址不能包含空格：${value}`;
    let parsed: URL;
    try {
      parsed = new URL(value);
    } catch {
      return `站点地图地址无效：${value}`;
    }
    if (parsed.protocol !== "https:") return `站点地图地址必须使用 HTTPS：${value}`;
    if (parsed.port) return `站点地图地址必须使用标准 HTTPS 端口：${value}`;
    if (!allowedOrigins.has(parsed.origin)) {
      return `站点地图地址必须属于当前网站或同租户已验证站点：${value}`;
    }
    if (parsed.username || parsed.password) return `站点地图地址不能包含账号或密码：${value}`;
    if (parsed.hash) return `站点地图地址不能包含 #fragment：${value}`;
  }
  return null;
}

export function sitemapDiscoveryModeLabel(value?: string): string {
  return ({
    auto: "自动发现",
    hybrid: "人工优先",
    manual: "仅人工",
    robots_txt: "robots.txt 声明",
    last_known_good: "上次成功入口",
    common_path: "常见地址探测",
    legacy: "历史默认入口",
    none: "未发现",
  } as Record<string, string>)[value || ""] || value || "未检测";
}

export function sitemapCoverageLabel(value?: string): string {
  return ({
    declared_complete: "范围完整",
    incomplete: "范围不完整",
  } as Record<string, string>)[value || ""] || value || "未检测";
}

export function sitemapRootUrls(manifest: {
  root_sitemap_url?: string;
  root_sitemap_urls?: string[];
}): string[] {
  if (Object.prototype.hasOwnProperty.call(manifest, "root_sitemap_urls")) {
    return Array.isArray(manifest.root_sitemap_urls) ? manifest.root_sitemap_urls : [];
  }
  return manifest.root_sitemap_url ? [manifest.root_sitemap_url] : [];
}

export function sitemapReasonLabel(value: string): string {
  return ({
    sitemap_not_discovered: "未找到可解析的站点地图",
    configured_sitemap_required: "仅人工模式尚未配置站点地图地址",
    configured_sitemap_incomplete: "部分人工配置的站点地图无效或不可访问",
    sitemap_roots_incomplete: "robots.txt 声明的部分站点地图无效或不可访问",
    sitemap_tree_incomplete: "站点地图索引中的子地图未能完整读取",
    sitemap_limit_reached: "站点地图数量超过服务器安全上限",
    manifest_url_limit_reached: "页面数量超过预检安全上限，未生成完整同步范围",
    untrusted_or_invalid_sitemap_url: "站点地图包含未受信任或无效的子地图地址",
    no_primary_language_urls: "站点地图中没有识别到网站主语言页面",
    translated_url_remained_in_manifest: "页面范围中仍包含需要排除的翻译页面",
  } as Record<string, string>)[value] || value;
}

export function sitemapWarningLabel(value: string): string {
  return ({
    configured_sitemap_fallback_used: "人工配置的地址不可用，本次已改用自动发现的入口",
    last_known_good_sitemap_used: "本次使用了上次检测成功的站点地图入口",
    sitemap_retry_recovered: "部分子站点地图首次读取失败，系统重试后已恢复",
  } as Record<string, string>)[value] || value;
}

export function sitemapAttemptSourceLabel(value: string): string {
  return ({
    manual: "人工配置",
    robots_txt: "robots.txt",
    last_known_good: "上次成功入口",
    common_path: "常见地址",
    nested: "子地图",
  } as Record<string, string>)[value] || value;
}

export function sitemapAttemptOutcomeLabel(value: string): string {
  if (value.startsWith("retrying_")) {
    return `首次${sitemapAttemptOutcomeLabel(value.slice("retrying_".length))}，正在重试`;
  }
  return ({
    accepted: "已接受",
    sitemaps_found: "已找到声明",
    no_sitemaps: "没有声明",
    http_not_found: "文件不存在",
    http_error: "HTTP 请求失败",
    fetch_failed: "读取失败",
    invalid_sitemap: "不是有效站点地图",
    untrusted_redirect: "重定向目标未受信任",
    untrusted_target: "目标地址未受信任",
    invalid_url: "地址无效",
    unsupported_content_type: "响应格式不受支持",
    wire_bytes_exceeded: "响应体超过传输上限",
    decompressed_bytes_exceeded: "解压后内容超过上限",
    compression_ratio_exceeded: "响应压缩比例异常",
  } as Record<string, string>)[value] || value;
}
