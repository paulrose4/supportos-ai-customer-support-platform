import { useEffect, useId, useState } from "react";
import { ChevronRight, RefreshCw, Save } from "lucide-react";

import { ApiRequestError, getSiteWebSourceConfig, updateSiteWebSourceConfig } from "./api";
import {
  parseSitemapUrls,
  sitemapDiscoveryModes,
  validateSitemapUrls,
} from "./content/sitemap";
import type {
  SiteWebDiscoveryMode,
  SiteWebSourceConfig,
} from "./types";

interface SitemapSourceFieldsProps {
  idPrefix: string;
  mode: SiteWebDiscoveryMode;
  sitemapText: string;
  validationStatus: SiteWebSourceConfig["validation_status"];
  validatedAt: string | null;
  disabled?: boolean;
  onModeChange: (mode: SiteWebDiscoveryMode) => void;
  onSitemapTextChange: (value: string) => void;
}

export function SitemapSourceFields({
  idPrefix,
  mode,
  sitemapText,
  validationStatus,
  validatedAt,
  disabled = false,
  onModeChange,
  onSitemapTextChange,
}: SitemapSourceFieldsProps) {
  const textareaId = `${idPrefix}-sitemap-urls`;
  const helpId = `${idPrefix}-sitemap-help`;
  const urlCount = parseSitemapUrls(sitemapText).length;
  const status = validationStatusPresentation(validationStatus, mode, urlCount);

  return <>
    <div className="sitemap-source-status" aria-live="polite">
      <span>验证状态</span>
      <span className={`state-badge ${status.tone}`}>{status.label}</span>
      {validatedAt ? <small>最近验证 {formatDateTime(validatedAt)}</small> : null}
    </div>
    <fieldset className="sitemap-mode-fieldset" disabled={disabled}>
      <legend>发现方式</legend>
      <div className="sitemap-mode-control" role="radiogroup" aria-label="站点地图发现方式">
        {sitemapDiscoveryModes.map((option) => <label key={option.value} title={option.title}>
          <input
            type="radio"
            name={`${idPrefix}-sitemap-mode`}
            value={option.value}
            checked={mode === option.value}
            onChange={() => onModeChange(option.value)}
          />
          <span>{option.label}</span>
        </label>)}
      </div>
    </fieldset>
    <label className="sitemap-url-field" htmlFor={textareaId}>
      <span>站点地图地址（每行一个）</span>
      <textarea
        id={textareaId}
        value={sitemapText}
        onChange={(event) => onSitemapTextChange(event.target.value)}
        placeholder={"https://www.example.com/sitemap_index.xml\nhttps://www.example.com/product-map"}
        aria-describedby={helpId}
        disabled={disabled || mode === "auto"}
        rows={4}
        spellCheck={false}
      />
      <small id={helpId}>{urlCount}/10 个地址</small>
    </label>
  </>;
}

export function SiteWebSourceEditor({ siteId, baseUrl }: { siteId: string; baseUrl: string }) {
  const generatedId = useId().replace(/:/g, "");
  const [mode, setMode] = useState<SiteWebDiscoveryMode>("hybrid");
  const [sitemapText, setSitemapText] = useState("");
  const [validationStatus, setValidationStatus] = useState<SiteWebSourceConfig["validation_status"]>("unvalidated");
  const [validatedAt, setValidatedAt] = useState<string | null>(null);
  const [allowedSitemapOrigins, setAllowedSitemapOrigins] = useState<string[]>([]);
  const [configVersion, setConfigVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoaded(false);
    setError("");
    void getSiteWebSourceConfig(siteId)
      .then((config) => {
        if (!active) return;
        setMode(config.discovery_mode);
        setSitemapText(config.explicit_sitemap_urls.join("\n"));
        setValidationStatus(config.validation_status);
        setValidatedAt(config.validated_at);
        setAllowedSitemapOrigins(config.allowed_sitemap_origins);
        setConfigVersion(config.config_version);
        setLoaded(true);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "读取站点地图配置失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [baseUrl, reloadKey, siteId]);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const urls = parseSitemapUrls(sitemapText);
    const validationError = validateSitemapUrls(
      urls,
      baseUrl,
      mode,
      allowedSitemapOrigins,
    );
    if (validationError) {
      setError(validationError);
      setNotice("");
      return;
    }

    setSaving(true);
    setError("");
    setNotice("");
    try {
      const config = await updateSiteWebSourceConfig(siteId, {
        discovery_mode: mode,
        explicit_sitemap_urls: urls,
        expected_config_version: configVersion,
      });
      setMode(config.discovery_mode);
      setSitemapText(config.explicit_sitemap_urls.join("\n"));
      setValidationStatus(config.validation_status);
      setValidatedAt(config.validated_at);
      setAllowedSitemapOrigins(config.allowed_sitemap_origins);
      setConfigVersion(config.config_version);
      setNotice("配置已保存，请在网站知识同步中重新检测范围。");
    } catch (reason) {
      if (reason instanceof ApiRequestError && reason.status === 409) {
        setLoaded(false);
        setError("配置已被其他操作更新，请重新读取后再保存。");
      } else {
        setError(reason instanceof Error ? reason.message : "保存站点地图配置失败");
      }
    } finally {
      setSaving(false);
    }
  }

  const status = validationStatusPresentation(
    validationStatus,
    mode,
    parseSitemapUrls(sitemapText).length,
  );

  return <details className="site-web-source-panel">
    <summary>
      <ChevronRight aria-hidden="true" />
      <span>知识同步来源</span>
      {!loading && <span className={`state-badge ${status.tone}`}>
        {status.label}
      </span>}
    </summary>
    {loading ? <p className="sitemap-source-loading" role="status">正在读取配置…</p> : !loaded ? <div className="sitemap-source-load-error">
      <div className="inline-error" role="alert">{error || "读取站点地图配置失败"}</div>
      <button className="secondary-small" type="button" onClick={() => setReloadKey((value) => value + 1)}>
        <RefreshCw aria-hidden="true" />重新读取
      </button>
    </div> : <form onSubmit={save} aria-label={`${siteId} 的站点地图配置`}>
      <SitemapSourceFields
        idPrefix={generatedId}
        mode={mode}
        sitemapText={sitemapText}
        validationStatus={validationStatus}
        validatedAt={validatedAt}
        disabled={saving}
        onModeChange={setMode}
        onSitemapTextChange={setSitemapText}
      />
      {error && <div className="inline-error" role="alert">{error}</div>}
      {notice && <div className="inline-warning" role="status">{notice}</div>}
      <div className="sitemap-source-actions">
        <button className="primary-small" type="submit" disabled={saving}>
          <Save aria-hidden="true" />{saving ? "保存中…" : "保存来源"}
        </button>
      </div>
    </form>}
  </details>;
}

function validationStatusPresentation(
  value: SiteWebSourceConfig["validation_status"],
  mode: SiteWebDiscoveryMode,
  urlCount: number,
): {
  label: string;
  tone: "green" | "amber" | "red" | "neutral";
} {
  if (value === "valid") return { label: "已验证", tone: "green" };
  if (value === "invalid") return { label: "验证失败", tone: "red" };
  if (mode === "auto" || (mode === "hybrid" && urlCount === 0)) {
    return { label: "自动发现", tone: "neutral" };
  }
  return { label: "待检测", tone: "amber" };
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
