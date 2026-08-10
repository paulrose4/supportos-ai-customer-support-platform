# Administrator Authentication and RBAC

## Boundary

Dashboard operators authenticate through the first-party administrator identity subsystem. The login form supplies a tenant identifier and credentials only to `POST /v1/auth/login`. After successful verification, the API issues an opaque session token in an HttpOnly cookie. The raw token is never stored in PostgreSQL; only its SHA-256 hash is persisted.

Trusted operator identity is reconstructed from the database-backed session. Chat content, request payload fields, model output, and URL query parameters cannot choose `tenant_id`, roles, scopes, or the operator subject.

## Session Lifecycle

- Login verifies an active tenant-scoped administrator with `hashlib.scrypt`.
- Session creation is transactional and writes `admin_sessions` plus `admin_session.created` audit data.
- Authentication accepts only an unexpired, non-revoked session and returns a minimum `AuthenticatedPrincipal`.
- Logout is idempotent, locks the session row, sets `revoked_at`, and emits one `admin_session.revoked` audit event.
- Bootstrap is idempotent by `(tenant_id, username)` and emits `admin_user.bootstrapped` only when a user is created.
- The session cookie is `HttpOnly`, `SameSite=Strict`, path `/`, and configurable as `Secure`.

The MVP session store supports immediate revocation and avoids a signing secret. It intentionally does not implement password reset, MFA, SSO, invitation flows, or user-management screens yet.

## Roles and Scopes

| Role | Intended use | Notable scopes |
| --- | --- | --- |
| `tenant_owner` | Tenant administrator | Support read/write, tenant knowledge sync, memory read/write, audit read, user management |
| `support_manager` | Support team lead | Support read/write, memory read/write, audit read |
| `support_agent` | Frontline operator | Support read/write, memory read |
| `knowledge_admin` | Knowledge operator | Knowledge read/sync and inbox read |
| `auditor` | Read-only reviewer | Audit, inbox, site, and memory read |

Scopes are derived by deterministic domain rules in `app/domain/rules/rbac.py`; prompts and model output cannot grant permissions. API dependencies authenticate the session and application services enforce the required scope and tenant boundary.

## Bootstrap

For local setup, configure these uncommitted `.env` values:

```text
AUTH_MODE=session
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=<at-least-12-characters>
BOOTSTRAP_ADMIN_DISPLAY_NAME=Local Tenant Owner
```

The lifespan bootstrap creates the tenant owner only when session authentication and bootstrap credentials are configured. After the first production bootstrap, remove `BOOTSTRAP_ADMIN_PASSWORD` from the runtime environment and manage future credentials through a dedicated administrative workflow.

## Production Gate

Before public exposure:

1. Terminate TLS at a trusted reverse proxy and set `ADMIN_COOKIE_SECURE=true`.
2. Restrict `ALLOWED_ORIGINS` to the exact HTTPS Dashboard origins.
3. Remove bootstrap credentials after the first successful start.
4. Use a strong, unique password and add rate limiting, lockout policy, password rotation/recovery, and MFA or SSO.
5. Never publish PostgreSQL or Qdrant ports to the public network.
6. Define retention for sessions and security audit events.
7. Review role assignments and tenant membership through an audited user-management use case.

## Login throttling

Failed administrator logins are rate-limited by a SHA-256 fingerprint derived from the trusted network source observed by the API adapter. The raw source address is not stored. The default policy is 10 failures in 15 minutes followed by a 15-minute lockout; all values are validated runtime configuration.

Write semantics:

- **Idempotency:** each HTTP login attempt is intentionally a distinct security event and increments once; requests rejected while already locked do not write again.
- **Permission:** login is unauthenticated by definition, but the source fingerprint comes from the trusted request adapter rather than JSON, model output, or user claims.
- **Audit:** every counted failure writes `admin_login.failed`; successful authentication removes an existing throttle and writes `admin_login.throttle_cleared`.
- **Transaction:** counter update/lock calculation and its audit event commit atomically under a row lock. Concurrent first writes retry after the unique-source constraint resolves the race.
- **Approval:** no human approval is required. Lockout is deterministic and exposes only a generic 429 response plus `Retry-After`.

The source-based limiter is a durable application control, not a replacement for Caddy/WAF connection and request limits at higher traffic volumes.

## Password changes

An authenticated administrator can change only their own password through `POST /v1/auth/change-password` or the Dashboard Settings page.

Write semantics:

- **Idempotency:** the operation uses the previously observed password hash as an optimistic concurrency precondition. A duplicate or racing request cannot overwrite a newer password and fails closed.
- **Permission:** a valid HttpOnly administrator session and the current password are both required. Tenant and user IDs are taken from the trusted session, never the request body.
- **Audit:** success writes `admin_user.password_changed` with the number of revoked sessions; passwords and hashes are never placed in audit details.
- **Transaction:** password replacement, revocation of all active sessions for that user, and the audit event commit in one PostgreSQL transaction.
- **Approval:** self-service password rotation needs no human approval. Administrative reset of another user remains deferred and will require `users:manage` plus a separate audited workflow.

After success, the API deletes the current cookie and the Dashboard returns to login. Every device must authenticate with the new password.


## Global knowledge synchronization

Global synchronization is deliberately not an administrator role scope. The tenant-scoped user-management API cannot grant `knowledge:sync:global`. A platform operator runs the guarded `scripts/sync_global_knowledge.py` command from a trusted host/container with a one-shot enable flag, explicit confirmation, approval reference, and actor identifier. The sync-start audit event stores the actor and approval context.
