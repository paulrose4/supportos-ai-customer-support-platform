# Static/PHP Site Connector

This package connects a traditional static HTML site to the customer-support Agent API without exposing the site credential to visitors.

Do not upload it to a live site until the Agent API, site credential, knowledge, handoff queue, email routing, and rollback plan have passed local acceptance. General architecture, security controls, write semantics, and packaging instructions are in `docs/site-connectors.md`.

## Local packaging

```powershell
python scripts/build_site_connectors.py
```

The generated archive intentionally contains `config.example.php`, never runtime `config.php`.
