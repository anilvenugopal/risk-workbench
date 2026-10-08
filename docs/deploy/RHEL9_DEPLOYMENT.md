# RHEL9 Deployment Runbook

Deploys Risk Workbench on a RHEL9 server without `uv` — matching production,
where `uv` is a developer tool only (see [AGENTS.md](../../AGENTS.md)).

Prerequisite: [RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md) completed — git,
Python 3.14, ODBC Driver 18, Redis/Valkey, nginx, gcc/g++/make, rsync all
installed. Commands only: [RHEL9_QUICKSTART.md](RHEL9_QUICKSTART.md).

The account `cinreadmd` and the app directory `/rms` used below are the
values on the deployed server.

Steps 1-7 need no elevated privileges once the one-time infra setup below is
done — the deployment account never needs standing `sudo`.

---

## 0. One-time infra setup (before the first deployment)

Requested from infra once per server, not repeated per deployment:

1. Create the application directory (`rhel9-setup.sh` does this). Production
   uses one account to deploy and a second to run the app; no script creates
   them (see Open items).
2. Install nginx as a `systemd` service, enabled and running with a
   placeholder/default config — infra owns the unit file
   (`/etc/systemd/system/nginx.service` or the packaged default) and its
   `User=`/permissions.
3. Grant the deployment account two narrowly scoped `sudoers` entries, without
   full root — `rhel9-setup.sh` writes both:
   ```
   cinreadmd ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx
   cinreadmd ALL=(root) NOPASSWD: /usr/bin/tee /etc/nginx/conf.d/risk-workbench.conf
   ```
   These are the only privileged actions the deployment performs: writing
   that one nginx config file and reloading nginx.

With this in place, deploying a new nginx config becomes: write the file,
run `sudo systemctl reload nginx` (permitted by the narrow `sudoers` rule
above — no password, no broader access) — never `sudo nginx -c ...` as an
ad hoc process the deployment account owns and manages itself.

---

## 1. Verify prerequisites

```bash
APP_DIR=/rms DEPLOY_USER=cinreadmd PYTHON_PKG=python3.14 \
    bash infra/scripts/deploy/rhel9-check-prereqs.sh
```

[infra/scripts/deploy/rhel9-check-prereqs.sh](../../infra/scripts/deploy/rhel9-check-prereqs.sh)
confirms [RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md)'s one-time setup
(packages, `/rms` created and owned correctly, nginx
running with the reload permission granted) actually happened — read-only,
safe to run from the server or a pipeline, before touching any code.

## 2. Get the code

```bash
APP_DIR=/rms BRANCH=<branch-or-tag-to-deploy> \
    bash infra/scripts/deploy/rhel9-pull-code.sh
```

[infra/scripts/deploy/rhel9-pull-code.sh](../../infra/scripts/deploy/rhel9-pull-code.sh)
handles both a fresh clone (first deployment) and updating an existing
checkout (`git fetch`/checkout/pull). Refuses by default if it finds
local modifications to tracked files or untracked files sitting in the
directory — rerun with `--stash` (sets modified files aside safely,
recoverable with `git stash pop`) or `--force` (permanently discards
modified tracked files; never touches untracked files) once you've
reviewed what it found. Gitignored files (`infra/.env`, `.dev-logs/`, `.venv`)
never show up in this check at all — confirmed directly, not assumed.

A real CI/CD pipeline would more likely push a built artifact via `rsync`
over SSH rather than have the server `git pull` from GitHub directly — see
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md#git) for that open question.
This script covers the pull-based case either way.

## 3. Environment file

```bash
cp infra/.env.example infra/.env
```

Edit `infra/.env` and fill in the real values for this environment
(`SESSION_SECRET_KEY`, `MSSQL_*_PASSWORD`, `ENTRA_*`, etc.). Never commit this
file. Placed once, manually — no script generates or overwrites it.

`MSSQL_*_SERVER` must be a hostname this server can actually resolve —
`sqlserver` (Docker Compose's internal DNS name for the container) only
works from inside Docker's network. Connecting directly from the host, use
the real reachable hostname or IP (e.g. `127.0.0.1` if SQL Server's port is
mapped to localhost).

## 4. Install dependencies and run migrations

```bash
PYTHON_BIN=python3.14 bash infra/scripts/deploy/rhel9-app-install.sh
```

[infra/scripts/deploy/rhel9-app-install.sh](../../infra/scripts/deploy/rhel9-app-install.sh)
builds/updates `.venv`, installs from `requirements.txt` (committed to git
by developers — see
[infra/scripts/generate-requirements.sh](../../infra/scripts/generate-requirements.sh)
— never generated or transferred by hand), and runs `alembic upgrade
head`. No `uv` involved at any point on the server.

`requirements.txt`'s `-e .` line (a reference to the project's own code,
not a downloadable package) is stripped before the hash-verified install —
confirmed this can't be avoided by asking `uv export` to skip it
differently (`--no-editable` still produces an unhashed local-path line
for the same structural reason); see the script's own comments for the
full reasoning.

