# Site Connectors

## Purpose

The Agent API is connector-neutral. WordPress and static/PHP sites use the same browser Widget, the same server-to-server site credential, and the same tenant-scoped chat and presence endpoints. A connector supplies trusted site identity; visitor text, model output, URL parameters, and browser-provided site IDs never select a tenant.

The default SaaS onboarding path is now the public Widget loader. A tenant registers an exact website origin in Dashboard and copies one public script tag. The public Widget ID is not a credential. The API validates the registered Origin, issues a short-lived HMAC token, and resolves tenant and site identity from PostgreSQL on every request. Private PHP, Worker, and WordPress credentials remain optional advanced connectors for authenticated customer or business-data integrations.

`site-connectors/shared-widget/` is the canonical browser asset source. Run `python scripts/sync_widget_assets.py` before packaging. The build scripts synchronize identical assets into the WordPress plugin and static/PHP package.

## Supported Connectors

| Connector | Browser endpoint | Secret holder | Status |
|---|---|---|---|
| Public static Widget | cross-origin Agent API | no browser secret; platform token signer | implemented, default |
| WordPress | WordPress REST proxy | WordPress option storage | implemented |
| Static site with PHP | same-origin PHP proxy | PHP config outside document root | implemented |
| Static site without PHP | Cloudflare Worker or equivalent edge proxy | provider secret storage | Cloudflare Worker implemented |

Both implemented connectors call:

- `POST /v1/widget/chat`
- `POST /v1/widget/presence`

The API authenticates `X-Agent-Site-Key`, resolves trusted `tenant_id` and `site_id`, then calls application services. Connectors never send tenant or customer identity.

The public Widget instead calls `/v1/public-widget/bootstrap`, `/v1/public-widget/chat`, and
`/v1/public-widget/presence` using credential-free POST requests. A visible page sends a lightweight
Presence request and receives a Presence-only token without creating a chat session. The first
explicit launcher click obtains a separate short-lived chat token bound to one registered Origin.
Tokens contain no tenant ID, site ID, customer ID, order ID, or private key.

Before any service request, the browser stores a random anonymous visitor ID in a first-party
`SameSite=Lax` cookie with a 30-day lifetime. This value is not authentication and cannot authorize
customer or business data. Presence runs while the page is visible when `presenceMode=page_view`;
human-message polling remains tied to an active handoff. A short local-storage lease selects one
visible tab. Known crawler user agents and browser automation are rejected in the browser; Caddy
and the application provide independent server-side crawler rejection.

## Credential Configuration

Dashboard-managed hashed site credentials are the production control plane. `WIDGET_SITE_KEYS` is an optional static bootstrap fallback. `WORDPRESS_SITE_KEYS` remains a deprecated compatibility fallback; the same key cannot appear in both settings.

Example fallback:

```env
WIDGET_SITE_KEYS={"development-site-key-with-32-characters":{"tenant_id":"tenant-demo","site_id":"site-demo"}}
WORDPRESS_SITE_KEYS={}
```

Do not put the site key in HTML, JavaScript, a visitor request body, Git, screenshots, analytics, logs, or Qdrant payloads.

## Static/PHP Package

Build without deploying:

```powershell
python scripts/build_site_connectors.py
```

The archive is written to `dist/company-product-support-agent-static-php.zip`. Runtime `config.php` is deliberately excluded.

Package layout:

```text
site-connectors/static-php/
├── private/
│   ├── .htaccess
│   └── config.example.php
├── public/support-agent/
│   ├── chat.php
│   ├── presence.php
│   ├── widget.js
│   ├── widget.css
│   └── embed-example.html
└── src/connector.php
```

For a future installation, copy `config.example.php` to `config.php` outside the public document root whenever hosting permits. Set `CPSA_CONFIG_PATH` to its absolute path. The bundled relative fallback exists for constrained shared hosting, but the private directory must then be denied by the web server.

Required configuration:

