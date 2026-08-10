# Tenant Knowledge Sources

## Directory Layout

Tenant and global knowledge use separate trusted source roots:

```text
examples/knowledge-sources/
└── tenants/
    └── {tenant_id}/
        ├── obsidian/
        └── wordpress/
examples/global-vault/
```

`TENANT_KNOWLEDGE_ROOT` points to the `tenants/` directory. A tenant synchronization resolves only `{root}/{tenant_id}/obsidian`; it never enumerates sibling tenant directories. Global synchronization remains a separate privileged operation and uses the reserved `__global__` partition.

## Trusted Tenant Boundary

The synchronization tenant comes from the authenticated administrator or trusted background-job configuration. It never comes from Markdown text, a URL parameter supplied by a visitor, or model output.

The scanner validates that `tenant_id` is a safe path segment and rejects traversal or separator characters. It also resolves the final source directory and verifies that it remains below the configured tenant root.

Every parsed document is checked again:

```text
trusted synchronization tenant
    = directory tenant
    = Markdown frontmatter tenant_id
    = Qdrant payload tenant_id
```

A frontmatter mismatch inside the selected tenant directory is quarantined: it is not staged, embedded, or indexed; it increments `excluded_count`; and the synchronization report contains a sanitized `quarantined` reason. The mismatch does not prevent valid documents in the same tenant directory from being processed.

## WordPress Sources

Future WordPress crawlers should write normalized knowledge documents to `{root}/{tenant_id}/wordpress/{site_id}/`. The trusted crawl job supplies both `tenant_id` and `site_id`; HTML content and the LLM cannot select either value. Raw HTML should be retained outside Qdrant when needed for audit, while Qdrant receives only cleaned chunks and retrieval metadata.

## Write Controls

- Synchronization is permission-controlled by `knowledge:sync` or `knowledge:sync:global`.
- Document/version writes remain idempotent through deterministic content and version identifiers.
- PostgreSQL control-plane writes use bounded transactions and audit the synchronization lifecycle.
- Qdrant replacement deletes and reindexes only the trusted tenant plus document identifier.
- No human approval is required for ordinary published public knowledge; high-risk publication still requires reviewer metadata. Tenant mismatches are fail-closed and never require an LLM decision.


## Global Sync Authorization

Global company knowledge is stored in the reserved `__global__` partition and is visible as a shared retrieval source. A tenant owner intentionally does not receive `knowledge:sync:global`; granting it through tenant user management would create a cross-tenant write path.

Run the guarded command from the API container only after platform review:

```bash
docker compose exec -e PLATFORM_GLOBAL_SYNC_ENABLED=true api \
  python -m scripts.sync_global_knowledge \
  --confirm-global-sync \
  --approval-reference "review-YYYY-MM-DD" \
  --actor-subject-id "platform-operator"
```

The command is disabled by default, requires OS/container access plus explicit confirmation, records actor, correlation, approval, vault path, and sync job information in PostgreSQL audit data, and exits non-zero if any document fails. The environment flag is one-shot and must not remain enabled.
