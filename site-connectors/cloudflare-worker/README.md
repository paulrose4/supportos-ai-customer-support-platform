# Cloudflare Worker Connector

This connector is for a static site whose hosting does not execute PHP. It keeps the tenant site key in a Cloudflare Worker secret and forwards only validated visitor payloads to the Agent API.

The Worker exposes:

- `POST /support-agent/chat`
- `POST /support-agent/presence`

The browser must never receive `AGENT_SITE_KEY`. The Worker adds `X-Agent-Site-Key` only on the server-to-server request.

## Configuration

Copy the deployment template before editing it:

```powershell
Copy-Item wrangler.toml.example wrangler.toml
npm install
npx wrangler login
```

Set the secret through Wrangler. Do not put it in `wrangler.toml`, source code, HTML, JavaScript, or Git:

```powershell
npx wrangler secret put AGENT_SITE_KEY
```

`AGENT_API_BASE_URL` must be the public HTTPS origin of the Agent API. `PUBLIC_ORIGIN` must exactly match the site origin, including `www` when that is the canonical hostname.

## Deployment

The template routes only `shop.example.com/support-agent/chat*` and `shop.example.com/support-agent/presence*` to this Worker. Static `widget.js` and `widget.css` remain served by the website. Replace the synthetic domain with the tenant storefront, review the routes and non-secret variables, then deploy:

```powershell
npx wrangler deploy
```

No live deployment is performed by repository tests. The Cloudflare account must own the configured storefront zone and the API must be reachable over public HTTPS.

## Static-site embed

Copy the canonical browser assets from `site-connectors/shared-widget/` to the static site's public assets. Add the following before the closing `body` tag:

```html
<link rel="stylesheet" href="/support-agent/widget.css">
<script>
  window.CPSAWidgetConfig = {
    endpoint: "/support-agent/chat",
    presenceEndpoint: "/support-agent/presence",
    siteId: "demo-store",
    title: "Customer Support",
    welcomeMessage: "Hello! How can we help?"
  };
</script>
<script src="/support-agent/widget.js" defer></script>
```

The static site sends no tenant ID, site key, customer ID, order ID, or ticket ID. The Worker derives trust from its secret binding and the Agent API resolves the tenant and site from the stored credential.

## Write semantics

Chat exchange and handoff persistence remain inside the Agent API transaction and audit boundary. Presence is a best-effort, in-memory operational heartbeat. Cloudflare rate-limit counters are keyed by an HMAC fingerprint of the source address and operation; raw source addresses are not sent to the Agent API or written by this Worker. No irreversible business action is exposed.
