(function () {
  "use strict";

  const scriptElement = document.currentScript;
  if (!scriptElement || document.querySelector("[data-cpsa-widget-loader], [data-cpsa-widget-root]")) {
    return;
  }

  const BOT_PATTERN = /(?:googlebot|bingbot|yandexbot|baiduspider|bytespider|petalbot|duckduckbot|gptbot|chatgpt-user|claudebot|claude-web|anthropic-ai|ccbot|perplexitybot|amazonbot|cohere-ai|meta-externalagent|slurp|semrushbot|ahrefsbot|mj12bot|dotbot|facebookexternalhit|twitterbot|linkedinbot|applebot|headlesschrome|(?:bot|crawler|spider)(?:[\s/;:_-]|$))/i;
  const userAgent = String(window.navigator && window.navigator.userAgent || "");
  if (Boolean(window.navigator && window.navigator.webdriver) || BOT_PATTERN.test(userAgent)) {
    return;
  }

  const sourceUrl = new URL(scriptElement.src, window.location.href);
  const assetVersion = sourceUrl.searchParams.get("v") || sourceUrl.searchParams.get("ver");
  const connectorConfig = window.CPSAWidgetConfig || {};
  const publicWidgetId = scriptElement.dataset.siteId || "";
  const namespace = publicWidgetId || connectorConfig.siteId || window.location.host || "default-site";
  const safeNamespace = String(namespace).replace(/[^a-z0-9_-]/gi, "_").slice(0, 64);
  const cookieName = `cpsa_visitor_${safeNamespace}`;
  const visitorStorageKey = `cpsa_visitor_${namespace}`;
  const tokenStorageKey = `cpsa_presence_token_${namespace}`;
  const sessionStorageKey = `cpsa_session_${namespace}`;
  const appearanceStorageKey = `cpsa_appearance_${namespace}`;
  const leaseKey = `cpsa_presence_lease_${namespace}`;
  const presenceMode = String(
    scriptElement.dataset.presenceMode ||
    connectorConfig.presenceMode ||
    connectorConfig.presence_mode ||
    "page_view"
  ).toLowerCase();
  const consentRequired = String(
    scriptElement.dataset.presenceConsentRequired ||
    connectorConfig.presenceConsentRequired ||
    connectorConfig.presence_consent_required ||
    "false"
  ).toLowerCase() === "true";
  const presenceEndpoint = publicWidgetId
    ? `${sourceUrl.origin}/v1/public-widget/presence`
    : String(connectorConfig.presenceEndpoint || "");
  const appearanceEndpoint = publicWidgetId
    ? `${sourceUrl.origin}/v1/public-widget/appearance?public_widget_id=${encodeURIComponent(publicWidgetId)}`
    : "";
  const PRESENCE_MIN_INTERVAL_MS = 20000;
  const PRESENCE_MAX_INTERVAL_MS = 25000;
  const LEASE_TTL_MS = 5000;
  const LEASE_POLL_MS = 2000;
  const VISITOR_COOKIE_MAX_AGE_SECONDS = 2592000;
  const runtimeVersion = String(
    assetVersion || scriptElement.dataset.runtimeVersion || "unknown",
  ).slice(0, 100);
  const connectorVersion = String(
    scriptElement.dataset.runtimeVersion || "",
  ).slice(0, 100);
  const connectorType = String(
    scriptElement.dataset.connectorType || (publicWidgetId ? "public" : "legacy"),
  ).slice(0, 32);
  let observedConfigVersion = "";

  function opaqueId(prefix) {
    return window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function storageRemove(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (_error) {
      return;
    }
  }

  function readCookie(name) {
    const prefix = `${encodeURIComponent(name)}=`;
    const item = document.cookie.split(";").map(function (part) {
      return part.trim();
    }).find(function (part) {
      return part.startsWith(prefix);
    });
    return item ? decodeURIComponent(item.slice(prefix.length)) : "";
  }

  function writeVisitorCookie(value) {
    const secure = window.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${encodeURIComponent(cookieName)}=${encodeURIComponent(value)}; Max-Age=${VISITOR_COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=Lax${secure}`;
  }

  function ensureVisitorId() {
    let value = readCookie(cookieName) || storageGet(visitorStorageKey) || "";
    if (!value) value = opaqueId("visitor");
    writeVisitorCookie(value);
    storageSet(visitorStorageKey, value);
    return value;
  }

  function siblingAsset(name) {
    const url = new URL(sourceUrl.href);
    url.search = assetVersion ? `?v=${encodeURIComponent(assetVersion)}` : "";
    url.pathname = url.pathname
      .replace(/\/js\/widget\.js$/i, `/js/${name}`)
      .replace(/\/widget\.js$/i, `/${name}`);
    if (name === "widget.css") {
      url.pathname = url.pathname.replace(/\/js\/widget\.css$/i, "/css/widget.css");
    }
    return url.href;
  }

  function loadRuntime(root, button) {
    button.disabled = true;
    if (!document.querySelector("link[data-cpsa-widget-stylesheet]")) {
      const stylesheet = document.createElement("link");
      stylesheet.rel = "stylesheet";
      stylesheet.href = siblingAsset("widget.css");
      stylesheet.dataset.cpsaWidgetStylesheet = "true";
      document.head.append(stylesheet);
    }
    const runtime = document.createElement("script");
    runtime.src = siblingAsset("widget-runtime.js");
    runtime.async = true;
    Object.keys(scriptElement.dataset).forEach(function (key) {
      runtime.dataset[key] = scriptElement.dataset[key];
    });
    if (root.dataset.launcherImageUrl) {
      runtime.dataset.launcherImageUrl = root.dataset.launcherImageUrl;
      runtime.dataset.launcherImageFit = root.dataset.launcherImageFit || "contain";
    }
    if (root.dataset.primaryColor) runtime.dataset.primaryColor = root.dataset.primaryColor;
    if (root.dataset.position) runtime.dataset.position = root.dataset.position;
    if (root.dataset.configVersion) runtime.dataset.configVersion = root.dataset.configVersion;
    runtime.dataset.assetVersion = assetVersion || runtimeVersion;
    runtime.dataset.autoOpen = "true";
    runtime.onerror = function () {
      button.disabled = false;
      if (!root.isConnected) document.body.append(root);
    };
    root.remove();
    document.head.append(runtime);
  }

  function launcherFallback() {
    const icon = document.createElement("span");
    icon.className = "cpsa-loader__fallback";
    icon.setAttribute("aria-hidden", "true");
    return icon;
  }

  function renderLauncherImage(root, button, imageUrl, fit) {
    let resolvedUrl;
    try {
      resolvedUrl = new URL(String(imageUrl || ""), sourceUrl.origin);
    } catch (_error) {
      return;
    }
    if (!/^https?:$/.test(resolvedUrl.protocol)) return;
    const normalizedFit = fit === "cover" ? "cover" : "contain";
    const image = document.createElement("img");
    image.alt = "";
    image.decoding = "async";
    image.src = resolvedUrl.href;
    image.addEventListener("load", function () {
      root.dataset.launcherImageUrl = resolvedUrl.href;
      root.dataset.launcherImageFit = normalizedFit;
      button.classList.add("cpsa-loader__button--image");
      button.classList.toggle("cpsa-loader__button--contain", normalizedFit === "contain");
      button.replaceChildren(image);
    }, { once: true });
    image.addEventListener("error", function () {
      delete root.dataset.launcherImageUrl;
      button.classList.remove("cpsa-loader__button--image", "cpsa-loader__button--contain");
      button.replaceChildren(launcherFallback());
    }, { once: true });
  }

  function readCachedAppearance() {
    try {
      const cached = JSON.parse(storageGet(appearanceStorageKey) || "null");
      if (!cached || cached.schema !== 3 || !cached.appearance) return null;
      return cached;
    } catch (_error) {
      return null;
    }
  }

  function cacheAppearance(appearance, etag) {
    storageSet(appearanceStorageKey, JSON.stringify({
      schema: 3,
      etag: String(etag || ""),
      appearance,
      checkedAt: Date.now(),
    }));
  }

  function applyAppearance(root, button, appearance) {
    if (!appearance || typeof appearance !== "object" || !root.isConnected) return;
    root.dataset.configVersion = String(
      appearance.config_version || appearance.version || "",
    );
    observedConfigVersion = root.dataset.configVersion;
    if (appearance.primary_color) {
      const color = String(appearance.primary_color);
      root.dataset.primaryColor = color;
      button.style.setProperty("--cpsa-loader-primary", color);
    }
    root.dataset.position = appearance.position === "left" ? "left" : "right";
    root.classList.toggle("cpsa-loader--left", root.dataset.position === "left");
    root.classList.toggle("cpsa-loader--desktop-only", appearance.mobile_enabled === false);
    if (appearance.launcher_image_url) {
      renderLauncherImage(
        root,
        button,
        appearance.launcher_image_url,
        appearance.launcher_image_fit,
      );
    } else {
      delete root.dataset.launcherImageUrl;
      button.classList.remove("cpsa-loader__button--image", "cpsa-loader__button--contain");
      button.replaceChildren(launcherFallback());
    }
  }

  async function loadAppearance(root, button) {
    if (!appearanceEndpoint) return;
    const cached = readCachedAppearance();
    if (cached) applyAppearance(root, button, cached.appearance);
    try {
      const response = await window.fetch(appearanceEndpoint, {
        method: "GET",
        credentials: "omit",
        cache: "no-cache",
      });
      if (response.status === 304) return;
      if (!response.ok || !root.isConnected) return;
      const appearance = await response.json();
      cacheAppearance(appearance, response.headers.get("ETag"));
      applyAppearance(root, button, appearance);
    } catch (_error) {
      return;
    }
  }

  function mountLoader() {
    if (!document.body || document.querySelector("[data-cpsa-widget-loader], [data-cpsa-widget-root]")) {
      return;
    }
    const style = document.createElement("style");
    style.textContent = ".cpsa-loader{position:fixed;right:20px;bottom:20px;z-index:2147483000}.cpsa-loader--left{right:auto;left:20px}.cpsa-loader__button{--cpsa-loader-primary:#2563eb;display:grid;place-items:center;width:58px;height:58px;padding:0;border:0;border-radius:50%;overflow:hidden;background:var(--cpsa-loader-primary);color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.22);cursor:pointer}.cpsa-loader__button:focus-visible{outline:3px solid #fff;outline-offset:2px}.cpsa-loader__button:disabled{cursor:wait;opacity:.72}.cpsa-loader__fallback{position:relative;width:25px;height:20px;border:2px solid currentColor;border-radius:7px}.cpsa-loader__fallback:after{position:absolute;right:3px;bottom:-5px;width:8px;height:8px;border-right:2px solid currentColor;border-bottom:2px solid currentColor;transform:skewY(35deg);content:\"\"}.cpsa-loader__button img{display:block;width:100%;height:100%;border-radius:inherit;object-fit:cover}.cpsa-loader__button--contain img{width:calc(100% - 12px);height:calc(100% - 12px);object-fit:contain}@media(max-width:640px){.cpsa-loader--desktop-only{display:none}}";
    document.head.append(style);
    const root = document.createElement("div");
    root.className = `cpsa-loader${scriptElement.dataset.position === "left" ? " cpsa-loader--left" : ""}`;
    root.dataset.cpsaWidgetLoader = "true";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cpsa-loader__button";
    button.append(launcherFallback());
    button.setAttribute("aria-label", scriptElement.dataset.openLabel || "Open customer support");
    button.addEventListener("click", function () {
      loadRuntime(root, button);
    }, { once: true });
    root.append(button);
    document.body.append(root);
    void loadAppearance(root, button);
  }

  function createPresenceController() {
    if (!presenceEndpoint || presenceMode === "disabled") return null;
    const visitorId = ensureVisitorId();
    const tabId = opaqueId("tab");
    let channel = null;
    if (typeof window.BroadcastChannel === "function") {
      try {
        channel = new window.BroadcastChannel(leaseKey);
      } catch (_error) {
        channel = null;
      }
    }
    let pageViewId = opaqueId("page");
    let lastSentPageViewId = "";
    let widgetState = "closed";
    let conversationId = "";
    let consentGranted = !consentRequired || Boolean(
      window.SupportOS && window.SupportOS.presenceConsent === true
    );
    let focusAt = document.hasFocus && document.hasFocus() ? Date.now() : 0;
    let retryDelay = 2000;
    let sendTimer = null;
    let leaseTimer = null;
    let stopped = false;
    let sending = false;
    let leaseStorageUnavailable = false;
    let memoryToken = "";
    let memoryTokenExpiresAt = 0;

    function currentPagePath() {
      return String(`${window.location.pathname || "/"}${window.location.hash || ""}`).slice(0, 500);
    }

    function canReport() {
      return !stopped &&
        consentGranted &&
        (presenceMode === "page_view" || widgetState === "open") &&
        document.visibilityState !== "hidden" &&
        window.navigator.onLine !== false;
    }

    function readLease() {
      try {
        return JSON.parse(storageGet(leaseKey) || "null");
      } catch (_error) {
        return null;
      }
    }

    function claimLease(preferFocused) {
      if (!canReport()) return false;
      const now = Date.now();
      const current = readLease();
      const canTake = !current ||
        current.owner === tabId ||
        Number(current.expiresAt) <= now ||
        Boolean(preferFocused && focusAt && focusAt >= Number(current.focusAt || 0));
      if (!canTake) return false;
      const stored = storageSet(leaseKey, JSON.stringify({
        owner: tabId,
        expiresAt: now + LEASE_TTL_MS,
        focusAt,
      }));
      if (!stored) {
        leaseStorageUnavailable = true;
        return true;
      }
      const claimed = readLease();
      const acquired = Boolean(claimed && claimed.owner === tabId);
      if (acquired && channel) channel.postMessage({ type: "lease", owner: tabId });
      return acquired;
    }

    function releaseLease() {
      const current = readLease();
      if (current && current.owner === tabId) storageRemove(leaseKey);
      if (channel) channel.postMessage({ type: "release", owner: tabId });
    }

    function isLeader() {
      if (leaseStorageUnavailable) return canReport();
      const current = readLease();
      return Boolean(current && current.owner === tabId && Number(current.expiresAt) > Date.now());
    }

    function cachedToken() {
      if (memoryToken && memoryTokenExpiresAt > Date.now() + 30000) return memoryToken;
      try {
        const cached = JSON.parse(storageGet(tokenStorageKey) || "null");
        return cached && cached.token && Number(cached.expiresAt) > Date.now() + 30000
          ? String(cached.token)
          : "";
      } catch (_error) {
        return "";
      }
    }

    function cachedSessionToken() {
      try {
        const cached = JSON.parse(storageGet(sessionStorageKey) || "null");
        const expiresAt = Number(cached && cached.expiresAt || 0);
        return cached && cached.sessionToken && expiresAt > Date.now() + 30000
          ? String(cached.sessionToken)
          : "";
      } catch (_error) {
        return "";
      }
    }

    function cacheToken(payload) {
      const token = String(payload.presence_token || "");
      const expiresAt = new Date(payload.presence_token_expires_at || 0).getTime();
      if (token && expiresAt) {
        memoryToken = token;
        memoryTokenExpiresAt = expiresAt;
        storageSet(tokenStorageKey, JSON.stringify({ token, expiresAt }));
      }
    }

    function schedule(delay) {
      if (sendTimer !== null) window.clearTimeout(sendTimer);
      if (!canReport() || !isLeader()) {
        sendTimer = null;
        return;
      }
      sendTimer = window.setTimeout(function () {
        sendTimer = null;
        void sendPresence();
      }, Math.max(0, delay));
    }

    function heartbeatDelay() {
      return PRESENCE_MIN_INTERVAL_MS + Math.floor(
        Math.random() * (PRESENCE_MAX_INTERVAL_MS - PRESENCE_MIN_INTERVAL_MS + 1)
      );
    }

    async function postPresence(payload) {
      if (publicWidgetId) {
        return window.fetch(presenceEndpoint, {
          method: "POST",
          credentials: "omit",
          headers: { "Content-Type": "text/plain;charset=UTF-8" },
          body: JSON.stringify(payload),
          keepalive: true,
        });
      }
      return window.fetch(presenceEndpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true,
      });
    }

    async function sendPresence() {
      if (sending || !canReport() || !isLeader()) return;
      sending = true;
      const token = publicWidgetId ? cachedToken() : "";
      const sessionToken = publicWidgetId && conversationId ? cachedSessionToken() : "";
      const payload = {
        visitor_id: visitorId,
        conversation_id: publicWidgetId
          ? (sessionToken ? conversationId : null)
          : (conversationId || null),
        event: lastSentPageViewId === pageViewId ? "heartbeat" : "enter",
        page_path: currentPagePath(),
        page_view_id: pageViewId,
        page_title: document.title || null,
        referrer: document.referrer || null,
        language: window.navigator.language || null,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || null,
        widget_state: widgetState,
        presence_source: "page_load",
        runtime_version: runtimeVersion,
        config_version: observedConfigVersion || null,
        connector_type: connectorType,
        connector_version: connectorVersion || null,
      };
      if (publicWidgetId) {
        if (sessionToken) payload.session_token = sessionToken;
        else if (token) payload.presence_token = token;
        else payload.public_widget_id = publicWidgetId;
      }
      try {
        const response = await postPresence(payload);
        if (response.status === 401 || response.status === 403) {
          memoryToken = "";
          memoryTokenExpiresAt = 0;
          storageRemove(tokenStorageKey);
          retryDelay = 2000;
          schedule(retryDelay);
          return;
        }
        if (!response.ok) throw new Error("presence request failed");
        if (publicWidgetId) cacheToken(await response.json());
        lastSentPageViewId = pageViewId;
        retryDelay = 2000;
        schedule(heartbeatDelay());
      } catch (_error) {
        retryDelay = Math.min(PRESENCE_MAX_INTERVAL_MS, retryDelay * 2);
        schedule(retryDelay + Math.floor(Math.random() * 1000));
      } finally {
        sending = false;
      }
    }

    function becomeActive(preferFocused) {
      if (!canReport()) return;
      if (claimLease(preferFocused)) schedule(0);
    }

    function navigationChanged() {
      pageViewId = opaqueId("page");
      if (isLeader()) schedule(0);
    }

    ["pushState", "replaceState"].forEach(function (method) {
      const original = window.history && window.history[method];
      if (typeof original !== "function") return;
      try {
        window.history[method] = function () {
          const result = original.apply(this, arguments);
          navigationChanged();
          return result;
        };
      } catch (_error) {
        return;
      }
    });
    window.addEventListener("popstate", navigationChanged);
    window.addEventListener("hashchange", navigationChanged);
    window.addEventListener("focus", function () {
      focusAt = Date.now();
      becomeActive(true);
    });
    window.addEventListener("online", function () {
      becomeActive(true);
    });
    window.addEventListener("offline", function () {
      if (sendTimer !== null) window.clearTimeout(sendTimer);
      sendTimer = null;
      releaseLease();
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") {
        if (sendTimer !== null) window.clearTimeout(sendTimer);
        sendTimer = null;
        releaseLease();
      } else {
        focusAt = Date.now();
        becomeActive(true);
      }
    });
    window.addEventListener("pagehide", releaseLease);
    window.addEventListener("storage", function (event) {
      if (event.key === leaseKey && canReport() && !isLeader()) becomeActive(false);
    });
    if (channel) {
      channel.addEventListener("message", function () {
        if (canReport() && !isLeader()) becomeActive(false);
      });
    }
    window.addEventListener("cpsa:widget-opened", function (event) {
      widgetState = "open";
      const detail = event && event.detail || {};
      if (detail.conversationId) conversationId = String(detail.conversationId);
      becomeActive(true);
      if (isLeader()) schedule(0);
    });
    window.addEventListener("cpsa:widget-closed", function () {
      widgetState = "closed";
      if (presenceMode === "widget_only") {
        releaseLease();
      } else if (isLeader()) {
        schedule(0);
      }
    });
    window.addEventListener("cpsa:conversation-linked", function (event) {
      const detail = event && event.detail || {};
      if (Object.prototype.hasOwnProperty.call(detail, "conversationId")) {
        conversationId = detail.conversationId ? String(detail.conversationId) : "";
      }
      if (isLeader()) schedule(0);
    });
    window.addEventListener("cpsa:config-updated", function (event) {
      const detail = event && event.detail;
      if (detail && detail.configVersion) {
        observedConfigVersion = String(detail.configVersion).slice(0, 100);
      }
    });

    leaseTimer = window.setInterval(function () {
      if (!canReport()) return;
      if (isLeader()) claimLease(false);
      else becomeActive(false);
    }, LEASE_POLL_MS);
    becomeActive(true);

    return {
      setConsent: function (granted) {
        consentGranted = Boolean(granted);
        if (window.SupportOS) window.SupportOS.presenceConsent = consentGranted;
        if (consentGranted) becomeActive(true);
        else {
          if (sendTimer !== null) window.clearTimeout(sendTimer);
          sendTimer = null;
          releaseLease();
        }
      },
      stop: function () {
        stopped = true;
        if (sendTimer !== null) window.clearTimeout(sendTimer);
        if (leaseTimer !== null) window.clearInterval(leaseTimer);
        releaseLease();
        if (channel) channel.close();
      },
    };
  }

  ensureVisitorId();
  const presenceController = createPresenceController();
  const supportApi = window.SupportOS || {};
  supportApi.setPresenceConsent = function (granted) {
    if (presenceController) presenceController.setConsent(granted);
  };
  window.SupportOS = supportApi;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountLoader, { once: true });
  } else {
    mountLoader();
  }
})();
