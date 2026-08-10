# WordPress Connector

## What Is Deployed

The installable plugin lives under `wordpress-plugin/company-product-support-agent/`. Version 0.4.0
uses the Dashboard-published public Widget by default. WordPress sends the private site key only to
the server-side Manifest endpoint, stores the returned public Widget ID and immutable asset version,
and never exposes the private key to visitors.

```text
WordPress server
  -> Agent API GET /v1/widget/manifest + X-Agent-Site-Key
  -> trusted registry resolves tenant_id, site_id and public_widget_id

Visitor browser
  -> versioned https://support-api.example.com/widget.js
  -> data-site-id contains only public_widget_id
  -> Appearance / Bootstrap validate the registered Origin
  -> tenant-filtered knowledge retrieval / safe handoff
```

## 1. Deploy the Agent API

The WordPress server must be able to reach the API over public HTTPS. Do not configure a browser-only URL such as `localhost`.

Create each WordPress installation from **Dashboard → Settings → WordPress Sites and Keys**.

The Dashboard generates a 256-bit key, sends only its SHA-256 hash to PostgreSQL, and displays the plaintext once for copying into WordPress.

`WIDGET_SITE_KEYS` is the connector-neutral bootstrap fallback. `WORDPRESS_SITE_KEYS` remains a deprecated compatibility fallback. Database-managed credentials take precedence and do not require an API restart. Never put a site key in Git, page source, browser JavaScript, or a visitor request body.

## 2. Prepare Tenant Product Knowledge

Each site's key maps to one tenant. Knowledge frontmatter must use the same `tenant_id`. Add published product documents to that tenant's controlled Vault and run:

```bash
curl -X POST https://support-api.example.com/v1/knowledge/sync
```

The current administrative sync endpoint uses the configured trusted admin identity. For multiple production tenants, deploy a dedicated authenticated sync worker or tenant-specific administrative credential before allowing tenant administrators to trigger synchronization.

## 3. Build and Install the Plugin

```bash
python scripts/build_wordpress_plugin.py
```

Upload `dist/company-product-support-agent.zip` from WordPress:

1. Plugins > Add New > Upload Plugin.
2. Activate **Company Product Support Agent**.
3. Open Settings > Product Support Agent.
4. Enter the public Agent API base URL, for example `https://support-api.example.com`.
5. Enter the one-time site key copied from Dashboard Settings.
6. Run **Test connection** so the plugin resolves its public Widget identity.
7. Enable the widget and save. Configure title, messages, images, color and position in Dashboard.

The settings page must show **Dashboard published version** before cutover. If it shows **Legacy
local proxy**, the old proxy remains available for one compatibility period, but Dashboard
appearance changes will not be authoritative.

## 4. Current Authentication Boundary

The public widget principal is anonymous and receives only `knowledge:read`. It can answer tenant-specific public product knowledge and create safe handoffs. It cannot query customer orders or tickets.

Authenticated WooCommerce order lookup requires a separate integration that signs the logged-in WordPress/WooCommerce customer identity server-side and maps it to an authoritative backend customer ID. Do not forward an email, WordPress user ID or customer ID supplied by browser JavaScript and treat it as identity.

## 5. Security and Operations

- Keep WordPress, PHP and plugins patched.
- Use HTTPS for WordPress and the Agent API.
- Use one random site key per site and rotate it if exposed.
- Do not expose the Agent API's administrative `/v1/chat`, knowledge-sync or handoff-queue endpoints publicly without production authentication.
- The proxy enforces same-site browser origins and a basic per-IP rate limit. Add edge/WAF rate limiting for production scale.
- WordPress only returns whitelisted response fields and converts upstream errors to generic messages.
- The widget renders all messages with `textContent`, not `innerHTML`.
- Monitor handoff queue availability before enabling public traffic.

## 6. Smoke Test

For the local Docker environment, start the main backend first and run the automated installation and proxy test:

```powershell
docker compose up -d --build
pwsh -File scripts/smoke_wordpress_plugin.ps1 -SiteKey "<development-site-key>"
```

The script installs WordPress when needed, activates and configures the plugin, verifies that the site key is absent from the frontend, requests a cited knowledge answer, confirms that an anonymous order request cannot access business data, and verifies the insufficient-knowledge human-handoff path.

On the WordPress site, open the widget and ask a product question that exists in the mapped tenant Vault. Confirm:

- Page source contains `data-site-id` and does not contain `window.CPSAWidgetConfig` in public mode.
- `widget.js`, `widget-runtime.js`, and `widget.css` carry the same immutable asset version.
- Appearance, Bootstrap, chat, and media requests use the configured Agent API origin.
- No site key appears in page source, browser JavaScript or the request payload.
- The Dashboard runtime panel reports WordPress 0.4.0 and the current published config version.
- The answer contains a valid source citation.
- An unknown question creates a handoff.
- An order-status question asks for trusted customer authentication rather than returning order data.

## Troubleshooting

- **Widget not visible:** confirm Enabled, API URL, site key, and public Widget ID are saved.
- **Old title or image:** run Test connection, purge WordPress/CDN page caches, and confirm the page
  loads the hosted versioned script rather than a cached plugin asset.
- **Broken image:** verify `PUBLIC_WIDGET_BASE_URL` is the public HTTPS API origin and media requests
  target that origin, not the storefront domain.
- **502 response:** verify DNS, TLS and outbound connectivity from the WordPress server to the Agent API.
- **401 upstream:** confirm the site is active and the pasted key matches the latest Dashboard-generated key.
- **Knowledge handoff:** synchronize published documents for the tenant mapped to the site key.
- **429 response:** wait one minute or configure production edge rate limits appropriate for the site.



## Online Presence

The lightweight Loader creates or reuses a first-party anonymous visitor cookie with a 30-day lifetime. As soon as a page is visible it sends a cross-origin-safe public `enter` event, then a jittered heartbeat every 20–25 seconds without loading the chat Runtime. Hidden or offline pages stop sending. A renewable local-storage/BroadcastChannel lease makes the most recently focused visible tab the only sender, and SPA history changes report a new idempotent `page_view_id` immediately. Opening the support panel only changes `widget_state`; it does not add another visitor or start a second Presence loop. Presence failures never block the page or chat.

The Dashboard considers a visitor online only while a heartbeat exists within the configured 45-second window. The visitor ID is not customer authentication and must never authorize order, payment, account, refund, or memory access.

Each heartbeat is an idempotent overwrite for the same tenant/site/visitor. The public token binds
the registered Widget ID, exact Origin and anonymous visitor hash. Redis stores the short-lived
presence record; it is not an authorization or durable audit source.

## Connection diagnostic

Plugin version 0.4.0 includes **Settings > Product Support Agent > Test connection**. Save the API
URL and site key first, then run the test. It calls the server-side Manifest endpoint, refreshes the
public Widget ID plus asset/config versions, and does not send a chat message or invoke the LLM. A
legacy presence probe is used only when Manifest migration is unavailable.
