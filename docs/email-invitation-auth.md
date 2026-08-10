# Invite-Only Email Authentication

## Purpose

The Dashboard uses email and password as its primary operator sign-in. Account creation is
closed: an administrator must issue a one-time invitation bound to an email address, tenant,
role set, and expiry. The client never chooses `tenant_id` or roles during registration.

Authorization remains based on active `tenant_memberships`. Email identifies the global user;
an invitation creates or extends that user's trusted workspace membership.

## Security Model

- Invitation and password-reset values are generated with high-entropy random tokens.
- Only SHA-256 token hashes are stored. Raw tokens are returned or emailed once.
- Invitation links place the token in the URL fragment (`/#invite=...`) so reverse proxies do
  not receive it in the HTTP request target.
- An invitation is bound to one normalized email, one tenant, one role set, and one expiry.
- Redemption locks the invitation row and completes user, credential, membership, invitation,
  audit, and session writes in one database transaction.
- Reuse, expiry, revocation, and concurrent redemption fail without creating extra membership.
- Existing email accounts must provide their current password before accepting another
  workspace invitation.
- Password changes and resets revoke all active sessions for the user.
- Login throttling keys on a source fingerprint and a hash of the normalized email.

## Configuration

Minimum production settings:

```dotenv
AUTH_MODE=session
EMAIL_LOGIN_ENABLED=true
INVITE_REGISTRATION_ENABLED=true
ADMIN_PUBLIC_BASE_URL=https://support.example.com
ADMIN_COOKIE_SECURE=true
EXTERNAL_LOGIN_PROVIDERS=[]
DINGTALK_LOGIN_ENABLED=false
LEGACY_LOGIN_ENABLED=true
```

Configure SMTP before enabling password reset and automatic invitation delivery:

```dotenv
TRANSACTIONAL_EMAIL_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=service-account
SMTP_PASSWORD=replace-with-a-secret
SMTP_FROM_ADDRESS=support@example.com
SMTP_USE_STARTTLS=true
PASSWORD_RESET_TTL_SECONDS=1800
```

When `TRANSACTIONAL_EMAIL_ENABLED=false`, invitation creation still returns the one-time URL to
an authorized administrator, but forgot-password delivery is unavailable in the Dashboard.

## First Deployment

1. Set `BOOTSTRAP_ADMIN_EMAIL` to the initial owner's email and provide a strong
   `BOOTSTRAP_ADMIN_PASSWORD`.
2. Apply migration `p6e7f8a9b012` with `alembic upgrade head` through the migrator role.
3. Start the application and sign in with the bootstrap email and password.
4. In Dashboard Settings, create an invitation for a non-bootstrap test account.
5. Open the invitation URL, register, sign out, and sign back in with email and password.
6. Verify workspace selection for a user who belongs to more than one tenant.
7. Verify disabling a membership prevents workspace access and existing sessions can be
   revoked.
8. Remove `BOOTSTRAP_ADMIN_PASSWORD` and set `BOOTSTRAP_PLATFORM_OWNER=false`.
9. Set `LEGACY_LOGIN_ENABLED=false` only after the email acceptance checks pass.

Keep a break-glass credential offline and restrict its use operationally. Do not leave a
predictable bootstrap password in environment files.

## Invitation Lifecycle

An operator with `users:manage`, or a platform owner/operator, can create, list, and revoke
invitations under `/v1/platform/tenants/{tenant_id}/invitations`. Use 24 to 72 hours for normal
expiry; the API allows 1 to 168 hours.

The registration endpoint accepts only:

```json
{
  "invitation_token": "one-time-token",
  "display_name": "Invited User",
  "password": "at-least-12-characters"
}
```

It does not accept an email, tenant, or role. Those values always come from the stored
invitation.

## Password Recovery

`POST /v1/auth/password/forgot` always returns an empty success response so callers cannot
enumerate registered email addresses. A valid account receives a link containing
`/#reset=...`. The token is single-use and expires according to
`PASSWORD_RESET_TTL_SECONDS`.

If SMTP delivery fails, inspect application logs and the mail provider without revealing
whether the submitted address has an account. An administrator can temporarily issue a new
invitation only for a new membership; invitations are not a replacement for password reset.

## Incident Response

- Leaked pending invitation: revoke it in Settings and create a replacement.
- Suspected account compromise: disable the tenant membership, revoke sessions, then reset the
  password.
- SMTP outage: keep `TRANSACTIONAL_EMAIL_ENABLED=false` until delivery is healthy and distribute
  newly created invitation URLs through an approved secure channel.
- Lost owner access: use the offline break-glass procedure, restore the owner membership, audit
  the action, and rotate the break-glass password.
- Repeated login failures: review `email_login_throttles` and audit events; do not expose raw
  emails or tokens in tickets or logs.

## Acceptance Checks

```bash
curl --fail https://support.example.com/v1/auth/providers
```

Expected flags after final cutover:

```json
{
  "providers": [],
  "legacy_login_enabled": false,
  "email_login_enabled": true,
  "invite_registration_enabled": true,
  "password_reset_enabled": true
}
```

Also verify login, logout, one-time invitation rejection on replay, workspace switching,
membership disablement, password reset, password change, and session revocation.
