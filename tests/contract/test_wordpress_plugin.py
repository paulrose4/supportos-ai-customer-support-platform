from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "wordpress-plugin" / "company-product-support-agent"


def test_frontend_bundle_never_contains_site_key_setting() -> None:
    main = (PLUGIN / "company-product-support-agent.php").read_text(encoding="utf-8")
    javascript = (PLUGIN / "public/js/widget.js").read_text(encoding="utf-8")

    inline_config = main.split("$config = array(", 1)[1].split("wp_add_inline_script", 1)[0]
    assert "site_key" not in inline_config
    assert "X-Agent-Site-Key" not in javascript
    assert "site_key" not in javascript


def test_wordpress_proxy_is_the_only_component_that_adds_site_credential() -> None:
    controller = (PLUGIN / "includes/class-cpsa-rest-controller.php").read_text(encoding="utf-8")

    assert "'X-Agent-Site-Key' => $options['site_key']" in controller
    assert "'/v1/widget/chat'" in controller
    assert "'/v1/widget/presence'" in controller
    assert "'tenant_id'" not in controller
    assert "'customer_id'" not in controller
    assert "'related_links'" in controller


def test_widget_renders_remote_content_without_inner_html() -> None:
    loader = (PLUGIN / "public/js/widget.js").read_text(encoding="utf-8")
    javascript = (PLUGIN / "public/js/widget-runtime.js").read_text(encoding="utf-8")

    assert ".textContent" in javascript
    assert "innerHTML" not in javascript
    assert "innerHTML" not in loader


def test_plugin_declares_safe_rest_permission_and_rate_limit() -> None:
    controller = (PLUGIN / "includes/class-cpsa-rest-controller.php").read_text(encoding="utf-8")

    assert "'permission_callback' => '__return_true'" in controller
    assert "consume_rate_limit" in controller
    assert "is_same_site_request" in controller
    assert "sslverify' => true" in controller


def test_loader_sends_page_presence_without_starting_chat_runtime() -> None:
    loader = (PLUGIN / "public/js/widget.js").read_text(encoding="utf-8")
    runtime = (PLUGIN / "public/js/widget-runtime.js").read_text(encoding="utf-8")

    assert "presenceEndpoint" in loader
    assert "sendPresence" in loader
    assert "PRESENCE_MIN_INTERVAL_MS = 20000" in loader
    assert "PRESENCE_MAX_INTERVAL_MS = 25000" in loader
    assert "document.visibilityState" in loader
    assert "page_view_id: pageViewId" in loader
    assert 'event: lastSentPageViewId === pageViewId ? "heartbeat" : "enter"' in loader
    assert "sendPresence" not in runtime
    assert 'CustomEvent("cpsa:widget-opened"' in runtime


def test_wordpress_presence_mode_is_configurable_and_defaults_to_safe_rollout() -> None:
    main = (PLUGIN / "company-product-support-agent.php").read_text(encoding="utf-8")
    settings = (PLUGIN / "includes/class-cpsa-settings.php").read_text(encoding="utf-8")

    assert "'presenceMode'      => $options['presence_mode']" in main
    assert "'presence_mode'   => 'widget_only'" in settings
    assert "array( 'page_view', 'widget_only', 'disabled' )" in settings


def test_widget_chat_timeout_starts_after_session_bootstrap_and_retries_safely() -> None:
    javascript = (PLUGIN / "public/js/widget-runtime.js").read_text(encoding="utf-8")

    assert "const CHAT_TIMEOUT_MS = 60000" in javascript
    assert "await ensureSession(false)" in javascript
    assert "payload.request_id = opaqueId" in javascript
    assert "async function chatRequest(sendRequest)" in javascript
    assert "RETRYABLE_CHAT_STATUSES" in javascript
    assert "sendChat(text, requestConversationId)" in javascript
    assert "sendChat(text, controller.signal)" not in javascript
    assert "25000" not in javascript
