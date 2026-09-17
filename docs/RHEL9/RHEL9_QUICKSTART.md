# RHEL9 Quickstart

See [RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md) for database permissions,
network paths, environment variables, and failure handling.

## One-time setup

On RHEL9:

```bash
DEPLOY_USER=cinreadm APP_DIR=/rms bash rhel9-setup.sh
```

Place the production secrets file at `/rms/infra/.env`. Create
`rwb_workbench`, `rwb_exposure`, and `rwb_loss` before application install.
The installer will not create databases. Prepare the Exposure and Loss schemas
separately; only Workbench is managed by Alembic.

## First deployment

From the deployment runner:

```bash
DEPLOY_HOST=cinreadm@rhel9.example \
DEPLOY_DIR=/rms \
SSH_KEY=~/.ssh/risk-workbench-deploy \
bash infra/scripts/rhel9/rhel9-ssh-deploy.sh
```

On RHEL9:

```bash
APP_DIR=/rms bash /rms/infra/scripts/rhel9/rhel9-start.sh
curl -sf http://127.0.0.1:8000/api/health
APP_DIR=/rms bash /rms/infra/scripts/rhel9/rhel9-worker-health.sh
APP_DIR=/rms bash /rms/infra/scripts/run_user_setup
```

The user setup menu runs with `.venv/bin/python`; production does not need
`uv`, InquirerPy, or rich.

## Repeated deployment

On RHEL9, drain while the old workers are running and then stop:

```bash
APP_DIR=/rms bash /rms/infra/scripts/rhel9/rhel9-drain-check.sh
APP_DIR=/rms bash /rms/infra/scripts/rhel9/rhel9-stop.sh
```

Run `rhel9-ssh-deploy.sh` from the deployment runner, then start and verify on
RHEL9:

```bash
APP_DIR=/rms bash /rms/infra/scripts/rhel9/rhel9-start.sh
curl -sf http://127.0.0.1:8000/api/health
APP_DIR=/rms bash /rms/infra/scripts/rhel9/rhel9-worker-health.sh
```

The push script refuses to deploy while uvicorn, a worker, or the poller is
running. It installs exact PyPI `irp-integration==0.8.0`, migrates only the
configured Workbench database, reloads nginx with `deploy/nginx/site.conf`, and
leaves application startup to the operator.
