# RHEL9 Deployment Runbook

Risk Workbench runs on RHEL9 without `uv`. The server uses Python 3.14,
`requirements.txt`, nginx, Valkey, the Microsoft ODBC Driver 18, uvicorn,
one Dramatiq worker supervisor per queue, and one poller.

## Required before application installation

The following requirements are outside `rhel9-app-install.sh`:

- `rhel9-setup.sh` and `rhel9-check-prereqs.sh` complete successfully.
- The deployment account owns the application directory and can write
  `/var/lib/risk-workbench`.
- `infra/.env` exists on the server with production secrets. Deployment never
  creates or replaces the file.
- `rwb_workbench`, `rwb_exposure`, and `rwb_loss` already exist. DataBridge also
  exists when DataBridge-backed functions are enabled.
- The configured application SQL login can connect to all configured
  databases. It needs schema migration rights only on `rwb_workbench`.
  `rhel9-app-install.sh` never creates a database, migrates Exposure or Loss,
  or sends DDL to DataBridge.
- Exposure and Loss schemas have been prepared by their owners or by the
  separate bootstrap process before production deployment.
- The broker shared drive is mounted at `SHARED_DRIVE_ROOT` and is readable by
  the deployment account when broker-file browsing is enabled.
- CinRe provides the production TLS certificate, firewall rules, SELinux
  policy, DNS name, database accounts, and secrets delivery.

## Network connectivity

Allow only the paths required by the selected configuration:

| Direction | Destination | Port | Required for |
|---|---|---:|---|
| Inbound | deployment runner to RHEL9 | 22 | SSH and rsync deployment |
| Inbound | users or CinRe proxy to nginx | 80/443 | Web access; CinRe owns TLS termination |
| Outbound | Workbench, Exposure, Loss SQL Server hosts | 1433 or configured port | Application database access |
| Outbound | DataBridge SQL Server host | configured port | DataBridge reads |
| Outbound | `RISK_MODELER_BASE_URL` | 443 | Risk Modeler API |
| Outbound | Risk Modeler authentication and returned object-storage URLs | 443 | API login and file transfer |
| Outbound | public PyPI (`pypi.org`, `files.pythonhosted.org`) or an approved internal mirror | 443 | Dependency installation |
| Outbound | Microsoft Entra endpoints | 443 | Only when `AUTH_MODE=oidc` or `both` |
| Outbound | Microsoft Entra and Graph endpoints | 443 | Only when `MAIL_*` email notifications are enabled |

Valkey listens on `127.0.0.1:6379`. Uvicorn listens on `127.0.0.1:8000`.
Neither port should be reachable from another host. nginx is the web entry
point.

If RHEL9 cannot reach public PyPI, configure pip to use an approved internal
mirror or populate its cache before running the installer. The lock export
contains hashes, and pip installs with `--require-hashes`.

## Production environment file

Start from `infra/.env.example`, replace every sample credential, and set at
least:

```dotenv
APP_ENV=production
AUTH_MODE=password
SESSION_SECRET_KEY=<random 64-character hex value>
REDIS_URL=redis://127.0.0.1:6379/0

MSSQL_WORKBENCH_SERVER=<host>
MSSQL_WORKBENCH_DATABASE=rwb_workbench
MSSQL_WORKBENCH_USER=<application login>
MSSQL_WORKBENCH_PASSWORD=<secret>

MSSQL_EXPOSURE_SERVER=<host>
MSSQL_EXPOSURE_DATABASE=rwb_exposure
MSSQL_EXPOSURE_USER=<application login>
MSSQL_EXPOSURE_PASSWORD=<secret>

MSSQL_LOSS_SERVER=<host>
MSSQL_LOSS_DATABASE=rwb_loss
MSSQL_LOSS_USER=<application login>
MSSQL_LOSS_PASSWORD=<secret>
```

Set the `RISK_MODELER_*`, `MSSQL_DATABRIDGE_*`, and `SHARED_DRIVE_ROOT`
variables for IRP-connected functions. OIDC variables are not required for
`AUTH_MODE=password`. Production cookies are marked `Secure` when
`APP_ENV=production`, so the user-facing endpoint must use HTTPS.

## First installation

From the deployed application directory on RHEL9:

```bash
cd /rms
APP_DIR=/rms DEPLOY_USER=cinreadm PYTHON_PKG=python3.14 \
  bash infra/scripts/rhel9/rhel9-check-prereqs.sh
PYTHON_BIN=python3.14 bash infra/scripts/rhel9/rhel9-app-install.sh
```

The installer creates or reuses `.venv`, installs the hash-locked runtime
dependencies from `requirements.txt`, verifies `irp-integration==0.8.0`, tests
the configured Workbench connection, runs `alembic upgrade head` against only
that database, checks the Alembic head, and imports the application. It does
not run `bootstrap_db.py`.

Deploy the nginx server block with the same command used by the push script:

```bash
APP_ROOT=/rms envsubst '$APP_ROOT' < deploy/nginx/site.conf \
  | sudo tee /etc/nginx/conf.d/risk-workbench.conf >/dev/null
sudo systemctl reload nginx
```

## Create the first administrator

The CLI needs no `uv` or development packages:

```bash
APP_DIR=/rms bash infra/scripts/run_user_setup
```

Choose **Create password or OIDC user**, choose the `admin` role, and set a
temporary password for a password account. The account is marked to change the
password at first login. The same menu lists users, resets passwords, and
assigns a role to a pending OIDC user.

Password authentication does not yet enforce the rate limits specified in the
PRD. CinRe accepts the brute-force risk until that deferred requirement is
implemented.

## Start and verify

```bash
APP_DIR=/rms bash infra/scripts/rhel9/rhel9-start.sh
curl -sf http://127.0.0.1:8000/api/health
APP_DIR=/rms bash infra/scripts/rhel9/rhel9-worker-health.sh
curl -sf http://127.0.0.1/api/health
```

`/api/health` returns HTTP 200 only when Workbench and Valkey are available.
It returns HTTP 503 otherwise. Exposure and Loss statuses remain in the JSON
response but do not determine readiness.

## Repeated deployment

Keep the manual process boundary explicit:

1. While the old workers are running, wait for every queued job to finish:

   ```bash
   APP_DIR=/rms bash infra/scripts/rhel9/rhel9-drain-check.sh
   ```

2. Stop uvicorn, every worker, the poller, and Valkey:

   ```bash
   APP_DIR=/rms bash infra/scripts/rhel9/rhel9-stop.sh
   ```

3. From the deployment runner, push and install the new release:

   ```bash
   DEPLOY_HOST=cinreadm@rhel9.example \
   DEPLOY_DIR=/rms \
   SSH_KEY=~/.ssh/risk-workbench-deploy \
   bash infra/scripts/rhel9/rhel9-ssh-deploy.sh
   ```

   The push script refuses to overwrite application files while uvicorn, a
   worker, or the poller is running. It also verifies that no `rwb_job` remains
   pending or running. It does not start the application.

4. On RHEL9, start and verify the new release with the commands in the previous
   section.

The deployment scripts do not install systemd units. Process recovery after a
server restart remains an operator action.
