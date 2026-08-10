import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "site-connectors" / "shared-widget"
WORDPRESS_PUBLIC = ROOT / "wordpress-plugin" / "company-product-support-agent" / "public"
STATIC_ROOT = ROOT / "site-connectors" / "static-php"
STATIC_PUBLIC = STATIC_ROOT / "public" / "support-agent"
API_ASSETS = ROOT / "app" / "api" / "assets"
WORKER_ROOT = ROOT / "site-connectors" / "cloudflare-worker"
WORKER_SOURCE = WORKER_ROOT / "src" / "index.ts"


def test_wordpress_and_static_connectors_share_canonical_widget_assets() -> None:
    shared_javascript = (SHARED / "widget.js").read_text(encoding="utf-8")
    shared_runtime = (SHARED / "widget-runtime.js").read_text(encoding="utf-8")
    shared_stylesheet = (SHARED / "widget.css").read_text(encoding="utf-8")

    assert (WORDPRESS_PUBLIC / "js" / "widget.js").read_text(encoding="utf-8") == shared_javascript
    assert (WORDPRESS_PUBLIC / "js" / "widget-runtime.js").read_text(
        encoding="utf-8"
    ) == shared_runtime
    assert (WORDPRESS_PUBLIC / "css" / "widget.css").read_text(
        encoding="utf-8"
    ) == shared_stylesheet
    assert (STATIC_PUBLIC / "widget.js").read_text(encoding="utf-8") == shared_javascript
    assert (STATIC_PUBLIC / "widget-runtime.js").read_text(encoding="utf-8") == shared_runtime
    assert (STATIC_PUBLIC / "widget.css").read_text(encoding="utf-8") == shared_stylesheet
    assert (API_ASSETS / "widget.js").read_text(encoding="utf-8") == shared_javascript
    assert (API_ASSETS / "widget-runtime.js").read_text(encoding="utf-8") == shared_runtime
    assert (API_ASSETS / "widget.css").read_text(encoding="utf-8") == shared_stylesheet


def test_shared_widget_has_safe_defaults_and_prevents_duplicate_mounts() -> None:
    javascript = (SHARED / "widget-runtime.js").read_text(encoding="utf-8")

    assert "rawConfig.siteId" in javascript
    assert "primaryLanguage" in javascript
    assert "payload.primary_language" in javascript
    assert "Hello! How can I help you today?" in javascript
    assert "您好！今天有什么可以帮您？" in javascript
    assert "こんにちは。今日はどのようなご用件でしょうか？" in javascript
    assert "Related pages" in javascript
    assert 'createElement("details", "cpsa-widget__citations")' not in javascript
    assert 'createElement("div", "cpsa-widget__citations")' in javascript
    assert "payload.related_links || []" in javascript
    assert "payload.citations || []" not in javascript
    assert "appendFormattedText" in javascript
    assert "document.createTextNode" in javascript
    assert "return /^https?:\\/\\//i.test(source);" in javascript
    assert "data-cpsa-widget-root" in javascript
    assert "innerHTML" not in javascript
    assert "X-Agent-Site-Key" not in javascript
    assert "site_key" not in javascript
    assert "public_widget_id" in javascript
    assert "messagesEndpoint" in javascript
    assert "pollHumanMessages" in javascript
    assert "let activeConversationId" in javascript
    assert "let conversationRevision" in javascript
    assert "requestRevision !== conversationRevision" in javascript
    assert "activeConversationId = opaqueId" in javascript
    assert 'credentials: "omit"' in javascript
    assert '"Content-Type": "text/plain;charset=UTF-8"' in javascript
    assert javascript.count('page_path: window.location.pathname || "/"') == 1


def test_shared_widget_defers_service_requests_and_reuses_browser_state() -> None:
    loader = (SHARED / "widget.js").read_text(encoding="utf-8")
    javascript = (SHARED / "widget-runtime.js").read_text(encoding="utf-8")

    assert len(gzip.compress(loader.encode("utf-8"))) < 10_000
    assert "widget-runtime.js" in loader
    assert "loadRuntime(root, button)" in loader
    assert "/v1/public-widget/presence" in loader
    assert "public_widget_id" in loader
    assert "presence_token" in loader
    assert "runtime_version" in loader
    assert "config_version" in loader
    assert "connector_type" in loader
    assert "page_view_id" in loader
    assert "visibilitychange" in loader
    assert "pushState" in loader
    assert "BroadcastChannel" in loader
    assert "widget.css" in loader
    assert "/v1/public-widget/appearance" in loader
    assert "appearance.launcher_image_url" in loader
    assert 'sourceUrl.searchParams.get("ver")' in loader
    assert "cpsa_appearance_" in loader
    assert 'cache: "no-cache"' in loader
    assert 'button.textContent = "?"' not in loader
    assert "cpsa-loader__fallback" in loader

    start_body = javascript.split("function start()", 1)[1].split("if (document.readyState", 1)[0]
    assert "ensureSession" not in start_body
    assert "sendPresence" not in start_body
    assert "visitorCookieName" in javascript
    assert "VISITOR_COOKIE_MAX_AGE_SECONDS = 2592000" in javascript
    assert "SameSite=Lax" in javascript
    assert "sessionStorageKey" in javascript
    assert "restoreCachedSession" in javascript
    assert "cacheSession(payload)" in javascript
    assert "restoreCachedAppearance" in javascript
    assert "refreshAppearance" in javascript
    assert "cached.widgetConfig" not in javascript
    assert "publicConfig || window.CPSAWidgetConfig" in javascript
    open_body = javascript.split("async function openWidget()", 1)[1].split(
        'launcher.addEventListener("click"', 1
    )[0]
    assert "await ensureSession(false);" in open_body
    assert "if (sessionReady) startActivity();" in open_body
    assert "stopActivity();" in javascript
    assert "claimActivityLease" in javascript
    assert "BroadcastChannel" in javascript
    assert "activityLeaseOwnedByAnotherTab" in javascript
    assert "stopEventStream();" in javascript
    assert "sendPresence" not in javascript
    assert 'CustomEvent("cpsa:widget-opened"' in javascript
    assert 'CustomEvent("cpsa:widget-closed"' in javascript
    assert 'CustomEvent("cpsa:conversation-linked"' in javascript
    assert 'window.addEventListener("pagehide", stopActivity)' in javascript
    assert "published.launcher_image_url" in javascript
    assert "cpsa-widget__launcher--open" in javascript
    assert 'createElement("button", "cpsa-widget__launcher", "?")' not in javascript