The server needs HTTPS access to PyPI during installation unless the
packages are staged on the server before the deployment.

The three application databases must already exist. `rhel9-app-install.sh`
does not create databases; it applies the Workbench Alembic migrations and
verifies `pyodbc` can see `ODBC Driver 18 for SQL Server` and that
`app.config` imports cleanly.

Revision `0005` loads the cedant list from `db/bootstrap/seed/cedants.xlsx`
once. The committed file holds only its `Cedant` header until the client's
list arrives, so `0005` adds no cedants. When the client sends a list, replace
that file and run
`cd /rms && set -a && source infra/.env && set +a && .venv/bin/python infra/scripts/load_cedants.py`;
it adds only the names not already in the `cedant` table. The script reads
the database settings from the environment, not from `infra/.env`, so the
`source` is needed.

## 5. Start Redis/Valkey

```bash
valkey-server \
    --daemonize yes \
    --logfile /var/lib/risk-workbench/valkey/valkey.log \
    --bind 127.0.0.1 \
    --appendonly yes \
    --appendfsync everysec \
    --dir /var/lib/risk-workbench/valkey
```

`--dir` must point at a directory the running account owns — `dir` cannot be
changed on a running server (`CONFIG SET dir` is rejected as a protected
config), so get this right at launch. `/var/lib/risk-workbench/valkey` is
created and owned correctly by
[rhel9-setup.sh](../../infra/scripts/deploy/rhel9-setup.sh) section 7 — `/var/lib` is
the standard Linux location for a service's own persistent data, not a
personal user's home directory (early manual testing used
`/home/cinreadmd/valkey-data`; corrected here since a home directory ties
the data to one specific account, and `/var/lib` itself is root-owned the
same way `/opt` is — confirmed directly with `ls -ld /var/lib` — so the
one-time `mkdir`+`chown` needs `sudo`, same pattern as the app directory).
Production may substitute `redis` for `valkey` — see
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md#redis-valkey) for why either
works with zero code changes.

**Verify:**

```bash
valkey-cli ping                    # PONG
valkey-cli CONFIG GET appendonly   # yes
```

## 6. Start the app

```bash
set -a && source infra/.env && set +a
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

This step proves the app itself starts and serves correctly. After it,
`rhel9-start.sh` starts uvicorn, the Dramatiq workers, the poller, and Valkey
with `nohup`. Production does not use systemd units for them: the team that
maintains the Linux server runs `rhel9-start.sh` after every server restart.

## 7. Deploy the nginx config and reload

Binding port 80 needs root — but with the one-time infra setup (Step 0)
done, the deployment account never runs nginx itself; it only writes the
config file and triggers the pre-authorized reload:

```bash
APP_ROOT=/rms envsubst '$APP_ROOT' \
    < deploy/nginx/site.conf | sudo tee /etc/nginx/conf.d/risk-workbench.conf > /dev/null
