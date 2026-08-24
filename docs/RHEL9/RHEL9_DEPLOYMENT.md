# RHEL9 Deployment Runbook

Deploys Risk Workbench on a RHEL9 server without `uv` — matching production,
where `uv` is a developer tool only (see [AGENTS.md](../../AGENTS.md)).

Prerequisite: [RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md) completed — git,
Python 3.14, ODBC Driver 18, Redis/Valkey, nginx, gcc/g++/make, rsync all
installed.

Placeholders below (`dev-user`, `/opt/risk-workbench`) stand in for whatever
account and path infra actually assigns — substitute the real values when
deploying for real.

Steps 1-7 need no elevated privileges once the one-time infra setup below is
done — the deployment account never needs standing `sudo`.

---

## 0. One-time infra setup (before the first deployment)

Requested from infra once per server, not repeated per deployment:

1. Create the application directory and service account (see Step 1 below).
2. Install nginx as a `systemd` service, enabled and running with a
   placeholder/default config — infra owns the unit file
   (`/etc/systemd/system/nginx.service` or the packaged default) and its
   `User=`/permissions.
3. Grant the deployment account permission to reload (not start/stop/edit)
   that one unit, without full root — e.g. a narrowly scoped `sudoers` entry:
   ```
   dev-user ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx
   ```
   or the equivalent via Polkit. This is the only privileged action the
   deployment ever performs, and it's scoped to exactly one command.

With this in place, deploying a new nginx config becomes: write the file,
run `sudo systemctl reload nginx` (permitted by the narrow `sudoers` rule
above — no password, no broader access) — never `sudo nginx -c ...` as an
ad hoc process the deployment account owns and manages itself.

---

## 1. Verify prerequisites

```bash
APP_DIR=/opt/risk-workbench DEPLOY_USER=dev-user PYTHON_PKG=python3.14 \
    bash infra/scripts/rhel9/rhel9-check-prereqs.sh
```

[infra/scripts/rhel9/rhel9-check-prereqs.sh](../../infra/scripts/rhel9/rhel9-check-prereqs.sh)
confirms [RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md)'s one-time setup
(packages, `/opt/risk-workbench` created and owned correctly, nginx
running with the reload permission granted) actually happened — read-only,
safe to run from the server or a pipeline, before touching any code.

## 2. Get the code

```bash
APP_DIR=/opt/risk-workbench BRANCH=<branch-or-tag-to-deploy> \
    bash infra/scripts/rhel9/rhel9-pull-code.sh
```

[infra/scripts/rhel9/rhel9-pull-code.sh](../../infra/scripts/rhel9/rhel9-pull-code.sh)
handles both a fresh clone (first deployment) and updating an existing
checkout (`git fetch`/checkout/pull). Refuses by default if it finds
local modifications to tracked files or untracked files sitting in the
directory — rerun with `--stash` (sets modified files aside safely,
recoverable with `git stash pop`) or `--force` (permanently discards
modified tracked files; never touches untracked files) once you've
reviewed what it found. Gitignored files (`infra/.env`, logs, `.venv`)
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
PYTHON_BIN=python3.14 bash infra/scripts/rhel9/rhel9-app-install.sh
```

[infra/scripts/rhel9/rhel9-app-install.sh](../../infra/scripts/rhel9/rhel9-app-install.sh)
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

irp-integration's pinned version currently lives on TestPyPI rather than
PyPI — the script's `--extra-index-url` accounts for this; see AGENTS.md's
irp-integration section for why, and the Open items below for reconciling
it with PyPI as the stated production default.

`rhel9-app-install.sh` also creates the app's 3 databases (skips existing)
and verifies `pyodbc` can see `ODBC Driver 18 for SQL Server` and that
`app.config` imports cleanly — no separate steps needed for any of this.

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
[rhel9-setup.sh](../../infra/scripts/rhel9/rhel9-setup.sh) section 7 — `/var/lib` is
the standard Linux location for a service's own persistent data, not a
personal user's home directory (early manual testing used
`/home/dev-user/valkey-data`; corrected here since a home directory ties
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
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Production runs this (and the Dramatiq workers, and the poller) as systemd
units, not a foreground shell command — unit files are not yet written; this
step proves the app itself starts and serves correctly first.

## 7. Deploy the nginx config and reload

Binding port 80 needs root — but with the one-time infra setup (Step 0)
done, the deployment account never runs nginx itself; it only writes the
config file and triggers the pre-authorized reload:

```bash
APP_ROOT=/opt/risk-workbench envsubst '$APP_ROOT' \
    < deploy/nginx/nginx.conf > /etc/nginx/conf.d/risk-workbench.conf
