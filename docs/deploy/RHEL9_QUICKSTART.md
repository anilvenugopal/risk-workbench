# RHEL9 Quickstart

Commands to set up the RHEL9 server, deploy a new version, and start and stop
the app. The accounts, databases, firewall rules and settings CIC provides
first are on the
[deployment checklist](../handover/10-deployment-checklist.html). Local
development, including a WSL2 RHEL9 distro, is in
[LOCAL_DEV_SETUP.md](../LOCAL_DEV_SETUP.md).

Run each command on the server unless the step says your machine. Log in to
the server with SSH as your own account, for example `ssh bbaile4@<server>`. `cinreadmd` is the account that runs the app
and `/rms` is the app directory on the deployed server.

## Do I need an SSH key?

No. You use SSH only to log in to the server and `scp` to upload the code,
both as your own account.

[RHEL9_SSH_KEY_SETUP.md](RHEL9_SSH_KEY_SETUP.md) sets up a key for
`rhel9-ssh-deploy.sh`, a script that deploys from a developer machine or CI
runner without anyone logging in. Nobody runs it today: there is no CD process
(C4), and its package install needs PyPI, which the server cannot reach (C5).
Both are on
[handover page 9](../handover/09-path-to-production.html#cleanup).

## 1. One-time server setup

Do this once per server.

1. From a clone of the repository on your machine, upload the setup script to
   your home directory on the server:

   ```bash
   scp "C:\path\to\risk-workbench\infra\scripts\deploy\rhel9-setup.sh" bbaile4@<server>:~
   ```

2. On the server, as your own account, install the system packages. This
   needs sudo:

   ```bash
   DEPLOY_USER=cinreadmd APP_DIR=/rms bash ~/rhel9-setup.sh
   ```

   It installs git, Python 3.14, ODBC Driver 18, nginx, Valkey, gcc, gettext
   and rsync. It creates `/rms`, owned by `cinreadmd`, and lets `cinreadmd`
   write the nginx config and reload nginx with sudo. Details:
   [RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md).

3. On your machine, zip the repository folder, not its contents, to
   `risk-workbench.zip`. Leave out `.venv`. Upload it and make it readable by
   `cinreadmd`:

   ```bash
   scp "C:\path\to\risk-workbench.zip" bbaile4@<server>:/rms
   ssh bbaile4@<server> chmod 644 /rms/risk-workbench.zip
   ```

4. On the server, switch to the run account and copy the code into `/rms`.
   Removing `infra/.env` from the unzipped copy keeps `cp` from overwriting
   the server's own settings file:

   ```bash
   su cinreadmd
   unzip -o /rms/risk-workbench.zip -d /tmp/risk-workbench-deploy
   rm -rf /tmp/risk-workbench-deploy/risk-workbench/.git
   rm -f /tmp/risk-workbench-deploy/risk-workbench/infra/.env
   cp -a /tmp/risk-workbench-deploy/risk-workbench/. /rms/
   rm -rf /tmp/risk-workbench-deploy /rms/risk-workbench.zip
   ```

   Run the rest of section 1 as `cinreadmd`.

5. Create `/rms/infra/.env` from `infra/.env.example` with the server's real
   values, and make it readable by `cinreadmd` only:

   ```bash
   cp /rms/infra/.env.example /rms/infra/.env
   chmod 600 /rms/infra/.env
   ```

   The CIC SQL Server uses port 4988, so set `MSSQL_WORKBENCH_PORT` and
   `MSSQL_LOSS_PORT` to 4988. Every setting:
   [handover page 7](../handover/07-configuration-and-observability.html#env-reference).

6. Check that the Workbench database and the loss repository exist on the CIC
   SQL Server. No script creates them.

7. Build `.venv`, install the Python packages, and apply the Alembic revisions:

   ```bash
   cd /rms && PYTHON_BIN=python3.14 bash infra/scripts/deploy/rhel9-app-install.sh
   ```

   This step downloads every package from PyPI, which the deployed server
   cannot reach (C5). Get approval to let the server reach `pypi.org` and
   `files.pythonhosted.org` on port 443, run the command above, then close
   the rule again.

8. Write the nginx config and reload nginx:

   ```bash
   cd /rms
   APP_ROOT=/rms envsubst '$APP_ROOT' < deploy/nginx/site.conf \
       | sudo tee /etc/nginx/conf.d/risk-workbench.conf > /dev/null
   sudo systemctl reload nginx
   ```

   On the deployed server the app cannot be reached through nginx on port 80
   yet (C2). Analysts use port 8000.

9. Start the app (section 3).

10. Create the first admin with
    [USER_PROVISIONING.md](../USER_PROVISIONING.md).

## 2. Deploy a new version

1. Check that no job is running: Workflows → RWB Jobs in the app, or, as
   `cinreadmd`:

   ```bash
   APP_DIR=/rms bash /rms/infra/scripts/deploy/rhel9-drain-check.sh
   ```

2. Follow
   [Manual deployment on handover page 8](../handover/08-deployed-environment.html#manual-deployment).

The manual deployment installs no Python packages. If a package version in
`requirements.txt` changed, copy that package's wheel to the server before
starting the app:

1. On your machine, which can reach PyPI, download the wheel:

   ```powershell
   py -m pip download --only-binary=:all: --no-deps --dest C:\Users\<you>\Desktop\wheels <package>==<version>
   ```

2. Upload it to the server. `/rms/deploy/wheels` is not in the repository,
   so on a new server run `mkdir -p /rms/deploy/wheels` there first.

   ```powershell
   scp C:\Users\<you>\Desktop\wheels\<wheel file> bbaile4@<server>:/rms/deploy/wheels/
   ```

3. On the server, as `cinreadmd`, install it into `.venv`:

   ```bash
   cd /rms && .venv/bin/python -m pip install --no-index --no-deps --upgrade \
       deploy/wheels/<wheel file>
   ```

A wheel downloaded on Windows installs on the server only if its file name
ends in `py3-none-any.whl`, which means it is pure Python. `--no-deps`
installs only that package, so if the new version needs a package the server
does not have yet, copy that package's wheel the same way.

## 3. Start, stop and check the app

As `cinreadmd`, from `/rms`:

```bash
APP_DIR=/rms bash infra/scripts/deploy/rhel9-start.sh
APP_DIR=/rms bash infra/scripts/deploy/rhel9-stop.sh
```

`rhel9-start.sh` starts Valkey, uvicorn, one Dramatiq worker per queue, and
the poller. It refuses to start if port 8000 is in use, so stop first.

Check the app after a start:

```bash
curl -s http://127.0.0.1:8000/api/health
APP_DIR=/rms bash infra/scripts/deploy/rhel9-worker-health.sh
```

`/api/health` should show `ok` for `db_workbench`, `db_loss` and `redis`.
`rhel9-worker-health.sh` lists each queue and whether its worker is running.

Follow a log:

```bash
APP_DIR=/rms bash infra/scripts/deploy/rhel9-logs-worker.sh upload_edm
bash infra/scripts/deploy/rhel9-logs-poller.sh
```

Run `rhel9-logs-worker.sh` with no queue name to list the queues. Every other
operation, such as a dead worker or a stuck job, is in the
[operations runbook on handover page 8](../handover/08-deployed-environment.html#runbook).
