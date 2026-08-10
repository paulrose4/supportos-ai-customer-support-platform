=== Company Product Support Agent ===
Contributors: your-company
Tags: customer support, ai chatbot, product support, live chat
Requires at least: 6.5
Tested up to: 7.0
Requires PHP: 8.0
Stable tag: 0.4.0
License: Proprietary

A secure WordPress widget for a tenant-specific, product-customized customer-support agent.

== Description ==

This plugin adds the centrally managed public widget to a WordPress site. WordPress uses the private site key only on the server to resolve the site's public widget identity. The browser receives the public identity and loads the Dashboard-published appearance directly from the agent API.

Current public-widget capabilities:

* Product knowledge answers with citations.
* Safe clarification when information is missing.
* Automatic human handoff for insufficient knowledge or high-risk requests.
* Anonymous visitors cannot query private order or ticket data.
* Per-site Dashboard-published title, images, messages, color and position.
* Automatic public identity and runtime version synchronization.
* Basic same-origin enforcement and per-IP rate limiting.

== Installation ==

1. Upload `company-product-support-agent.zip` in Plugins > Add New > Upload Plugin.
2. Activate the plugin.
3. Open Settings > Product Support Agent.
4. Enter the public HTTPS Agent API URL.
5. Enter the site key assigned to this WordPress site.
6. Run Connection diagnostics, then enable the widget. Manage its appearance in the Dashboard.

The Agent API maps the site key through Dashboard-managed credentials or the optional `WIDGET_SITE_KEYS` bootstrap fallback.

== Security ==

The site key is stored as a WordPress option and is used only for server-side identity synchronization and the temporary legacy proxy. It is not included in frontend JavaScript. The public widget ID is safe to expose and cannot select a tenant without the server-side registry and origin checks.

== Changelog ==

= 0.4.0 =
* Make the Dashboard-published widget configuration authoritative.
* Resolve and store the public widget identity through the server-side manifest endpoint.
* Propagate immutable runtime asset versions and retain the legacy proxy as a fallback.

= 0.1.0 =
* Initial installable MVP.


