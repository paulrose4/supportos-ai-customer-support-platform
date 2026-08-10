# DingTalk SSO Runbook

## Responsibility boundary

DingTalk proves who the employee is. Local `tenant_memberships` and `platform_role_assignments` decide which workspaces and administrative actions that employee can access. A DingTalk department, callback parameter, email address, phone number, or browser-provided tenant ID never grants access.

The application exchanges the authorization code server-side, validates the returned `corpId`, and then issues the same HttpOnly application session used by local emergency login. DingTalk access tokens are not application sessions and are never stored in browser storage.

## DingTalk application setup

Create an internal DingTalk application and configure the production callback exactly as:

```text
https://support.company.example/v1/auth/providers/dingtalk/callback
```

Configure only the permissions needed for OAuth identity and the current user profile. Record the application client ID, client secret, and enterprise corp ID in the production secret store, then set:

```text
EXTERNAL_LOGIN_PROVIDERS=["dingtalk"]
DINGTALK_LOGIN_ENABLED=true
ADMIN_PUBLIC_BASE_URL=https://support.company.example
DINGTALK_CLIENT_ID=...
DINGTALK_CLIENT_SECRET=...
DINGTALK_ORGANIZATION_ID=...
```

Do not place these values in Dashboard JavaScript, Widget configuration, logs, audit details, or source control.

## First activation

1. Deploy with `LEGACY_LOGIN_ENABLED=true`, a one-time bootstrap admin, and `BOOTSTRAP_PLATFORM_OWNER=true`.
2. Sign in through the restricted emergency entry and create the required tenants.
3. Ask each employee to complete DingTalk login once. An employee with no membership receives a controlled access-denied screen but is created as a verified global user.
4. Assign that global user to the correct tenant with a tenant role. Use `tenant_owner` for colleagues who fully manage their own website; do not grant `platform_owner` for this purpose.
5. Verify single-workspace login, multi-workspace selection, workspace switching, logout, and session revocation with two real internal tenants.
6. Set `LEGACY_LOGIN_ENABLED=false`, remove the bootstrap password from the runtime environment, and redeploy.

## Personnel lifecycle

Membership disablement revokes active sessions for that tenant. When an employee leaves, disable every membership and revoke all active sessions. Department synchronization may automate status discovery later, but it must not silently create tenant permissions or delete memberships on a synchronization failure.

## Incident and break-glass flow

If DingTalk is unavailable, an approved operator may temporarily enable the emergency account from a restricted network. Every use must create an alert and an audit review. Disable the entry again after recovery; never leave ordinary password login enabled as a convenience fallback.

Rotate the DingTalk client secret after suspected exposure, personnel changes affecting secret access, or the normal secret-rotation interval. Existing application sessions remain locally revocable and do not depend on a long-lived DingTalk token.

## Acceptance

- Forged, expired, and replayed OAuth state is rejected.
- Concurrent callback attempts allow exactly one completion.
- A different DingTalk corp ID is rejected.
- Verified employees without an active membership cannot enter a workspace.
- Workspace switching rejects non-members and rotates the session ID.
- The old WebSocket and tenant-scoped browser state disappear after switching.
- Logs contain no authorization code, DingTalk token, client secret, phone number, or email address.
