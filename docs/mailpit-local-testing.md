# Local email testing with Mailpit

Mailpit is a local SMTP sink. The application delivers mail to it, but Mailpit does not send
anything to the public Internet. This makes it suitable for testing enrollment, invitations,
password reset, and other transactional email flows.

## Start the local stack

From the repository root:

```powershell
docker compose -f docker-compose.yml -f docker-compose.mailpit.yml up -d --build
```

The local endpoints are:

- Dashboard: `http://localhost:8090`
- Mailpit inbox: `http://localhost:8025`
- Mailpit SMTP: `localhost:1025` from the host, or `mailpit:1025` from the API container

The override enables `TRANSACTIONAL_EMAIL_ENABLED`, `EMPLOYEE_ENROLLMENT_ENABLED`, and
`SELF_SERVICE_SIGNUP_ENABLED` only for this local stack. It uses `no-reply@local.test` and
STARTTLS is disabled because Mailpit is local and unauthenticated.

### When Docker Hub is unavailable

The repository also works with the Mailpit Windows binary. Start it from the repository root:

```powershell
Start-Process -FilePath ".\.cache\mailpit\mailpit.exe" `
  -WorkingDirectory (Get-Location).Path `
  -ArgumentList @('--listen','127.0.0.1:8025','--smtp','0.0.0.0:1025','--database','.cache/mailpit/mailpit.db','--disable-version-check') `
  -WindowStyle Hidden
```

For this mode, the local `.env` must point the container at the Windows host:

```dotenv
SMTP_HOST=host.docker.internal
SMTP_PORT=1025
SMTP_USE_STARTTLS=false
```

Then recreate only the API container:

```powershell
docker compose up -d --force-recreate api
```

## Test independent workspace onboarding

1. Sign in to the Dashboard with a `platform_owner` or `platform_operator` account.
2. Open Settings -> Platform administration -> Independent workspace onboarding.
3. Enter a test address, for example `new-user@example.test`, and generate the code/link.
4. Open `http://localhost:8025` in another tab. The verification email should appear there.
5. Open the link from the captured email, or paste the generated signup link into a private
   browser window. Complete email verification and set the new account password.
6. The new account should enter a new, empty workspace. The original workspace is not reused.

For a quick health check, confirm the API advertises the feature:

```powershell
Invoke-RestMethod http://localhost:8000/v1/auth/providers
```

The response should contain `"self_service_signup_enabled": true`.

## Stop or reset Mailpit

```powershell
docker compose -f docker-compose.yml -f docker-compose.mailpit.yml down
```

The Mailpit inbox is ephemeral in this setup. The application database is retained in the
existing `postgres_data` volume, so previously created test accounts and workspaces remain.

## Production differences

Do not use Mailpit in production. Replace the override with a real SMTP provider or an
enterprise relay in `.env.production` / the secret manager:

```dotenv
TRANSACTIONAL_EMAIL_ENABLED=true
SMTP_HOST=smtp.provider.example
SMTP_PORT=587
SMTP_USERNAME=service-account
SMTP_PASSWORD=<secret-manager-value>
SMTP_FROM_ADDRESS=support@example.com
SMTP_USE_STARTTLS=true
```

Production should also use:

- `ADMIN_PUBLIC_BASE_URL=https://your-public-domain` so links are usable outside the server.
- HTTPS and `ADMIN_COOKIE_SECURE=true`.
- A randomly generated `ENROLLMENT_TOKEN_SECRET` of at least 32 characters.
- A verified sender/domain, SPF/DKIM/DMARC, SMTP credentials, and provider monitoring.
- A staged rollout: keep self-service disabled, run migrations and smoke tests, then enable
  `EMPLOYEE_ENROLLMENT_ENABLED=true` and `SELF_SERVICE_SIGNUP_ENABLED=true`.

If the provider is temporarily unavailable, leave transactional email disabled and distribute
one-time links only through an approved secure channel; do not point production at Mailpit.