- `agent_api_base_url`: public HTTPS Agent API origin.
- `site_key`: random site credential of at least 32 characters.
- `public_origin`: exact website origin allowed to call the proxy.

HTTP Agent API URLs are rejected unless `allow_insecure_agent_api=true`, which is only for isolated local testing.

## Cloudflare Worker Package

For a pure static site, use `site-connectors/cloudflare-worker/`. The package is designed for storefronts served through Cloudflare that do not expose a confirmed PHP runtime.

The Worker exposes the same two browser-facing paths as the PHP connector:

- `POST /support-agent/chat`
- `POST /support-agent/presence`

It validates the exact configured `Origin`, rejects non-POST requests, bounds JSON payloads, applies Cloudflare Rate Limiting bindings, and forwards the site key only in the Worker-to-Agent API request. The `AGENT_SITE_KEY` value is provisioned with `wrangler secret put` and is never part of the static site bundle.

The synthetic example routes are `shop.example.com/support-agent/chat*` and `shop.example.com/support-agent/presence*`; static Widget assets remain served by the website. Review `site-connectors/cloudflare-worker/wrangler.toml.example` and follow `site-connectors/cloudflare-worker/README.md`. Deployment is intentionally separate from local tests and must be reviewed before changing DNS or Worker routes.

## Static/PHP Security

The connector:

- accepts POST only;
- requires an exact configured browser `Origin` by default;
- limits request bodies to 16 KiB;
- validates messages, opaque IDs, and relative page paths;
- rate-limits chat and presence separately by a keyed hash of the source address;
- verifies upstream TLS certificates and hostnames;
- adds the site key only during the server-to-server call;
- returns only whitelisted chat fields;
- converts upstream authentication and provider failures to generic visitor errors;
- never trusts browser tenant, site, customer, order, or ticket identifiers.

The source-address fingerprint is used only as a temporary one-minute rate-limit key. Raw source addresses are not written to the rate-limit file.

## Write Semantics

| Write | Idempotency | Permission | Audit | Transaction | Human approval |
|---|---|---|---|---|---|
| PHP rate-limit counter | fixed-window increment; duplicate requests increment | exact origin plus connector availability | no durable business audit; operational only | file lock and truncate/write | not required |
| Presence heartbeat | overwrite by tenant/site/visitor | trusted site credential | no durable audit; no PII payload | in-memory atomic replacement | not required |
| Chat exchange | clients must not automatically retry an ambiguous timeout | trusted site credential; public knowledge scope only | messages, run, tools and trace persist in PostgreSQL | backend transaction per exchange | not required because no irreversible operation exists |
| Handoff creation | idempotent for the same agent run | deterministic graph/risk decision | audited and persisted | backend transaction | receiving human accepts work in Dashboard |
| Site creation/key rotation | desired-state idempotent | tenant owner with `sites:manage` | audited | PostgreSQL transaction | authenticated owner is the approver; rotation requires explicit UI confirmation |
| Public Widget bootstrap | read-only token issue; repeated calls produce independent short-lived tokens | exact registered Origin, active public Widget ID, source rate limit | correlation and request logs only; token is not persisted | no database write | not required |
| Public chat admission | idempotent by tenant, site, and browser-generated `request_id` | valid short-lived token, exact Origin, per-source rate limit, daily site quota | conversation exchange and any handoff are audited by existing services | admission and daily quota decision use a PostgreSQL row lock; exchange persistence remains its own transaction | not required because no irreversible action is exposed |

## Local Validation

No public deployment is performed by these commands:

```powershell
python scripts/sync_widget_assets.py
python scripts/build_wordpress_plugin.py
python scripts/build_site_connectors.py
python -m pytest tests/contract/test_site_connectors.py
```

The Worker package can be type-checked and deployed from its own directory after Node.js dependencies are installed:

```powershell
Set-Location site-connectors/cloudflare-worker
npm install
npm run typecheck
```

PHP syntax can be checked in the local WordPress container or another PHP 8 runtime. Public DNS, tunnels, live site files, and live credentials remain a final deployment phase.