sudo systemctl reload nginx
```

`sudo tee` and `sudo systemctl reload nginx` are the two commands the narrow
`sudoers` rules from Step 0 permit — no password, no broader access, and no ad hoc `nginx
-c ...` process for the deployment account to own and manage itself.

`site.conf` (a `conf.d` server block, not the full `nginx.conf` the Docker dev
container runs) uses an `${APP_ROOT}` placeholder for the static-file path —
substitute the real deployment path here. `envsubst` (part of the `gettext`
package) must be installed — see
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md#nginx).

**Dry-run note**: without Step 0's systemd unit in place, this runbook was
exercised with `sudo nginx -c /tmp/nginx-risk-workbench.conf` directly —
confirms the same config and `${APP_ROOT}` substitution work, but is not the
deployment-repeatable form described above.

**Verify:**

```bash
curl -s http://127.0.0.1:80/api/health
curl -sI http://127.0.0.1:80/static/css/app.css   # expect 200 OK
```

Confirmed end to end: nginx proxying to uvicorn, and serving the real app's
static files through the `${APP_ROOT}`-substituted path — not just the
config's syntax checking out.

---

## Push-based deployment (SSH, for CI/CD or repeated deploys)

Steps 1-4 above (prerequisites, code, environment, install/migrate) plus
the nginx reload from step 7 collapse into one script, meant to run from a
dev machine or CI/CD runner — never on RHEL9 itself:

```bash
DEPLOY_HOST=cinreadmd@<rhel9-ip> \
DEPLOY_DIR=/rms \
SSH_KEY=~/.ssh/risk-workbench-deploy \
bash infra/scripts/deploy/rhel9-ssh-deploy.sh
```

See [RHEL9_SSH_KEY_SETUP.md](RHEL9_SSH_KEY_SETUP.md) for generating and
installing the key this script authenticates with.

[infra/scripts/deploy/rhel9-ssh-deploy.sh](../../infra/scripts/deploy/rhel9-ssh-deploy.sh)
does, over SSH:

1. Checks that `infra/.env` exists on the server — stops here if not.
2. Pushes code via `rsync --delete-after`, using `--filter=':- .gitignore'` — reads
   `.gitignore` directly so gitignored files (`infra/.env`, `.dev-logs/`, `.venv`,
   generated data) are never candidates for deletion, without needing a
   separately-maintained exclude list that could fall out of date.
   Confirmed directly with a dry run (`rsync -n`) before ever using
   `--delete` for real: only git-tracked files appeared in the transfer
   plan.
3. Runs `rhel9-check-prereqs.sh` **remotely** — it runs after the push
   because the script only exists on the server once the code is there.
4. Runs `rhel9-drain-check.sh` **remotely** and stops if `rwb_job` rows are
   still `pending` or `running` after `DRAIN_TIMEOUT_SECS` (default 300).
5. Runs `rhel9-app-install.sh` **remotely** — same script used by the
   local/manual flow; it doesn't care how code arrived (`git pull` or
   `rsync` push), only that it's already there.
6. Writes `site.conf` to `/etc/nginx/conf.d/risk-workbench.conf` and reloads
   nginx **remotely**, using the pre-authorized, no-password commands from Step 0.
7. Hits the health check endpoint and reports the result.

This deliberately does **not** call `rhel9-pull-code.sh` — that script is
for the separate, local/manual "log into the server and `git pull`
yourself" flow (steps 1-4 above, run individually). RHEL9 does not need
GitHub access or GitHub credentials with this script because it receives
the application files through `rsync`. The dependency installation still
needs PyPI access unless the packages are staged on the server.

**Requires `rsync` installed on RHEL9 itself**, not just the pushing
machine — confirmed the hard way on the first real attempt (`rsync:
command not found` on the remote side) — see
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md#rsync).

**Does not yet restart the running application.** The health check at the
end reports whatever's currently running (if anything) — it does not
prove the newly-installed code has actually taken effect, since restarting
uvicorn/the Dramatiq worker/the poller is a manual step: `rhel9-stop.sh`
before the deploy, `rhel9-start.sh` after it.

---

## Open items — not yet resolved

**Resolved by CR-004 (worker isolation + queue drain):** `rhel9-start.sh`/
`rhel9-stop.sh` now run one Dramatiq worker process per `rwb_job_type`
(`-Q <queue>` each), not one process handling every job type — a
long-running job of one type can no longer delay a job of a different type.
Each queue gets its own `worker-<queue>.pid`/`worker-<queue>.log`. The queue
list is derived from the code (`python -m app.workers.queues`), never
hand-copied into either script. `rhel9-worker-health.sh` reports each
queue's live/dead state (PID-file + independent process-scan) for
before/after inspection around a start or stop. The deploy script does not
stop or start the app, so an operator deploys in this order:

1. Run `APP_DIR=/rms bash infra/scripts/deploy/rhel9-drain-check.sh` on the
   server while the workers are still running, so `pending` and `running`
   `rwb_job` rows can finish.
2. Stop the app with `rhel9-stop.sh`.
3. Run `rhel9-ssh-deploy.sh`.
4. Start the app with `rhel9-start.sh`.

`rhel9-ssh-deploy.sh` runs its own drain check (step 4 above), but only after
rsync has replaced the code and after the operator has stopped the workers.
A `pending` row left at stop time can never clear then, and the deploy stops
after `DRAIN_TIMEOUT_SECS`.

- **Accounts**: this runbook uses `cinreadmd` to both deploy and run the
  app. Production uses one account to deploy (owns the app directory, holds
  the deploy SSH key and the two nginx sudoers rules) and a second to run
  the app (owns `/var/lib/risk-workbench`, runs `rhel9-start.sh` and
  `rhel9-stop.sh`). Get both names from infra. `rhel9-setup.sh` takes one
  `DEPLOY_USER` for both today.
- **Code delivery mechanism**: the push-based script exists
  (`rhel9-ssh-deploy.sh`, sharing `rhel9-app-install.sh`) and is the chosen
  path. Still to confirm with infra: whether the CI/CD runner or a developer
  machine may SSH to the server, and whether the server can reach PyPI for
  `pip install` or the packages must be staged.
- **Governed file sync for a push-based deploy**: done — `rhel9-ssh-deploy.sh`
  runs `rsync --delete-after --filter=':- .gitignore'`, so only git-tracked
  files are ever deleted.

## Next

[ENTRA_SETUP.md](../ENTRA_SETUP.md), then the first admin with
[USER_PROVISIONING.md](../USER_PROVISIONING.md).
