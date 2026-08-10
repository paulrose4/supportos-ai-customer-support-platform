(function () {
  "use strict";

  const scriptElement = document.currentScript;
  const publicWidgetId = scriptElement && scriptElement.dataset.siteId;
  const publicApiBase = publicWidgetId
    ? new URL(scriptElement.src, window.location.href).origin
    : "";
  const runtimeSourceUrl = scriptElement
    ? new URL(scriptElement.src, window.location.href)
    : null;
  const runtimeBuildVersion = String(
    scriptElement && (
      scriptElement.dataset.assetVersion
    ) || runtimeSourceUrl && (
      runtimeSourceUrl.searchParams.get("v") || runtimeSourceUrl.searchParams.get("ver")
    ) || scriptElement && scriptElement.dataset.runtimeVersion || "unknown",
  ).slice(0, 100);
  const publicConfig = publicWidgetId
    ? {
        publicWidgetId,
        bootstrapEndpoint: `${publicApiBase}/v1/public-widget/bootstrap`,
        endpoint: `${publicApiBase}/v1/public-widget/chat`,
        messagesEndpoint: `${publicApiBase}/v1/public-widget/messages`,
        conversationStateEndpoint: `${publicApiBase}/v1/public-widget/conversation-state`,
        eventsEndpoint: `${publicApiBase}/v1/public-widget/event-stream`,
        presenceEndpoint: `${publicApiBase}/v1/public-widget/presence`,
        offlineEndpoint: `${publicApiBase}/v1/public-widget/offline-message`,
        satisfactionEndpoint: `${publicApiBase}/v1/public-widget/satisfaction`,
        appearanceEndpoint: `${publicApiBase}/v1/public-widget/appearance?public_widget_id=${encodeURIComponent(publicWidgetId)}`,
        siteId: publicWidgetId,
        title: scriptElement.dataset.title || "",
        primaryColor: scriptElement.dataset.primaryColor || "#2563eb",
        primaryLanguage: scriptElement.dataset.language || "en",
        launcherImageUrl: scriptElement.dataset.launcherImageUrl || "",
        launcherImageFit: scriptElement.dataset.launcherImageFit || "contain",
      }
    : null;
  const rawConfig = publicConfig || window.CPSAWidgetConfig;
  if (!rawConfig || !rawConfig.endpoint) {
    return;
  }

  const CHAT_TIMEOUT_MS = 60000;
  const ACTIVITY_TICK_MS = 1000;
  const HUMAN_MESSAGE_POLL_MIN_MS = 5000;
  const HUMAN_MESSAGE_POLL_MAX_MS = 30000;
  const ACTIVITY_LEASE_TTL_MS = 15000;
  const VISITOR_COOKIE_MAX_AGE_SECONDS = 2592000;
  const SESSION_CACHE_SCHEMA = 2;
  const RETRYABLE_CHAT_STATUSES = new Set([502, 503, 504]);
  const BOT_USER_AGENT_PATTERN = /(?:googlebot|bingbot|yandexbot|baiduspider|bytespider|petalbot|duckduckbot|gptbot|chatgpt-user|claudebot|claude-web|anthropic-ai|ccbot|perplexitybot|amazonbot|cohere-ai|meta-externalagent|slurp|semrushbot|ahrefsbot|mj12bot|dotbot|facebookexternalhit|twitterbot|linkedinbot|applebot|headlesschrome|(?:bot|crawler|spider)(?:[\s/;:_-]|$))/i;

  if (isLikelyAutomatedClient()) {
    return;
  }

  if (publicConfig) {
    if (!document.querySelector("link[data-cpsa-widget-stylesheet]")) {
      const stylesheet = document.createElement("link");
      stylesheet.rel = "stylesheet";
      stylesheet.dataset.cpsaWidgetStylesheet = "true";
      const sourceUrl = new URL(scriptElement.src, window.location.href);
      const assetVersion = sourceUrl.searchParams.get("v") || sourceUrl.searchParams.get("ver");
      stylesheet.href = `${publicApiBase}/widget.css${assetVersion ? `?v=${encodeURIComponent(assetVersion)}` : ""}`;
      document.head.append(stylesheet);
    }
  }

  const namespace = rawConfig.siteId || window.location.host || "default-site";
  const locales = {
    en: {
      title: "Customer Support",
      welcome: "Hello! How can I help you today?",
      labels: { open: "Open customer support", close: "Close customer support", placeholder: "Type your question…", send: "Send", clear: "New conversation", error: "We couldn't connect to support. Please check your connection and try again.", timeout: "The reply is taking longer than expected. Please try again; your conversation has been kept.", citations: "Related pages", source: "Page" },
    },
    zh: {
      title: "在线客服",
      welcome: "您好！今天有什么可以帮您？",
      labels: { open: "打开在线客服", close: "关闭在线客服", placeholder: "请输入您的问题…", send: "发送", clear: "新对话", error: "暂时无法连接客服，请检查网络后重试。", timeout: "回复时间比预期更长，请重试；您的对话记录已保留。", citations: "相关页面", source: "页面" },
    },
    ja: {
      title: "カスタマーサポート",
      welcome: "こんにちは。今日はどのようなご用件でしょうか？",
      labels: { open: "サポートを開く", close: "サポートを閉じる", placeholder: "ご質問を入力してください…", send: "送信", clear: "新しい会話", error: "サポートに接続できませんでした。通信環境をご確認のうえ、もう一度お試しください。", timeout: "回答に通常より時間がかかっています。会話は保存されていますので、もう一度お試しください。", citations: "関連ページ", source: "ページ" },
    },
    ko: {
      title: "고객 지원",
      welcome: "안녕하세요. 무엇을 도와드릴까요?",
      labels: { open: "고객 지원 열기", close: "고객 지원 닫기", placeholder: "질문을 입력하세요…", send: "보내기", clear: "새 대화", error: "고객 지원에 연결할 수 없습니다. 네트워크를 확인한 후 다시 시도해 주세요.", timeout: "답변이 예상보다 오래 걸리고 있습니다. 대화는 저장되었으니 다시 시도해 주세요.", citations: "출처", source: "출처" },
    },
    es: {
      title: "Atención al cliente",
      welcome: "¡Hola! ¿En qué puedo ayudarle hoy?",
      labels: { open: "Abrir atención al cliente", close: "Cerrar atención al cliente", placeholder: "Escriba su pregunta…", send: "Enviar", clear: "Nueva conversación", error: "No pudimos conectar con atención al cliente. Compruebe su conexión e inténtelo de nuevo.", timeout: "La respuesta está tardando más de lo esperado. Inténtelo de nuevo; la conversación se ha conservado.", citations: "Fuentes", source: "Fuente" },
    },
    fr: {
      title: "Service client",
      welcome: "Bonjour ! Comment puis-je vous aider aujourd'hui ?",
      labels: { open: "Ouvrir le service client", close: "Fermer le service client", placeholder: "Saisissez votre question…", send: "Envoyer", clear: "Nouvelle conversation", error: "Impossible de joindre le service client. Vérifiez votre connexion et réessayez.", timeout: "La réponse prend plus de temps que prévu. Réessayez ; votre conversation a été conservée.", citations: "Sources", source: "Source" },
    },
    de: {
      title: "Kundenservice",
      welcome: "Hallo! Wie kann ich Ihnen heute helfen?",
      labels: { open: "Kundenservice öffnen", close: "Kundenservice schließen", placeholder: "Geben Sie Ihre Frage ein…", send: "Senden", clear: "Neues Gespräch", error: "Die Verbindung zum Kundenservice konnte nicht hergestellt werden. Prüfen Sie Ihre Verbindung und versuchen Sie es erneut.", timeout: "Die Antwort dauert länger als erwartet. Versuchen Sie es erneut; der Gesprächsverlauf bleibt erhalten.", citations: "Passende Seiten", source: "Seite" },
    },
    pt: {
      title: "Atendimento ao cliente",
      welcome: "Olá! Como posso ajudar hoje?",
      labels: { open: "Abrir atendimento", close: "Fechar atendimento", placeholder: "Digite sua pergunta…", send: "Enviar", clear: "Nova conversa", error: "Não foi possível conectar ao atendimento. Verifique sua conexão e tente novamente.", timeout: "A resposta está demorando mais do que o esperado. Tente novamente; a conversa foi mantida.", citations: "Fontes", source: "Fonte" },
    },
  };
  const customLabels = rawConfig.labels || {};
  const initialLanguage = normalizeLanguage(rawConfig.primaryLanguage || rawConfig.language || "en");
  const initialLocale = localeFor(initialLanguage);
  const config = Object.assign(
    {
      storageKey: `cpsa_conversation_${namespace}`,
      visitorStorageKey: `cpsa_visitor_${namespace}`,
      visitorCookieName: `cpsa_visitor_${String(namespace).replace(/[^a-z0-9_-]/gi, "_").slice(0, 64)}`,
      sessionStorageKey: `cpsa_session_${namespace}`,
      appearanceStorageKey: `cpsa_appearance_${namespace}`,
      activityLeaseKey: `cpsa_activity_${namespace}`,
      primaryLanguage: initialLanguage,
      title: initialLocale.title,
      welcomeMessage: initialLocale.welcome,
      onlineMessage: "",
      offlineMessage: initialLocale.welcome,
      mobileEnabled: true,
      offlineFormEnabled: true,
      csatEnabled: true,
      isOnline: true,
      labels: initialLocale.labels,
    },
    rawConfig,
    { labels: Object.assign({}, initialLocale.labels, customLabels) },
  );
  let sessionToken = "";
  let sessionExpiresAt = 0;
  let resumeToken = "";
  let resumeExpiresAt = 0;
  let bootstrapPromise = null;
  let appearancePromise = null;
  let appearanceEtag = "";
  let appearanceVersion = "";
  let appearanceValidated = false;
  let onConfigUpdated = function () {};
  let activeConversationId = storageGet(config.storageKey) || "";
  let conversationRevision = 0;
  const activityTabId = opaqueId("tab");
  const activityChannel = createActivityChannel();
  const pageStartedAt = Date.now();

  function isLikelyAutomatedClient() {
    const userAgent = String(window.navigator && window.navigator.userAgent || "");
    return Boolean(window.navigator && window.navigator.webdriver) || BOT_USER_AGENT_PATTERN.test(userAgent);
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

  function normalizeLanguage(value) {
    return String(value || "en").trim().replace(/_/g, "-").toLowerCase() || "en";
  }

  function localeFor(language) {
    return locales[normalizeLanguage(language).split("-", 1)[0]] || locales.en;
  }

  function applyLanguage(language) {
    const normalized = normalizeLanguage(language);
    const locale = localeFor(normalized);
    config.primaryLanguage = normalized;
    config.labels = Object.assign({}, locale.labels, customLabels);
    if (!rawConfig.title) config.title = locale.title;
    if (!rawConfig.welcomeMessage) config.welcomeMessage = locale.welcome;
  }

  function applyPublishedConfig(published, isOnline, version) {
    if (!published || typeof published !== "object") return;
    if (published.default_language) applyLanguage(published.default_language);
    config.welcomeMessage = String(published.welcome_message || config.welcomeMessage);
    config.onlineMessage = String(published.online_message || "");
    config.offlineMessage = String(published.offline_message || config.welcomeMessage);
    config.primaryColor = String(published.primary_color || config.primaryColor);
    config.position = published.position === "left" ? "left" : "right";
    config.title = String(published.agent_name || config.title);
    config.agentAvatarUrl = resolveImageUrl(published.agent_avatar_url);
    config.launcherImageUrl = resolveImageUrl(published.launcher_image_url);
    config.launcherImageFit = published.launcher_image_fit === "cover" ? "cover" : "contain";
    config.mobileEnabled = published.mobile_enabled !== false;
    config.offlineFormEnabled = published.offline_form_enabled !== false;
    config.csatEnabled = published.csat_enabled !== false;
    config.isOnline = isOnline !== false;
    if (version) appearanceVersion = String(version);
    onConfigUpdated();
    window.dispatchEvent(new CustomEvent("cpsa:config-updated", {
      detail: { configVersion: appearanceVersion, runtimeVersion: runtimeBuildVersion },
    }));
  }

  applyLanguage(initialLanguage);

  function resolveImageUrl(value) {
    if (!value) return "";
    try {
      const resolved = new URL(String(value), publicApiBase || window.location.href);
      return /^https?:$/.test(resolved.protocol) ? resolved.href : "";
    } catch (_error) {
      return "";
    }
  }

  function appearanceDocumentFromConfig(published, isOnline, version) {
    return Object.assign(
      {
        schema_version: 3,
        version: String(version || ""),
        config_version: String(version || ""),
        is_online: isOnline !== false,
      },
      published || {},
    );
  }

  function persistAppearance(appearance, etag) {
    if (!config.publicWidgetId || !appearance || typeof appearance !== "object") return;
    storageSet(config.appearanceStorageKey, JSON.stringify({
      schema: 3,
      etag: String(etag || ""),
      appearance,
      checkedAt: Date.now(),
    }));
  }

  function restoreCachedAppearance() {
    if (!config.publicWidgetId) return false;
    try {
      const cached = JSON.parse(storageGet(config.appearanceStorageKey) || "null");
      if (!cached || cached.schema !== 3 || !cached.appearance) return false;
      appearanceEtag = typeof cached.etag === "string" ? cached.etag : "";
      const appearance = cached.appearance;
      applyPublishedConfig(
        appearance,
        appearance.is_online,
        appearance.config_version || appearance.version,
      );
      return true;
    } catch (_error) {
      storageRemove(config.appearanceStorageKey);
      return false;
    }
  }

  async function refreshAppearance(force) {
    if (!config.publicWidgetId || !config.appearanceEndpoint) return;
    if (!force && appearancePromise) return appearancePromise;
    appearancePromise = (async function () {
      try {
        const response = await window.fetch(config.appearanceEndpoint, {
          method: "GET",
          credentials: "omit",
          cache: "no-cache",
        });
        if (response.status === 304) {
          appearanceValidated = true;
          return;
        }
        if (!response.ok) return;
        const appearance = await response.json();
        appearanceEtag = String(response.headers.get("ETag") || "");
        appearanceValidated = true;
        applyPublishedConfig(
          appearance,
          appearance.is_online,
          appearance.config_version || appearance.version,
        );
        persistAppearance(appearance, appearanceEtag);
      } catch (_error) {
        return;
      }
    })();
    try {
      await appearancePromise;
    } finally {
      appearancePromise = null;
    }
  }

  function clearCachedSession(clearResume) {
    sessionToken = "";
    sessionExpiresAt = 0;
    if (clearResume) {
      resumeToken = "";
      resumeExpiresAt = 0;
    }
    persistSessionState();
  }

  function restoreCachedSession() {
    if (!config.publicWidgetId) return false;
    try {
      const cached = JSON.parse(storageGet(config.sessionStorageKey) || "null");
      if (!cached || cached.schema !== SESSION_CACHE_SCHEMA) {
        clearCachedSession(true);
        return false;
      }
      const expiresAt = Number(cached.expiresAt);
      const cachedResumeExpiresAt = Number(cached.resumeExpiresAt);
      if (
        cached.publicWidgetId !== config.publicWidgetId ||
        (cached.resumeToken && !Number.isFinite(cachedResumeExpiresAt))
      ) {
        clearCachedSession(true);
        return false;
      }
      resumeToken = typeof cached.resumeToken === "string" ? cached.resumeToken : "";
      resumeExpiresAt = cachedResumeExpiresAt || 0;
      if (resumeExpiresAt && Date.now() >= resumeExpiresAt) {
        resumeToken = "";
        resumeExpiresAt = 0;
      }
      if (
        typeof cached.sessionToken === "string" &&
        cached.sessionToken &&
        Number.isFinite(expiresAt) &&
        Date.now() + 30000 < expiresAt
      ) {
        sessionToken = cached.sessionToken;
        sessionExpiresAt = expiresAt;
        return true;
      }
      sessionToken = "";
      sessionExpiresAt = 0;
      persistSessionState();
      return false;
    } catch (_error) {
      clearCachedSession(true);
      return false;
    }
  }

  function persistSessionState(payload) {
    if (!sessionToken && !resumeToken) {
      storageRemove(config.sessionStorageKey);
      return;
    }
    storageSet(config.sessionStorageKey, JSON.stringify({
      schema: SESSION_CACHE_SCHEMA,
      publicWidgetId: config.publicWidgetId,
      sessionToken,
      expiresAt: sessionExpiresAt,
      resumeToken,
      resumeExpiresAt,
    }));
  }

  function cacheSession(payload) {
    if (payload.resume_token) resumeToken = String(payload.resume_token);
    if (payload.resume_expires_at) {
      resumeExpiresAt = new Date(payload.resume_expires_at).getTime();
    }
    persistSessionState(payload);
  }

  restoreCachedSession();
  restoreCachedAppearance();

  function createElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (typeof text === "string") {
      element.textContent = text;
    }
    return element;
  }

  function appendFormattedText(container, text) {
    String(text || "").split("\n").forEach(function (line, lineIndex) {
      if (lineIndex > 0) container.append(document.createElement("br"));
      line.split(/(\*\*[^*]+\*\*)/g).forEach(function (part) {
        if (!part) return;
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
          container.append(createElement("strong", "", part.slice(2, -2)));
        } else {
          container.append(document.createTextNode(part));
        }
      });
    });
  }

  function opaqueId(prefix) {
    return window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function conversationId() {
    if (!activeConversationId) {
      activeConversationId = opaqueId("cpsa");
      storageSet(config.storageKey, activeConversationId);
    }
    return activeConversationId;
  }

  function resetConversation() {
    conversationRevision += 1;
    activeConversationId = opaqueId("cpsa");
    storageSet(config.storageKey, activeConversationId);
    return activeConversationId;
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
    document.cookie = `${encodeURIComponent(config.visitorCookieName)}=${encodeURIComponent(value)}; Max-Age=${VISITOR_COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=Lax${secure}`;
  }

  function visitorId() {
    let value = readCookie(config.visitorCookieName);
    if (!value) value = storageGet(config.visitorStorageKey) || "";
    if (!value) {
      value = opaqueId("visitor");
    }
    writeVisitorCookie(value);
    storageSet(config.visitorStorageKey, value);
    return value;
  }

  visitorId();

  function createActivityChannel() {
    if (typeof window.BroadcastChannel !== "function") return null;
    try {
      return new window.BroadcastChannel(config.activityLeaseKey);
    } catch (_error) {
      return null;
    }
  }

  function broadcastActivityLease(type) {
    if (!activityChannel) return;
    activityChannel.postMessage({ type, owner: activityTabId });
  }

  function activityLeaseOwnedByAnotherTab() {
    try {
      const current = JSON.parse(storageGet(config.activityLeaseKey) || "null");
      return Boolean(
        current &&
        current.owner !== activityTabId &&
        Number(current.expiresAt) > Date.now()
      );
    } catch (_error) {
      return false;
    }
  }

  function claimActivityLease() {
    const now = Date.now();
    try {
      const current = JSON.parse(storageGet(config.activityLeaseKey) || "null");
      if (current && current.owner !== activityTabId && Number(current.expiresAt) > now) {
        return false;
      }
      storageSet(config.activityLeaseKey, JSON.stringify({
        owner: activityTabId,
        expiresAt: now + ACTIVITY_LEASE_TTL_MS,
      }));
      const claimed = JSON.parse(storageGet(config.activityLeaseKey) || "null");
      const acquired = Boolean(claimed && claimed.owner === activityTabId);
      if (acquired) broadcastActivityLease("lease");
      return acquired;
    } catch (_error) {
      return true;
    }
  }

  function releaseActivityLease() {
    try {
      const current = JSON.parse(storageGet(config.activityLeaseKey) || "null");
      if (current && current.owner === activityTabId) storageRemove(config.activityLeaseKey);
    } catch (_error) {
      storageRemove(config.activityLeaseKey);
    }
    broadcastActivityLease("release");
  }

  async function publicPost(endpoint, payload, options) {
    return window.fetch(endpoint, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "text/plain;charset=UTF-8" },
      body: JSON.stringify(payload),
      signal: options && options.signal,
      keepalive: Boolean(options && options.keepalive),
    });
  }

  async function ensureSession(force) {
    if (!config.publicWidgetId) {
      return "";
    }
    if (!force && sessionToken && Date.now() + 30000 < sessionExpiresAt) {
      return sessionToken;
    }
    if (force) clearCachedSession(false);
    if (!force && bootstrapPromise) {
      return bootstrapPromise;
    }
    bootstrapPromise = (async function () {
      const requestPayload = {
        public_widget_id: config.publicWidgetId,
      };
      if (resumeToken && Date.now() < resumeExpiresAt) {
        requestPayload.resume_token = resumeToken;
      }
      let response = await publicPost(config.bootstrapEndpoint, requestPayload);
      if (response.status === 403 && requestPayload.resume_token) {
        clearCachedSession(true);
        response = await publicPost(config.bootstrapEndpoint, {
          public_widget_id: config.publicWidgetId,
        });
      }
      if (!response.ok) {
        throw new Error("widget bootstrap failed");
      }
      const payload = await response.json();
      applyLanguage(payload.primary_language || config.primaryLanguage);
      const payloadVersion = String(payload.widget_config_version || "");
      if (!appearanceValidated || !appearanceVersion || appearanceVersion === payloadVersion) {
        applyPublishedConfig(payload.widget_config, payload.is_online, payloadVersion);
        persistAppearance(
          appearanceDocumentFromConfig(payload.widget_config, payload.is_online, payloadVersion),
          "",
        );
      }
      sessionToken = String(payload.session_token || "");
      sessionExpiresAt = new Date(payload.expires_at || 0).getTime();
      if (!sessionToken || !sessionExpiresAt) {
        throw new Error("widget bootstrap response is invalid");
      }
      cacheSession(payload);
      onConfigUpdated();
      return sessionToken;
    })();
    try {
      return await bootstrapPromise;
    } finally {
      bootstrapPromise = null;
    }
  }

  async function chatRequest(sendRequest) {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const controller = new AbortController();
      const timeout = window.setTimeout(function () {
        controller.abort();
      }, CHAT_TIMEOUT_MS);
      try {
        const response = await sendRequest(controller.signal);
        if (attempt === 0 && RETRYABLE_CHAT_STATUSES.has(response.status)) {
          continue;
        }
        return response;
      } catch (error) {
        if (error && error.name === "AbortError") {
          const timeoutError = new Error("support reply timed out");
          timeoutError.code = "reply_timeout";
          throw timeoutError;
        }
        if (attempt === 1) {
          throw error;
        }
      } finally {
        window.clearTimeout(timeout);
      }
    }
    throw new Error("support request failed");
  }

  async function sendChat(message, requestConversationId) {
    const payload = {
      conversation_id: requestConversationId,
      message,
      page_path: String(window.location.pathname || "/"),
      dwell_seconds: Math.min(86400, Math.floor((Date.now() - pageStartedAt) / 1000)),
    };
    if (!config.publicWidgetId) {
      return chatRequest(function (signal) {
        return window.fetch(config.endpoint, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal,
        });
      });
    }
    payload.request_id = opaqueId("request");
    payload.session_token = await ensureSession(false);
    let response = await chatRequest(function (signal) {
      return publicPost(config.endpoint, payload, { signal });
    });
    if (response.status === 401) {
      payload.session_token = await ensureSession(true);
      response = await chatRequest(function (signal) {
        return publicPost(config.endpoint, payload, { signal });
      });
    }
    return response;
  }

  async function sendOfflineMessage(email, message, requestConversationId) {
    return publicPost(config.offlineEndpoint, {
      session_token: await ensureSession(false),
      conversation_id: requestConversationId,
      email,
      message,
      page_path: window.location.pathname || "/",
      request_id: opaqueId("offline"),
    });
  }

  async function submitSatisfaction(score) {
    return publicPost(config.satisfactionEndpoint, {
      session_token: await ensureSession(false),
      conversation_id: conversationId(),
      score,
      request_id: opaqueId("csat"),
    });
  }

  function mount() {
    if (document.querySelector("[data-cpsa-widget-root]")) {
      return;
    }
    const root = createElement("section", `cpsa-widget cpsa-widget--${config.position || "right"}`);
    if (!config.mobileEnabled) root.classList.add("cpsa-widget--desktop-only");
    root.dataset.cpsaWidgetRoot = "true";
    root.style.setProperty("--cpsa-primary", config.primaryColor || "#2563eb");
    root.setAttribute("aria-label", config.title || "Product Support");

    const launcher = createElement("button", "cpsa-widget__launcher");
    launcher.type = "button";
    launcher.setAttribute("aria-label", config.labels.open);
    launcher.setAttribute("aria-expanded", "false");
    const launcherImage = createElement("img", "cpsa-widget__launcher-image");
    launcherImage.alt = "";
    launcherImage.decoding = "async";
    const launcherFallback = createElement("span", "cpsa-widget__launcher-fallback");
    launcherFallback.setAttribute("aria-hidden", "true");
    const launcherClose = createElement("span", "cpsa-widget__launcher-close", "×");
    launcherClose.setAttribute("aria-hidden", "true");
    launcher.append(launcherImage, launcherFallback, launcherClose);

    const panel = createElement("div", "cpsa-widget__panel");
    panel.hidden = true;

    const header = createElement("header", "cpsa-widget__header");
    const avatar = createElement("img", "cpsa-widget__avatar");
    avatar.alt = "";
    avatar.hidden = !config.agentAvatarUrl;
    if (config.agentAvatarUrl) avatar.src = config.agentAvatarUrl;
    header.append(avatar);
    const titleWrap = createElement("div", "cpsa-widget__identity");
    const title = createElement("strong", "cpsa-widget__title", config.title || "Product Support");
    const availability = createElement(
      "small",
      "cpsa-widget__availability",
      config.isOnline === false ? config.offlineMessage : config.onlineMessage,
    );
    titleWrap.append(title, availability);
    const headerActions = createElement("div", "cpsa-widget__header-actions");
    const clearButton = createElement("button", "cpsa-widget__clear", config.labels.clear);
    clearButton.type = "button";
    const closeButton = createElement("button", "cpsa-widget__close", "×");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", config.labels.close);
    headerActions.append(clearButton, closeButton);
    header.append(titleWrap, headerActions);

    const messages = createElement("div", "cpsa-widget__messages");
    messages.setAttribute("role", "log");
    messages.setAttribute("aria-live", "polite");

    const form = createElement("form", "cpsa-widget__form");
    const emailInput = createElement("input", "cpsa-widget__email");
    emailInput.type = "email";
    emailInput.maxLength = 254;
    emailInput.placeholder = config.primaryLanguage.startsWith("zh") ? "联系邮箱" : "Contact email";
    emailInput.hidden = config.isOnline !== false || !config.offlineFormEnabled;
    emailInput.required = config.isOnline === false && config.offlineFormEnabled;
    const input = createElement("textarea", "cpsa-widget__input");
    input.rows = 2;
    input.maxLength = 10000;
    input.placeholder = config.labels.placeholder;
    input.setAttribute("aria-label", config.labels.placeholder);
    const sendButton = createElement("button", "cpsa-widget__send", config.labels.send);
    sendButton.type = "submit";
    if (config.isOnline === false) {
      input.placeholder = config.offlineMessage;
      input.setAttribute("aria-label", config.offlineMessage);
    }
    form.append(emailInput, input, sendButton);

    const satisfaction = createElement("div", "cpsa-widget__satisfaction");
    satisfaction.hidden = true;
    satisfaction.append(createElement("strong", "", config.primaryLanguage.startsWith("zh") ? "本次服务体验如何？" : "How was this support experience?"));
    const satisfactionButtons = createElement("div", "cpsa-widget__satisfaction-buttons");
    [1, 2, 3, 4, 5].forEach(function (score) {
      const button = createElement("button", "", String(score));
      button.type = "button";
      button.title = `${score}/5`;
      button.addEventListener("click", async function () {
        button.disabled = true;
        try {
          const response = await submitSatisfaction(score);
          if (!response.ok) throw new Error("rating failed");
          storageSet(`${config.storageKey}_rated`, conversationId());
          satisfaction.replaceChildren(createElement("span", "", config.primaryLanguage.startsWith("zh") ? "感谢您的评价" : "Thank you for your feedback"));
        } catch (_error) {
          button.disabled = false;
        }
      });
      satisfactionButtons.append(button);
    });
    satisfaction.append(satisfactionButtons);

    panel.append(header, messages, satisfaction, form);
    root.append(panel, launcher);
    document.body.append(root);

    const seenHumanMessageIds = new Set();
    let humanMessagePollInFlight = false;
    let activityIntervalId = null;
    let handoffActive = false;
    let humanMessageCursor = "";
    let humanMessagePollDelay = HUMAN_MESSAGE_POLL_MIN_MS;
    let nextHumanMessagePollAt = 0;
    let eventStreamAbortController = null;
    let eventStreamConnected = false;
    let eventStreamReconnectTimer = null;
    let eventStreamRetryDelay = 5000;
    let eventStreamUnavailableUntil = 0;
    let welcomeShown = false;
    let opening = false;
    let launcherImageFailedUrl = "";
    let avatarFailedUrl = "";

    launcherImage.addEventListener("error", function () {
      launcherImageFailedUrl = launcherImage.src;
      launcherImage.hidden = true;
      launcher.classList.remove("cpsa-widget__launcher--image");
    });

    launcherImage.addEventListener("load", function () {
      if (launcherImage.src !== launcherImageFailedUrl) {
        launcherImage.hidden = false;
        launcher.classList.add("cpsa-widget__launcher--image");
        launcher.classList.toggle(
          "cpsa-widget__launcher--contain",
          config.launcherImageFit !== "cover",
        );
      }
    });

    avatar.addEventListener("error", function () {
      avatarFailedUrl = avatar.src;
      avatar.hidden = true;
    });

    avatar.addEventListener("load", function () {
      if (avatar.src !== avatarFailedUrl) avatar.hidden = false;
    });

    function syncConfigUi() {
      root.dataset.configVersion = appearanceVersion;
      root.classList.remove("cpsa-widget--left", "cpsa-widget--right");
      root.classList.add(`cpsa-widget--${config.position || "right"}`);
      root.classList.toggle("cpsa-widget--desktop-only", !config.mobileEnabled);
      root.style.setProperty("--cpsa-primary", config.primaryColor || "#2563eb");
      root.setAttribute("aria-label", config.title || "Product Support");
      root.dataset.configVersion = appearanceVersion;
      root.dataset.runtimeVersion = String(
        runtimeBuildVersion,
      );
      root.dataset.connectorType = String(
        scriptElement && scriptElement.dataset.connectorType || (publicConfig ? "public" : "legacy"),
      );
      title.textContent = config.title || "Product Support";
      availability.textContent = config.isOnline === false
        ? config.offlineMessage
        : config.onlineMessage;
      avatar.hidden = !config.agentAvatarUrl || config.agentAvatarUrl === avatarFailedUrl;
      if (
        config.agentAvatarUrl &&
        config.agentAvatarUrl !== avatarFailedUrl &&
        avatar.src !== config.agentAvatarUrl
      ) {
        avatar.src = config.agentAvatarUrl;
      }
      const launcherUrl = resolveImageUrl(config.launcherImageUrl);
      const imageAvailable = Boolean(launcherUrl && launcherUrl !== launcherImageFailedUrl);
      if (imageAvailable && launcherImage.src !== launcherUrl) launcherImage.src = launcherUrl;
      const imageLoaded = imageAvailable &&
        launcherImage.src === launcherUrl &&
        launcherImage.complete &&
        launcherImage.naturalWidth > 0;
      launcherImage.hidden = !imageLoaded;
      launcherImage.style.objectFit = config.launcherImageFit === "cover" ? "cover" : "contain";
      launcher.classList.toggle("cpsa-widget__launcher--image", imageLoaded);
      launcher.classList.toggle(
        "cpsa-widget__launcher--contain",
        imageLoaded && config.launcherImageFit !== "cover",
      );
      launcher.classList.toggle("cpsa-widget__launcher--open", !panel.hidden);
      launcher.setAttribute("aria-label", panel.hidden ? config.labels.open : config.labels.close);
      input.placeholder = config.isOnline === false ? config.offlineMessage : config.labels.placeholder;
      input.setAttribute("aria-label", input.placeholder);
      emailInput.hidden = config.isOnline !== false || !config.offlineFormEnabled;
      emailInput.required = config.isOnline === false && config.offlineFormEnabled;
      clearButton.textContent = config.labels.clear;
      sendButton.textContent = config.labels.send;
    }

    onConfigUpdated = syncConfigUi;
    syncConfigUi();

    function showSatisfaction() {
      if (!config.csatEnabled || storageGet(`${config.storageKey}_rated`) === conversationId()) return;
      satisfaction.hidden = false;
    }

    function humanCursorStorageKey(value) {
      return `${config.storageKey}_human_cursor_${value}`;
    }

    function setHandoffActive(active) {
      handoffActive = Boolean(active);
      if (handoffActive) {
        humanMessageCursor = storageGet(humanCursorStorageKey(conversationId())) || "";
        nextHumanMessagePollAt = 0;
        humanMessagePollDelay = HUMAN_MESSAGE_POLL_MIN_MS;
        if (!panel.hidden) startEventStream();
      } else {
        nextHumanMessagePollAt = 0;
        stopEventStream();
      }
    }

    async function restoreConversationState() {
      if (!config.publicWidgetId || !config.conversationStateEndpoint || !activeConversationId) {
        return;
      }
      try {
        const payload = {
          session_token: await ensureSession(false),
          conversation_id: activeConversationId,
        };
        let response = await publicPost(config.conversationStateEndpoint, payload);
        if (response.status === 401) {
          clearCachedSession(false);
          payload.session_token = await ensureSession(true);
          response = await publicPost(config.conversationStateEndpoint, payload);
        }
        if (!response.ok) return;
        const state = await response.json();
        setHandoffActive(state.exists && state.handoff_active);
        if (state.conversation_status === "resolved") showSatisfaction();
      } catch (_error) {
        return;
      }
    }

    function appendMessage(role, text, relatedLinks) {
      const item = createElement("article", `cpsa-widget__message cpsa-widget__message--${role}`);
      const messageContent = createElement("div", "cpsa-widget__message-content");
      appendFormattedText(messageContent, text);
      item.append(messageContent);
      if (Array.isArray(relatedLinks) && relatedLinks.length > 0) {
        const sources = Array.from(
          new Set(
            relatedLinks.map(function (relatedLink) {
              return String(relatedLink);
            }).filter(function (source) {
              return /^https?:\/\//i.test(source);
            }),
          ),
        ).slice(0, 3);
        if (sources.length > 0) {
          const relatedPages = createElement("div", "cpsa-widget__citations");
          relatedPages.append(createElement("strong", "cpsa-widget__citations-title", config.labels.citations));
          const list = createElement("ul");
          sources.forEach(function (source, index) {
            const listItem = createElement("li");
            const sourceUrl = new URL(source, window.location.href);
            const pageName = decodeURIComponent(sourceUrl.pathname.split("/").filter(Boolean).pop() || "")
              .replace(/\.html?$/i, "")
              .replace(/[-_]+/g, " ");
            const link = createElement("a", "", pageName || `${config.labels.source} ${index + 1}`);
            link.href = sourceUrl.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            listItem.append(link);
            list.append(listItem);
          });
          relatedPages.append(list);
          item.append(relatedPages);
        }
      }
      messages.append(item);
      messages.scrollTop = messages.scrollHeight;
    }

    async function pollHumanMessages() {
      if (
        !config.publicWidgetId ||
        !config.messagesEndpoint ||
        panel.hidden ||
        !handoffActive ||
        Date.now() < nextHumanMessagePollAt ||
        humanMessagePollInFlight
      ) {
        return;
      }
      humanMessagePollInFlight = true;
      const requestConversationId = conversationId();
      const requestRevision = conversationRevision;
      try {
        let payload = {
          session_token: await ensureSession(false),
          conversation_id: requestConversationId,
          limit: 20,
        };
        if (humanMessageCursor) payload.after_cursor = humanMessageCursor;
        let response = await publicPost(config.messagesEndpoint, payload);
        if (response.status === 401) {
          clearCachedSession(false);
          payload.session_token = await ensureSession(true);
          response = await publicPost(config.messagesEndpoint, payload);
        }
        if (!response.ok) {
          humanMessagePollDelay = Math.min(
            HUMAN_MESSAGE_POLL_MAX_MS,
            humanMessagePollDelay * 2,
          );
          nextHumanMessagePollAt = Date.now() + humanMessagePollDelay;
          return;
        }
        const result = await response.json();
        if (
          requestRevision !== conversationRevision ||
          requestConversationId !== conversationId()
        ) {
          return;
        }
        (Array.isArray(result.items) ? result.items : []).forEach(function (item) {
          const messageId = String(item.message_id || "");
          if (!messageId || seenHumanMessageIds.has(messageId)) return;
          seenHumanMessageIds.add(messageId);
          appendMessage("agent", String(item.content || ""));
        });
        if (result.next_cursor) {
          humanMessageCursor = String(result.next_cursor);
          storageSet(humanCursorStorageKey(requestConversationId), humanMessageCursor);
        }
        humanMessagePollDelay = HUMAN_MESSAGE_POLL_MIN_MS;
        nextHumanMessagePollAt = Date.now() + humanMessagePollDelay;
        if (result.conversation_status === "resolved" || !result.handoff_active) {
          setHandoffActive(false);
          if (result.conversation_status === "resolved") showSatisfaction();
        }
      } catch (_error) {
        humanMessagePollDelay = Math.min(
          HUMAN_MESSAGE_POLL_MAX_MS,
          humanMessagePollDelay * 2,
        );
        nextHumanMessagePollAt = Date.now() + humanMessagePollDelay;
        return;
      } finally {
        humanMessagePollInFlight = false;
      }
    }

    function stopEventStream() {
      eventStreamConnected = false;
      if (eventStreamAbortController) {
        eventStreamAbortController.abort();
        eventStreamAbortController = null;
      }
      if (eventStreamReconnectTimer !== null) {
        window.clearTimeout(eventStreamReconnectTimer);
        eventStreamReconnectTimer = null;
      }
    }

    function handleActivityLeaseChange(event) {
      const message = event && event.data;
      if (message && message.owner === activityTabId) return;
      if (activityLeaseOwnedByAnotherTab()) {
        stopEventStream();
      } else if (!panel.hidden && document.visibilityState !== "hidden") {
        runActivityCycle();
      }
    }

    if (activityChannel) {
      activityChannel.addEventListener("message", handleActivityLeaseChange);
    }
    window.addEventListener("storage", function (event) {
      if (event.key === config.activityLeaseKey) handleActivityLeaseChange(event);
    });

    function scheduleEventStreamReconnect() {
      if (!handoffActive || panel.hidden || document.visibilityState === "hidden") return;
      if (eventStreamReconnectTimer !== null) return;
      eventStreamReconnectTimer = window.setTimeout(function () {
        eventStreamReconnectTimer = null;
        startEventStream();
      }, eventStreamRetryDelay);
      eventStreamRetryDelay = Math.min(HUMAN_MESSAGE_POLL_MAX_MS, eventStreamRetryDelay * 2);
    }

    function handleEventStreamBlock(block) {
      const eventLine = block.split("\n").find(function (line) {
        return line.startsWith("event:");
      });
      const eventType = eventLine ? eventLine.slice(6).trim() : "";
      if (eventType === "message-available") {
        nextHumanMessagePollAt = 0;
        void pollHumanMessages();
      }
    }

    async function startEventStream() {
      if (
        !handoffActive ||
        !config.publicWidgetId ||
        !config.eventsEndpoint ||
        panel.hidden ||
        document.visibilityState === "hidden" ||
        window.navigator.onLine === false ||
        Date.now() < eventStreamUnavailableUntil ||
        eventStreamAbortController ||
        !claimActivityLease()
      ) return;
      eventStreamAbortController = new AbortController();
      const controller = eventStreamAbortController;
      try {
        nextHumanMessagePollAt = 0;
        await pollHumanMessages();
        const response = await publicPost(config.eventsEndpoint, {
          session_token: await ensureSession(false),
          conversation_id: conversationId(),
        }, { signal: controller.signal });
        if (response.status === 401) {
          clearCachedSession(false);
        }
        if (!response.ok || !response.body) {
          if ([404, 409, 501, 503].includes(response.status)) {
            eventStreamUnavailableUntil = Date.now() + 60000;
          }
          return;
        }
        eventStreamConnected = true;
        eventStreamRetryDelay = 5000;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            handleEventStreamBlock(buffer.slice(0, boundary));
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch (error) {
        if (!error || error.name !== "AbortError") {
          eventStreamConnected = false;
        }
      } finally {
        if (eventStreamAbortController === controller) {
          eventStreamAbortController = null;
          eventStreamConnected = false;
          scheduleEventStreamReconnect();
        }
      }
    }

    function runActivityCycle() {
      if (
        panel.hidden ||
        document.visibilityState === "hidden" ||
        window.navigator.onLine === false
      ) return;
      if (!claimActivityLease()) {
        stopEventStream();
        return;
      }
      if (handoffActive) {
        if (!eventStreamAbortController) startEventStream();
        if (!eventStreamConnected) void pollHumanMessages();
      }
    }

    function startActivity() {
      if (panel.hidden || document.visibilityState === "hidden") return;
      runActivityCycle();
      if (activityIntervalId === null) {
        activityIntervalId = window.setInterval(runActivityCycle, ACTIVITY_TICK_MS);
      }
    }

    function stopActivity() {
      if (activityIntervalId !== null) {
        window.clearInterval(activityIntervalId);
        activityIntervalId = null;
      }
      stopEventStream();
      releaseActivityLease();
    }

    function closeWidget() {
      panel.hidden = true;
      launcher.classList.remove("cpsa-widget__launcher--open");
      launcher.setAttribute("aria-expanded", "false");
      launcher.setAttribute("aria-label", config.labels.open);
      stopActivity();
      window.dispatchEvent(new CustomEvent("cpsa:widget-closed"));
    }

    async function openWidget() {
      if (opening || !panel.hidden) return;
      opening = true;
      launcher.disabled = true;
      let sessionReady = !config.publicWidgetId;
      try {
        if (config.publicWidgetId) {
          await refreshAppearance(false);
          await ensureSession(false);
          await restoreConversationState();
          sessionReady = true;
        }
      } catch (_error) {
        // Keep the local launcher usable; a later send retries initialization.
      } finally {
        opening = false;
        launcher.disabled = false;
      }
      syncConfigUi();
      panel.hidden = false;
      launcher.classList.add("cpsa-widget__launcher--open");
      launcher.setAttribute("aria-expanded", "true");
      launcher.setAttribute("aria-label", config.labels.close);
      window.dispatchEvent(new CustomEvent("cpsa:widget-opened", {
        detail: { conversationId: activeConversationId || null },
      }));
      if (!welcomeShown) {
        appendMessage("agent", config.isOnline === false ? config.offlineMessage : config.welcomeMessage);
        welcomeShown = true;
      }
      if (sessionReady) startActivity();
      window.setTimeout(function () {
        input.focus();
      }, 0);
    }

    launcher.addEventListener("click", function () {
      if (panel.hidden) {
        void openWidget();
      } else {
        closeWidget();
      }
    });
    closeButton.addEventListener("click", function () {
      closeWidget();
    });
    clearButton.addEventListener("click", function () {
      resetConversation();
      window.dispatchEvent(new CustomEvent("cpsa:conversation-linked", {
        detail: { conversationId: null },
      }));
      setHandoffActive(false);
      satisfaction.hidden = true;
      seenHumanMessageIds.clear();
      messages.replaceChildren();
      appendMessage("agent", config.isOnline === false ? config.offlineMessage : config.welcomeMessage);
      welcomeShown = true;
      input.disabled = false;
      sendButton.disabled = false;
      root.classList.remove("cpsa-widget--loading");
      runActivityCycle();
      input.focus();
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const text = input.value.trim();
      if (!text || sendButton.disabled) {
        return;
      }
      appendMessage("visitor", text);
      const requestConversationId = conversationId();
      const requestRevision = conversationRevision;
      input.value = "";
      input.disabled = true;
      sendButton.disabled = true;
      root.classList.add("cpsa-widget--loading");
      try {
        const response = config.isOnline === false && config.offlineFormEnabled
          ? await sendOfflineMessage(emailInput.value.trim(), text, requestConversationId)
          : await sendChat(text, requestConversationId);
        if (!response.ok) {
          throw new Error("support request failed");
        }
        const payload = await response.json();
        if (
          requestRevision !== conversationRevision ||
          requestConversationId !== conversationId()
        ) {
          return;
        }
        if (payload.conversation_id && payload.conversation_id !== requestConversationId) {
          activeConversationId = String(payload.conversation_id);
          storageSet(config.storageKey, activeConversationId);
        }
        window.dispatchEvent(new CustomEvent("cpsa:conversation-linked", {
          detail: { conversationId: activeConversationId || requestConversationId },
        }));
        if (payload.handoff_id || payload.kind === "handoff") {
          setHandoffActive(true);
        }
        if (config.isOnline === false && config.offlineFormEnabled) {
          appendMessage("agent", config.primaryLanguage.startsWith("zh") ? "留言已提交，我们会通过邮箱回复您。" : "Your message has been submitted. We will reply by email.");
          emailInput.value = "";
        } else {
          appendMessage(
            "agent",
            String(payload.message || config.labels.error),
            payload.related_links || [],
          );
        }
      } catch (error) {
        if (
          requestRevision === conversationRevision &&
          requestConversationId === conversationId()
        ) {
          appendMessage("agent", error && error.code === "reply_timeout" ? config.labels.timeout : config.labels.error);
        }
      } finally {
        if (
          requestRevision === conversationRevision &&
          requestConversationId === conversationId()
        ) {
          input.disabled = false;
          sendButton.disabled = false;
          root.classList.remove("cpsa-widget--loading");
          input.focus();
        }
      }
    });

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") {
        stopActivity();
      } else if (!panel.hidden) {
        startActivity();
      }
    });
    window.addEventListener("pagehide", stopActivity);
    window.addEventListener("offline", stopActivity);
    window.addEventListener("online", function () {
      if (!panel.hidden) startActivity();
    });
    if (scriptElement && scriptElement.dataset.autoOpen === "true") {
      void openWidget();
    }
  }

  function start() {
    mount();
    void refreshAppearance(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
