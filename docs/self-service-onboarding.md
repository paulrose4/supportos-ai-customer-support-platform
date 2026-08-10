# Self-Service Tenant Onboarding

The application supports two independent registration paths:

- `POST /v1/onboarding/signup` consumes a one-time enrollment code and, after email verification, creates one new tenant and a `tenant_owner` membership.
- `POST /v1/auth/register` redeems an administrator invitation and joins an existing tenant. It never provisions a tenant.

The onboarding path never accepts `tenant_id`, roles, plan, quota, site IDs, or widget credentials from the client.

Platform operators can use `POST /v1/platform/workspace-onboarding-codes` to issue a code in one
operation. The service idempotently prepares the default authority and the target email-domain
policy, then returns the one-time code and a signup URL containing the code in the URL fragment.
The raw code is returned only from this response; list and audit views expose only its prefix.

## Production rollout

1. Apply migrations with `python -m alembic upgrade head`.
2. Keep `SELF_SERVICE_SIGNUP_ENABLED=false` and `EMPLOYEE_ENROLLMENT_ENABLED=false` while creating the enrollment authority and policy.
3. Set a randomly generated `ENROLLMENT_TOKEN_SECRET` (at least 32 characters) in the secret manager. Do not reuse the widget token secret.
4. Use the platform APIs to create an authority, a policy with approved email domains, and one code per employee. Codes are stored as HMAC digests and are single-use.
5. Enable `EMPLOYEE_ENROLLMENT_ENABLED=true`, then enable `SELF_SERVICE_SIGNUP_ENABLED=true` after smoke tests.
6. Set `LEGACY_LOGIN_ENABLED=false` in production. Keep any break-glass access isolated and audited.

The authority row atomically reserves active and total tenant capacity when signup starts. Expired intents release the code reservation and capacity on the next signup cleanup pass. The database also enforces one self-service provisioning per user.

Configure `TRUSTED_PROXY_CIDRS` with the exact ingress/load-balancer networks when the API is behind a reverse proxy. Registration and login throttles accept `X-Forwarded-For`, `X-Real-IP`, or `CF-Connecting-IP` only when the direct peer is in one of these networks; otherwise forwarded headers are ignored. Leave it empty for a directly internet-facing API.

## Site verification

New sites are created with `verification_status=pending`. The dashboard can issue a challenge using either method:

- DNS TXT: create the returned TXT record at the returned name.
- Install script: serve the returned value from `/.well-known/managed-support-verification.txt` over the HTTPS origin.

The verify endpoint performs the DNS or HTTPS probe and compares the result with the stored HMAC digest. Raw challenge values are not persisted. A site must have both `status=active` and `verification_status=verified` before Widget bootstrap, knowledge preflight, or web sync. Changing the HTTPS origin returns the site to `pending` and drafts the initial Widget configuration.

Use `SITE_VERIFICATION_TTL_SECONDS` to control challenge expiry. Site origins must be HTTPS origins without paths, credentials, query strings, fragments, non-default ports, or private/reserved IP addresses.

## Operational checks

Monitor enrollment rejection and quota exhaustion, code reuse, verification failures, provisioning failures, and email outbox lag. All enrollment, tenant provisioning, site challenge, verification, revoke, and disable operations emit audit events.