def test_shared_widget_excludes_automated_clients_before_loading_assets() -> None:
    javascript = (SHARED / "widget.js").read_text(encoding="utf-8")

    bot_check = javascript.index("BOT_PATTERN.test(userAgent)")
    runtime_load = javascript.index('document.createElement("script")')
    assert bot_check < runtime_load
    assert "navigator.webdriver" in javascript
    assert "googlebot" in javascript.casefold()
    assert "bytespider" in javascript.casefold()
    assert "gptbot" in javascript.casefold()
    assert "claudebot" in javascript.casefold()


def test_static_connector_keeps_site_credential_server_side() -> None:
    connector = (STATIC_ROOT / "src" / "connector.php").read_text(encoding="utf-8")
    public_files = "".join(
        path.read_text(encoding="utf-8") for path in STATIC_PUBLIC.rglob("*") if path.is_file()
    )

    assert "X-Agent-Site-Key: ' . $config['site_key']" in connector
    assert "CPSA_CONFIG_PATH" in connector
    assert "site_key" not in public_files
    assert "tenant_id" not in connector
    assert "customer_id" not in connector
    assert "$validated['page_path'] = $pagePath" in connector
    assert "'related_links' => $relatedLinks" in connector


def test_static_connector_enforces_origin_rate_limits_and_tls() -> None:
    connector = (STATIC_ROOT / "src" / "connector.php").read_text(encoding="utf-8")

    assert "cpsa_require_same_origin" in connector
    assert "hash_hmac('sha256'" in connector
    assert "flock($handle, LOCK_EX)" in connector
    assert "CURLOPT_SSL_VERIFYPEER => true" in connector
    assert "CURLOPT_SSL_VERIFYHOST => 2" in connector
    assert "allow_insecure_agent_api" in connector
    assert "strlen($config['site_key']) < 32" in connector


def test_static_embed_uses_same_origin_proxy_without_secret() -> None:
    embed = (STATIC_PUBLIC / "embed-example.html").read_text(encoding="utf-8")

    assert "https://livechatgo.com/widget.js?v=ASSET_VERSION" in embed
    assert 'data-site-id="site_pub_replace_with_dashboard_id"' in embed
    assert 'data-connector-type="static_php"' in embed
    assert 'data-cfasync="false"' in embed
    assert "site_key" not in embed
    assert "X-Agent-Site-Key" not in embed


def test_connector_builds_never_package_runtime_config() -> None:
    build_script = (ROOT / "scripts" / "build_site_connectors.py").read_text(encoding="utf-8")
    example = (STATIC_ROOT / "private" / "config.example.php").read_text(encoding="utf-8")

    assert 'path.name != "config.php"' in build_script
    assert "CPSA_CONNECTOR_BOOTSTRAPPED" in example
    assert "replace-with-at-least-32-random-characters" in example


def test_cloudflare_worker_keeps_site_credential_outside_browser_assets() -> None:
    source = WORKER_SOURCE.read_text(encoding="utf-8")
    embed = (WORKER_ROOT / "embed-example.html").read_text(encoding="utf-8")

    assert '"X-Agent-Site-Key": env.AGENT_SITE_KEY' in source
    assert "validateOrigin" in source
    assert "CHAT_LIMITER" in source
    assert "PRESENCE_LIMITER" in source
    assert "PRESENCE_SOURCE_LIMITER" in source
    assert "PRESENCE_SITE_LIMITER" in source
    assert "MAX_BODY_BYTES = 16_384" in source
    assert "AGENT_SITE_KEY" not in embed
    assert "page_path: pagePath" in source
    assert "https://livechatgo.com/widget.js?v=ASSET_VERSION" in embed
    assert 'data-site-id="site_pub_replace_with_dashboard_id"' in embed
    assert 'data-connector-type="cloudflare_worker"' in embed
    assert "validateMessagesPayload" in source
    assert "related_links: relatedLinks" in source


def test_cloudflare_worker_deployment_requires_https_and_exact_site_route() -> None:
    config = (WORKER_ROOT / "wrangler.toml.example").read_text(encoding="utf-8")
    source = WORKER_SOURCE.read_text(encoding="utf-8")

    assert 'pattern = "shop.example.com/support-agent/chat*"' in config
    assert 'pattern = "shop.example.com/support-agent/messages*"' in config
    assert 'pattern = "shop.example.com/support-agent/presence*"' in config
    assert 'PUBLIC_ORIGIN = "https://shop.example.com"' in config
    assert 'ALLOW_INSECURE_AGENT_API = "false"' in config
    assert "simple = { limit = 20, period = 60 }" in config
    assert "simple = { limit = 6, period = 60 }" in config
    assert "simple = { limit = 120, period = 60 }" in config
    assert "simple = { limit = 30000, period = 60 }" in config
    assert 'env.ALLOW_INSECURE_AGENT_API === "true"' in source
    assert '"invalid_origin"' in source