sudo systemctl reload nginx
```

`sudo systemctl reload nginx` is the one command the narrow `sudoers` rule
from Step 0 permits — no password, no broader access, and no ad hoc `nginx
-c ...` process for the deployment account to own and manage itself.

`nginx.conf` uses an `${APP_ROOT}` placeholder for the static-file path —
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
DEPLOY_HOST=dev-user@<rhel9-ip> \
DEPLOY_DIR=/opt/risk-workbench \
SSH_KEY=~/.ssh/risk-workbench-deploy \
bash infra/scripts/rhel9/rhel9-ssh-deploy.sh
```

See [RHEL9_SSH_KEY_SETUP.md](RHEL9_SSH_KEY_SETUP.md) for generating and
installing the key this script authenticates with.

[infra/scripts/rhel9/rhel9-ssh-deploy.sh](../../infra/scripts/rhel9/rhel9-ssh-deploy.sh)
does, over SSH:

1. Runs `rhel9-check-prereqs.sh` **remotely** on RHEL9 — stops here if
   anything's missing.
2. Pushes code via `rsync`, using `--filter=':- .gitignore'` — reads
   `.gitignore` directly so gitignored files (`infra/.env`, logs, `.venv`,
   generated data) are never candidates for `--delete`, without needing a
   separately-maintained exclude list that could fall out of date.
   Confirmed directly with a dry run (`rsync -n`) before ever using
   `--delete` for real: only git-tracked files appeared in the transfer
   plan.
3. Runs `rhel9-app-install.sh` **remotely** — same script used by the
   local/manual flow; it doesn't care how code arrived (`git pull` or
   `rsync` push), only that it's already there.
4. Reloads nginx **remotely**, using the pre-authorized, no-password
   command from Step 0.
5. Hits the health check endpoint and reports the result.

This deliberately does **not** call `rhel9-pull-code.sh` — that script is
for the separate, local/manual "log into the server and `git pull`
yourself" flow (steps 1-4 above, run individually). RHEL9 never needs
outbound internet access or GitHub credentials with this script, since it
receives files pushed to it rather than fetching them itself.

**Requires `rsync` installed on RHEL9 itself**, not just the pushing
machine — confirmed the hard way on the first real attempt (`rsync:
command not found` on the remote side) — see
[RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md#rsync).

**Does not yet restart the running application.** The health check at the
end reports whatever's currently running (if anything) — it does not
prove the newly-installed code has actually taken effect, since restarting
uvicorn/the Dramatiq worker/the poller is still a manual step (systemd
units and the Dramatiq drain-before-restart mechanism are both deliberately
unbuilt — see Open items).

---

## Open items — not yet resolved

- **Dramatiq queue drain before a redeploy**: stopping the worker
  mid-job (rather than letting it finish first) is not yet handled by any
  script. The mechanism itself is understood — SIGTERM to the worker
  process, then poll `SELECT COUNT(*) FROM rwb_job WHERE status_code =
  'running'` until it reaches zero (or a timeout) instead of guessing a
  fixed wait — but it isn't built yet. Deliberately deferred; revisit
  before any script actually restarts a live deployment's worker process.
- **systemd unit files** for uvicorn, Dramatiq workers, the poller, and
  Valkey — not yet written; Steps 5-6 above run them in the
  foreground/manually as a proof of concept only. nginx's privilege problem
  (Step 7) is resolved in principle by Step 0's one-time infra setup — a
  pre-authorized `systemctl reload nginx` — but the other four processes
  need the same treatment: real unit files, owned and started by infra
  under the service account, not run ad hoc by the deployment account.
- **Service account**: this runbook uses a personal account as a
  placeholder. Production needs a dedicated, non-personal service account —
  get the real name from infra before finalizing any unit file that
  references one.
- **Code delivery mechanism**: step 2's `rhel9-pull-code.sh` assumes the
  server can reach GitHub directly (outbound internet + credentials), which
  many corporate servers won't have. Confirm with infra whether code
  arrives via CI/CD artifact (pushed via `rsync`/`scp` over SSH) instead —
  the script covers the pull-based case; the push-based case still needs
  its own script, sharing `rhel9-app-install.sh` for the install step
  rather than duplicating it.
- **Governed file sync for a push-based deploy**: once a push-based script
  exists, it must only ever delete files git tracks — never touch anything
  gitignored (`infra/.env`, logs, `.venv`, generated data). `rsync
  --filter=':- .gitignore'` was identified as the correct mechanism (reads
  `.gitignore` directly, rather than a hand-maintained exclude list that
  can silently fall out of date) but not yet implemented in any script.
- **irp-integration source**: this runbook installs from TestPyPI to match
  what the current lockfile resolves to. AGENTS.md states PyPI `0.2.0` as
  the production default — reconcile which source production actually uses
  before deploying for real.
