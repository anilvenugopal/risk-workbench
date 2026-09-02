# Email Notifications — Graph API Setup and Send Module

Covers provisioning the sender mailbox, registering Graph API access for
app-only mail send, the `.env` keys, and the modular send function used by
the worker, poller, and the scheduled batch-report job.

This app already has one Entra app registration ("Governance", see
[ENTRA_SETUP.md](ENTRA_SETUP.md)) used for OIDC login. Mail send needs a
**second, separate app registration** — different credential type
(client-credentials, no user present), different permission (`Mail.Send`
instead of `User.Read`), and a different blast radius if the secret leaks.
Reusing "Governance" would mean a leaked mail secret also compromises login.

---

## Part 1 — Mailbox provisioning

Someone with **Exchange Administrator** (or Global Administrator) rights in
the PremiumIQ Microsoft 365 tenant does this. It is tenant configuration, not
something done from the RWB codebase or from Linux — the commands below run
in **Exchange Online PowerShell**, from a Windows machine (or PowerShell 7 +
the `ExchangeOnlineManagement` module, which also runs on Windows/macOS/Linux).

**One-time setup — installing the module.** `Connect-ExchangeOnline` is not
built into Windows; it ships in the `ExchangeOnlineManagement` module, which
must be installed before first use. See
[Installing ExchangeOnlineManagement](#installing-exchangeonlinemanagement-one-time-per-machine)
at the end of this doc if `Connect-ExchangeOnline` is not recognized, or if
`Import-Module` fails with a script-execution-policy error.

Once installed, connect with:

```powershell
Connect-ExchangeOnline -UserPrincipalName admin@premiumiq.com
```

### Scenario A — mailbox does not exist yet (the `no-reply-rwb@premiumiq.com` case)

1. **Create a shared mailbox.** Shared mailboxes don't need an Exchange
   Online license, can't be interactively logged into, and are the standard
   choice for a send-only automation address:
   ```powershell
   New-Mailbox -Shared -Name "RWB No-Reply" -Alias no-reply-rwb -PrimarySmtpAddress no-reply-rwb@premiumiq.com
   ```
2. **Verify it exists:**
   ```powershell
   Get-Mailbox -Identity no-reply-rwb@premiumiq.com
   ```
3. Note the mailbox's email address (`no-reply-rwb@premiumiq.com`) — this is
   what goes in `.env` as `MAIL_SENDER_ADDRESS` (Part 3). The app never needs
   the mailbox's GUID; the Graph `sendMail` call addresses it by email address.

### Scenario B — using an existing mailbox

If IT already has a suitable shared or service mailbox (e.g. an existing
`notifications@premiumiq.com`), skip mailbox creation and just confirm with
`Get-Mailbox` that it's a shared (not user) mailbox, so it can't also be
logged into interactively. Use its address as `MAIL_SENDER_ADDRESS`.

### Restricting the app to only this mailbox

App-only `Mail.Send` (Part 2) grants permission to send **as any mailbox in
the tenant** unless scoped down. An admin scopes it to only the RWB sender
mailbox with an application access policy:

```powershell
# One-time: create a mail-enabled security group containing only the sender mailbox
New-DistributionGroup -Name "RWB-Mail-Senders" -Type Security -Members no-reply-rwb@premiumiq.com
```

This command may create the group's address on `premiumiq.onmicrosoft.com`
instead of `premiumiq.com`. Check the `PrimarySmtpAddress` column it prints
and use that exact address in the next command's `-PolicyScopeGroupId`:

```powershell
# Restrict the RWB Graph app to only send as members of that group
New-ApplicationAccessPolicy `
  -AppId <client_id_from_part_2> `
  -PolicyScopeGroupId <PrimarySmtpAddress_from_previous_command> `
  -AccessRight RestrictAccess `
  -Description "RWB mail app may send only as no-reply-rwb@premiumiq.com"
```

Verify the policy is effective (run as/impersonating the app, or ask the
admin to run):

```powershell
Test-ApplicationAccessPolicy -AppId <client_id_from_part_2> -Identity no-reply-rwb@premiumiq.com
# Expect: AccessCheckResult = Granted

Test-ApplicationAccessPolicy -AppId <client_id_from_part_2> -Identity someoneelse@premiumiq.com
# Expect: AccessCheckResult = Denied
```

Without this policy, a leaked `MAIL_CLIENT_SECRET` could send mail as any
mailbox in the PremiumIQ tenant, not just the no-reply address. Do this step
— it's the difference between "leaked secret sends spam as no-reply" and
"leaked secret sends spam as the CFO."

---

## Part 2 — Graph API app registration and permission

Also done by an Entra admin (Application Administrator or Global
Administrator), in the [Azure Portal](https://portal.azure.com). This is a
**new, separate app registration** from "Governance" — do not add `Mail.Send`
to the login app.

1. **Azure Portal → App registrations → New registration**
   - Name: `RWB Mail Sender`
   - Supported account types: single tenant (PremiumIQ only)
   - No redirect URI needed — this app never redirects a browser anywhere;
     it authenticates server-to-server.
2. **Note the two IDs from the Overview page:**
   - Application (client) ID → `MAIL_CLIENT_ID`
   - Directory (tenant) ID → `MAIL_TENANT_ID` (same tenant as the login app:
     `4dcbd443-2dae-4065-b806-17d9c7781f58`)
3. **Certificates & secrets → New client secret.** Copy the secret **value**
   immediately (it's shown once) → `MAIL_CLIENT_SECRET`. Set a calendar
   reminder for its expiry, same caveat as `ENTRA_CLIENT_SECRET` in
   [ENTRA_SETUP.md](ENTRA_SETUP.md) — an expired secret fails mail send
   silently from the app's point of view (Graph returns 401).
4. **API permissions → Add a permission → Microsoft Graph → Application
   permissions** (not Delegated — there is no signed-in user in this flow)
   → search `Mail.Send` → Add.
5. **Grant admin consent for PremiumIQ.** Application permissions cannot be
   consented by an end user; this button must be clicked by an admin. The
   permission row should show status "Granted for PremiumIQ" with a green
   check.
6. Apply the application access policy from Part 1 using this app's client
   ID, restricting it to the `RWB-Mail-Senders` group.

**Do not add any permission beyond `Mail.Send`.** No `Mail.Read`, no
`Mail.ReadWrite`, no `User.Read.All` — this app registration should be able
to do exactly one thing: send mail as the one mailbox the access policy
scopes it to.

---

## Part 3 — `.env` keys

Named `MAIL_*`, not `GRAPH_*` — this app registration only ever calls the
`sendMail` endpoint, so naming the keys after the broader Graph API would
overstate what they're for.

Add to `infra/.env.example` (placeholders) and `infra/.env` (real values,
never committed):

```ini
# ── Email notifications (Graph API, app-only Mail.Send) ───────────────────────
# Separate Entra app registration from ENTRA_* (OIDC login) — see
# docs/EMAIL_NOTIFICATIONS.md. This app can only send mail as MAIL_SENDER_ADDRESS;
# an application access policy in Exchange Online enforces that restriction.
MAIL_TENANT_ID=
MAIL_CLIENT_ID=
MAIL_CLIENT_SECRET=
MAIL_SENDER_ADDRESS=no-reply-rwb@premiumiq.com
```

`app/config.py` already has placeholder `smtp_*`/`notify_*` fields from an
earlier iteration (R10) that were never wired to any implementation — replace
them, don't add alongside them:

```python
# ── Email notifications (Graph API, app-only Mail.Send) ─────────────────────
# Separate Entra app registration from entra_* (OIDC login) — see
# docs/EMAIL_NOTIFICATIONS.md. Empty mail_client_id disables sending: callers
# check settings.mail_enabled before calling send_email().
mail_tenant_id: str = ""
mail_client_id: str = ""
mail_client_secret: str = ""
mail_sender_address: str = ""

@computed_field
@property
def mail_enabled(self) -> bool:
    return bool(self.mail_tenant_id and self.mail_client_id and self.mail_client_secret)
```

Remove `notify_channels`, `teams_webhook_url`, `smtp_host`, `smtp_port`,
`smtp_from`, `notify_email_to` from `app/config.py` and the corresponding
block from `infra/.env.example` — dead settings from a design that used SMTP
instead of Graph.

---

## Part 4 — Modular send function

One module, `app/notifications/email_sender.py`. It knows how to get a token
and call `sendMail`. It knows nothing about submissions, jobs, analyses, or
templates — every caller builds the subject/body/recipients itself.

```python
"""Email delivery via Microsoft Graph (app-only Mail.Send). See docs/EMAIL_NOTIFICATIONS.md."""

from __future__ import annotations

import httpx
import msal

from app.config import settings

_GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
_token_cache = msal.TokenCache()


def _app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.mail_client_id,
        client_credential=settings.mail_client_secret,
        authority=f"https://login.microsoftonline.com/{settings.mail_tenant_id}",
        token_cache=_token_cache,
    )


def _access_token() -> str:
    app = _app()
    result = app.acquire_token_silent(_GRAPH_SCOPE, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=_GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Graph token acquisition failed: {result.get('error_description') or result.get('error')}"
        )
    return result["access_token"]


def _recipients(addresses: list[str]) -> list[dict]:
    return [{"emailAddress": {"address": a}} for a in addresses]


def send_email(
    to: list[str],
    subject: str,
    html_body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> None:
    """Send one HTML email as settings.mail_sender_address via Graph.

    Raises httpx.HTTPStatusError on a non-2xx Graph response. Callers in the
    worker/poller should catch and log, not let a mail failure fail the job
    it's reporting on.
    """
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": _recipients(to),
            "ccRecipients": _recipients(cc or []),
            "bccRecipients": _recipients(bcc or []),
        },
        "saveToSentItems": "false",
    }
    response = httpx.post(
        f"https://graph.microsoft.com/v1.0/users/{settings.mail_sender_address}/sendMail",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
```

`acquire_token_silent` returns a cached token from MSAL's in-memory
`TokenCache` when one hasn't expired yet (tokens are valid ~60–90 minutes),
so this doesn't request a new token on every email — MSAL handles that
caching internally, the same pattern `app/auth/oidc.py` relies on for its
own flow.

Why `to`/`cc`/`bcc`/`subject`/`html_body` as plain parameters instead of a
dataclass or "message" object: three call sites (worker failure, poller
failure, batch-report job), none needing more fields than this — a wrapper
type would only rename this same argument list.

---

## Part 5 — Invocation examples

**Smoke test.** Run this after filling in the `MAIL_*` values in `infra/.env`,
before wiring any call site, to confirm the app registration, secret, and
access policy actually work end to end:

```python
# scripts/mail_smoke_test.py
"""Send one test email via Graph. Usage: uv run python scripts/mail_smoke_test.py you@premiumiq.com"""

import sys

from app.notifications.email_sender import send_email

if __name__ == "__main__":
    to_address = sys.argv[1]
    send_email(
        to=[to_address],
        subject="RWB mail smoke test",
        html_body="<p>If you got this, MAIL_* config and the Graph access policy are working.</p>",
    )
    print(f"Sent to {to_address}")
```

```bash
set -a && source infra/.env && set +a
uv run python infra/scripts/mail_smoke_test.py you@premiumiq.com
```

`Settings()` doesn't read `infra/.env` on its own — export it first, as
above. Without that, it fails on `session_secret_key: Field required`
before it ever gets to `MAIL_*`.

**Worker, on a job failure** (e.g. in `app/workers/analysis_jobs.py`, inside
whatever `except` block currently marks the job failed):

```python
from app.notifications.email_sender import send_email
from app.config import settings

if settings.mail_enabled:
    analyst_email = _resolve_owner_email(submission.assigned_analyst_id)
    send_email(
        to=[analyst_email],
        subject=f"Analysis failed — submission {submission.id}",
        html_body=(
            f"<p>Analysis job {analysis_job.id} for submission "
            f"<b>{submission.name}</b> failed.</p>"
            f"<p>Error: {error_message}</p>"
        ),
    )
```

**Poller, on an IRP job reaching a FAILED status** — same shape, called from
wherever `app/poller/run.py` currently transitions an `irp_job` to failed:

```python
if settings.mail_enabled:
    send_email(
        to=[_resolve_owner_email(job.submission.assigned_analyst_id)],
        subject=f"IRP job failed — {job.job_type}",
        html_body=f"<p>IRP job {job.irp_job_id} failed: {job.error_detail}</p>",
    )
```

**Batch report job** (new, per-analyst digest — one email per owner, not one
email per submission):

```python
from collections import defaultdict
from app.notifications.email_sender import send_email

def send_daily_digest() -> None:
    by_owner: dict[str, list[Submission]] = defaultdict(list)
    for submission in _submissions_needing_digest():
        by_owner[_resolve_owner_email(submission.assigned_analyst_id)].append(submission)

    for owner_email, submissions in by_owner.items():
        rows = "".join(f"<tr><td>{s.name}</td><td>{s.status_code}</td></tr>" for s in submissions)
        send_email(
            to=[owner_email],
            subject=f"RWB daily digest — {len(submissions)} submission(s)",
            html_body=f"<table>{rows}</table>",
        )
```

`_resolve_owner_email` is a lookup from `submission.assigned_analyst_id` to
the analyst's email (already stored on `app_user` from the OIDC `email`
claim — see [ENTRA_SETUP.md](ENTRA_SETUP.md) Step 4). Per Article 6, this
uses `assigned_analyst_id` only as a mail-routing hint, not an access gate —
every analyst can still see every submission in the UI regardless of who
gets notified about it.

Wrap every call site's `send_email` in a `try/except` that logs and
continues rather than propagating — a Graph outage or an expired secret
should not fail the analysis job or poller pass it's trying to report on.

---

## Troubleshooting

**"invalid_client: The provided client secret keys are expired"** — same
failure mode as `ENTRA_CLIENT_SECRET` in ENTRA_SETUP.md. Rotate
`MAIL_CLIENT_SECRET` in Certificates & secrets, update `.env`, restart.

**403 Forbidden from `sendMail`, but `Mail.Send` shows granted** — the
application access policy (Part 1) is scoping the app to a mailbox other
than `MAIL_SENDER_ADDRESS`, or the policy hasn't propagated yet (can take up
to 30 minutes after `New-ApplicationAccessPolicy`). Confirm with
`Test-ApplicationAccessPolicy`.

**401 Unauthorized** — check `MAIL_TENANT_ID` / `MAIL_CLIENT_ID` match the
"RWB Mail Sender" app registration's Overview page, not the "Governance" app's.

**"ErrorAccessDenied: Access to OData is disabled"** — Exchange Online mailbox
policy or org-wide app access policy is blocking Graph API mail entirely for
the tenant. This is an Exchange Online org setting an admin controls, not a
per-app value; ask the Exchange admin to check
`Get-OrganizationConfig | select OAuth2ClientProfileEnabled`.

---

## Installing ExchangeOnlineManagement (one-time, per machine)

`Connect-ExchangeOnline` and the mailbox/policy cmdlets in Part 1 come from
the `ExchangeOnlineManagement` PowerShell module. It is not installed by
default on Windows — install it once per machine before first use.

1. **Open PowerShell as Administrator** (Start menu → search "PowerShell" →
   right-click → Run as administrator). The install step below writes to a
   machine- or user-wide module path and can fail silently, or prompt for
   elevation mid-command, in a non-elevated window.

2. **Install the module:**
   ```powershell
   Install-Module -Name ExchangeOnlineManagement -Scope CurrentUser -Repository PSGallery -Force
   ```
   - If this is the first PSGallery install on the machine, it may first
     prompt to install the NuGet provider — accept with `Y` or `A`.
   - It may also prompt **"Do you want to run software from this untrusted
     publisher?"** for a Microsoft-signed dependency (e.g.
     `PackageManagement.format.ps1xml`, signed `CN=Microsoft Corporation`) —
     choose **`A` (Always run)** so the same prompt doesn't reappear on every
     future session.

3. **If `Import-Module ExchangeOnlineManagement` fails** with:
   ```
   ... cannot be loaded because running scripts is disabled on this system.
   ```
   the machine's PowerShell execution policy is blocking module script
   loading. Fix it once:
   ```powershell
   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
   ```
   `RemoteSigned` allows locally-installed modules to load while still
   requiring a signature on scripts downloaded from the internet — this is
   the standard, safe setting for running PowerShell modules on a dev
   machine, not a security bypass.

4. **Import and connect:**
   ```powershell
   Import-Module ExchangeOnlineManagement
   Connect-ExchangeOnline -UserPrincipalName admin@premiumiq.com
   ```
   This opens a browser window to sign in (with MFA) as that admin account.
   A banner about the "V3 EXO PowerShell module" and REST-backed cmdlets
   printing after a successful connect is expected — it's version
   information, not an error.

After this one-time setup, only step 4 (`Import-Module` +
`Connect-ExchangeOnline`) is needed in future sessions — the module stays
installed and the execution policy stays set.
