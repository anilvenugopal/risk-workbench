# RHEL9 System Setup

Installs the system-level packages Risk Workbench needs to run: git, Python
3.14, the Microsoft ODBC Driver 18 for SQL Server, Valkey (Redis), nginx,
build tools, and `rsync`.

Prerequisite: [RHEL9_WSL_INSTALL.md](RHEL9_WSL_INSTALL.md) completed —
registered RHEL9 distro, `cinreadm` created with sudo, locale fixed.

This covers system packages only. For `uv` and running the app for
development, see [RHEL9_DEV_SETUP.md](RHEL9_DEV_SETUP.md).

## Do this

Run the setup script, then the check script to confirm it worked:

```bash
DEPLOY_USER=cinreadm APP_DIR=/rms bash infra/scripts/rhel9/rhel9-setup.sh
```

Verify it worked with:
```bash
APP_DIR=/rms DEPLOY_USER=cinreadm PYTHON_PKG=python3.14 \
    bash infra/scripts/rhel9/rhel9-check-prereqs.sh
```

- [rhel9-setup.sh](../../infra/scripts/rhel9/rhel9-setup.sh) installs
  everything below. Safe to re-run — it checks state before acting.
- [rhel9-check-prereqs.sh](../../infra/scripts/rhel9/rhel9-check-prereqs.sh)
  is read-only — checks every package, command, permission, and (once
  `infra/.env` exists) network reachability. Safe to run repeatedly, from
  the server or a pipeline.

## Reference: what each step does and why

The sections below explain what `rhel9-setup.sh` does, one package at a
time.

---

## git

*Automated by rhel9-setup.sh section 2 ("System packages").*

Needed to get the code onto the box at all.

### Install

```bash
sudo dnf install -y git
```

### Verify

```bash
git --version
```

### Production considerations

**Decided**: this project's deploy mechanism is push-based —
[rhel9-ssh-deploy.sh](../../infra/scripts/rhel9/rhel9-ssh-deploy.sh) pushes code to
the server via `rsync` over SSH; the server never runs `git clone`/`git
pull` against GitHub, and never needs outbound internet access or GitHub
credentials. `git` is still installed on the server (for the separate,
manual/local `rhel9-pull-code.sh` flow, and general troubleshooting
convenience), but production deploys do not depend on it being there.

---

## rsync

*Automated by rhel9-setup.sh section 2 ("System packages").*

Needed on the **RHEL9 server itself**, not just the machine pushing code.
`rsync` over SSH is a client-*and*-server tool: the local `rsync` command
connects over SSH and launches a matching `rsync` process on the remote
end to negotiate what changed. Without it, a deploy from a machine that
already has `rsync` still fails with `rsync: command not found` on the
RHEL9 end.

### Install

```bash
sudo dnf install -y rsync
```

### Verify

```bash
rsync --version
```

---

## Application directory and ownership (`/rms`)

*Automated by rhel9-setup.sh section 5 ("Application directory").*

Where the application code and its data live, and who owns it.

### Install

For this run, `cinreadm` is the app's owner.

```bash
sudo mkdir -p /rms
sudo chown cinreadm:cinreadm /rms
```

---

## Python 3.14

*Automated by rhel9-setup.sh section 2 ("System packages").*

### Install — the interpreter

```bash
sudo dnf install -y python3.14 python3.14-devel
```

- `python3.14` — the interpreter itself, installed as `/usr/bin/python3.14`
  (does not replace or alias the system's default `python3` → 3.9; both
  coexist).
- `python3.14-devel` — headers and build files needed at compile time by
  Python packages with C extensions — this project needs it for `pyodbc`
  (SQL Server connectivity).

Pulled in automatically as dependencies: `libnsl2`, `libtirpc`, `mpdecimal`,
`pkgconf` and related packages, `python3.14-libs`.

### Install — pip

`python3.14` does **not** pull in `pip` automatically — RHEL splits it into
its own package:

```bash
sudo dnf install -y python3.14-pip
```

Pulls in `python3.14-setuptools` as a dependency.

`venv` needs no separate install — it ships as a standard-library module
inside the `python3.14` package itself.

### Verify

```bash
python3.14 --version
python3.14 -m pip --version
python3.14 -m venv --help     # prints venv's usage text
```

The exact patch version and pip version printed weren't captured when this
was verified — confirm them live rather than trusting a number written here.

### A note on `python3` / `python` aliasing — do not do this

It might seem natural to make `python3` (or a bare `python`) point at 3.14
system-wide via `alternatives`/`update-alternatives`. **Don't** — this project
uses `uv` (see [RHEL9_DEV_SETUP.md](RHEL9_DEV_SETUP.md)) to manage its virtual
environment, which finds or downloads the exact Python version a project
needs and builds an isolated `.venv/` with it, untouched by whatever the
system's `python3` symlink points to. Changing the system-wide default would
affect every user and script on the box for no benefit to this project, and
risks breaking other tooling that expects RHEL's default 3.9.

A personal, non-default `python` → `python3` shell alias (for typing
convenience only, in your own `~/.bashrc`) is fine and needs no sudo — RHEL
ships no bare `python` command by default. This is optional and unrelated to
the project's dependency management.

### Optional: install `uv`

`uv` is a dependency *installer*. Optionally install it to manage the project's virtual environment:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

See [RHEL9_DEV_SETUP.md](RHEL9_DEV_SETUP.md) for how the project uses `uv` day to day, and [RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md) for how a
`uv.lock`-built environment can be applied without `uv` present there, with a workaround.

---

## Microsoft ODBC Driver 18 for SQL Server

*Automated by rhel9-setup.sh sections 1 ("Microsoft's package repository")
and 3 ("Microsoft ODBC Driver 18"). Section 1 registers the repo before
section 2's package install runs — see that section's own comments for why
the ordering matters (`unixODBC-devel` needs the repo registered first).*

### Install — register Microsoft's repo

```bash
sudo curl -fsSL https://packages.microsoft.com/config/rhel/9/prod.repo -o /etc/yum.repos.d/mssql-release.repo
```

The RHEL equivalent of SCAFFOLDING.md's
`packages.microsoft.com/config/ubuntu/24.04/prod.list` step — a `.repo` file
(dnf/yum's config format) in `/etc/yum.repos.d/`, the directory `dnf` scans
for repo definitions. Microsoft publishes this RHEL9-specific repo directly;
no rebuild-compatibility guessing needed.

### Install — the driver

```bash
sudo ACCEPT_EULA=Y dnf install -y msodbcsql18
```

`ACCEPT_EULA=Y` is required non-interactively, same as the `apt` install.
First run prompts to import Microsoft's GPG key — accept it.

This pulls in `unixODBC` (the runtime, from RHEL's own AppStream repo) as a
dependency automatically. No CRB (CodeReady Builder) repo enablement is
needed for the base driver install.

### Install — the devel headers

Needed at compile time for `pyodbc` (this project's SQL Server connectivity
library). Available without CRB — it ships from Microsoft's own repo, not
RHEL's:

```bash
sudo dnf list available unixODBC-devel   # confirm before installing
sudo dnf install -y unixODBC-devel
```

Installing `unixODBC-devel` from Microsoft's repo (version `2.3.11-1.rh`)
causes `dnf` to *downgrade* the already-installed `unixODBC` runtime from
AppStream's `2.3.12-1.el9` to Microsoft's matching `2.3.11-1.rh`, so both
packages come from the same repo at the same version. This is expected, not
a conflict.

It also prints `warning: /etc/odbcinst.ini created as
/etc/odbcinst.ini.rpmnew` — `dnf` preserves the live config (which already
has the ODBC Driver 18 registration from the `msodbcsql18` install) rather
than overwriting it, and drops unixODBC's own default template alongside it
as `.rpmnew`. Verify nothing was lost:

```bash
cat /etc/odbcinst.ini          # should still contain [ODBC Driver 18 for SQL Server]
cat /etc/odbcinst.ini.rpmnew   # unixODBC's default template — empty or ignorable in our case
```

### Verify

```bash
odbcinst -q -d -n "ODBC Driver 18 for SQL Server"
```

Should print the driver block with
`Driver=/opt/microsoft/msodbcsql18/lib64/libmsodbcsql-18.6.so.2.1`. This is
the same lookup `pyodbc` performs at runtime via `MSSQL_DRIVER="ODBC Driver
18 for SQL Server"` in `.env` — confirming it here confirms the app will find
it too.

---

## Redis (Valkey)

*Package install automated by rhel9-setup.sh section 2 ("System
packages") — it installs `valkey`, per the decision below. Starting it
with the right flags (AOF, a writable `--dir`) is NOT yet scripted for
RHEL9 — see "This is already solved" further down for Ubuntu's existing
script and what an RHEL9 equivalent still needs.*

### Which one, and why

RHEL9.8 offers `redis` (6.2.22) and `valkey` (8.0.9) directly from AppStream.

```bash
sudo dnf list available redis valkey
```

**This project uses Valkey by default.**

- `redis` 6.2.22 in RHEL9 AppStream is frozen at a pre-license-change Redis
  release. Upstream Redis 6.2 itself reached end-of-life in August 2024 —
  RHEL9's `redis` package gets security backports only, no forward path.
- Red Hat's own RHEL9 release notes state Redis is being deprecated in favor
  of Valkey starting in RHEL9 and completed in RHEL10, and explicitly
  recommend migrating new deployments to `valkey`.
  ([RHEL9 Release Notes](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/9.8_release_notes/new-features))

### Install

```bash
sudo dnf install -y valkey
```

### WSL2-specific: shared networking between distros

If you also have an Ubuntu (or any other) WSL2 distro on the same Windows
machine, and WSL2 is running in **mirrored networking mode**, all WSL2
distros share one IP address and one port space with each other and the
Windows host — confirmed live: `ip addr show eth0` printed the identical
address on both RHEL9 and Ubuntu-26.04. This means Ubuntu's existing
`redis-server` will already hold port 6379 by the time you try to start Valkey on
RHEL9, and Valkey's startup fails with "Address already in use."

This is a WSL2/Windows networking property, not anything specific to Redis,
Valkey, or this project — it would affect any port both distros' dev stacks
try to bind (redis, the app itself on 80/8000, etc.) if you ever ran full
stacks in both distros at the same time.

**Resolution used on WSL2:** stop Ubuntu's Redis before starting RHEL9's, and
vice versa — don't run both distros' full stacks simultaneously on the same
ports. Chosen over switching WSL2 to NAT networking mode (which would give
each distro its own IP and avoid the conflict entirely) because a NAT switch
is a machine-wide WSL2 setting affecting every distro, not something scoped
to this project.

```powershell
# From Windows PowerShell, before starting RHEL9's Valkey:
wsl -d Ubuntu-26.04 -- sudo systemctl stop redis-server

# To resume Ubuntu development afterward:
wsl -d Ubuntu-26.04 -- sudo systemctl start redis-server
```

If you need both environments' full stacks running at once, switching WSL2
to NAT mode (`networkingMode=NAT` in `%UserProfile%\.wslconfig`, then
`wsl --shutdown` and restart) is the real fix — revisit if this becomes a
recurring interruption rather than a rare conflict.

### Memory overcommit

Valkey warns on startup if `vm.overcommit_memory` isn't set to `1` — without
it, a background save (which AOF rewrites depend on) can fail under memory
pressure. Fix once, persists across reboots:

```bash
echo 'vm.overcommit_memory = 1' | sudo tee -a /etc/sysctl.conf
sudo sysctl vm.overcommit_memory=1
```
### AOF needs a writable `dir` set at startup — not changeable live

Starting Valkey with no explicit directory defaults to whatever directory
you launched it from. Without a writable one, AOF fails outright:

```
Can't open or create append-only dir appendonlydir: Permission denied
```

`dir` is also a **protected config** — `CONFIG SET dir ...` on an
already-running server is rejected (`can't set protected config`), so this
can't be patched around after the fact. It must be set at launch, pointed at
a directory the running account already owns.

### Install — create the data directory

`/var/lib` is the standard Linux location for a service's own persistent
data — not a personal home directory, which would tie Valkey's data to one
account instead of the service account that should own it.

```bash
sudo mkdir -p /var/lib/risk-workbench/valkey
sudo chown -R cinreadm:cinreadm /var/lib/risk-workbench
```

Then start Valkey against it:

```bash
valkey-server --daemonize no --port 6379 \
  --dir /var/lib/risk-workbench/valkey \
  --appendonly yes --appendfsync everysec &
```

Verify:

```bash
valkey-cli ping                              # PONG
valkey-cli CONFIG GET dir                    # /var/lib/risk-workbench/valkey
valkey-cli CONFIG GET appendonly             # yes
ls -la /var/lib/risk-workbench/valkey/       # appendonlydir present
```

This directory is unrelated to the application code directory (`/rms`) —
Valkey's data is a service concern, not application code, so it stays under
`/var/lib` regardless of where the app itself is checked out.

---

## nginx

*Automated by rhel9-setup.sh section 2 ("System packages," installs
`nginx` and `gettext`) and section 6 ("nginx as a systemd service" —
enables/starts the service and grants the narrow, passwordless
`systemctl reload nginx` permission described below).*

### Install

```bash
sudo dnf install -y nginx gettext
```

`gettext` provides `envsubst`, needed below.

### The static-file path problem — fixed

[deploy/nginx/nginx.conf](../../deploy/nginx/nginx.conf) used to hardcode
`alias /workspace/app/static/;` — `/workspace` only exists inside the Docker
container. On any real checkout (RHEL9, Ubuntu native, or the production
server), that folder doesn't exist, so nginx can't find the app's CSS/JS/
image files. This was broken everywhere except Docker; it is now fixed.

**Fix:** the config file now uses a placeholder, `${APP_ROOT}`, instead of a
hardcoded path. Before starting nginx, run `envsubst` to fill in the real
checkout path for whichever environment you're in:

```bash
APP_ROOT=/home/cinreadm/risk-workbench envsubst '$APP_ROOT' \
    < deploy/nginx/nginx.conf > /tmp/nginx.conf
nginx -c /tmp/nginx.conf
```

Replace `/home/cinreadm/risk-workbench` with wherever the repo is actually
checked out. The Docker path (`infra/scripts/start-all.sh`) does this
automatically with `APP_ROOT=/workspace`.

### Install — enable as a systemd service

```bash
sudo systemctl enable nginx
sudo systemctl start nginx
```

### Install — passwordless reload and config write for the deploy user

The deploy user needs to reload nginx and write its config after every
deploy, without holding general sudo. Two narrow sudoers rules grant exactly
that, each restricted to one specific command:

```bash
echo 'cinreadm ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx' | \
    sudo tee /etc/sudoers.d/risk-workbench-nginx-reload > /dev/null
sudo chmod 440 /etc/sudoers.d/risk-workbench-nginx-reload
sudo visudo -c -f /etc/sudoers.d/risk-workbench-nginx-reload

echo 'cinreadm ALL=(root) NOPASSWD: /usr/bin/tee /etc/nginx/conf.d/risk-workbench.conf' | \
    sudo tee /etc/sudoers.d/risk-workbench-nginx-conf-write > /dev/null
sudo chmod 440 /etc/sudoers.d/risk-workbench-nginx-conf-write
sudo visudo -c -f /etc/sudoers.d/risk-workbench-nginx-conf-write

sudo visudo -c   # validate against the full sudoers configuration, not just these files
```

`visudo -c -f <file>` only checks that one file's syntax; `visudo -c` with no
file checks it against everything else already in effect. Both matter — a
rule can pass in isolation and still conflict with an existing one. If the
full check fails, remove the file rather than leave a rule that only passed
in isolation.

### Production considerations

- Run nginx as root or under a `systemd` unit (needed to bind port 80 — see
  "Application directory and ownership (`/rms`)" above for the same class of
  permission requirement), with `APP_ROOT` set to wherever the app is
  actually deployed (`/rms`, per the FHS layout discussed earlier).
- `gettext`/`envsubst` needs to be part of the production install request
  too — it's a small, standard package, but the config won't load without it.

---

## Build tools (gcc, g++)

*Automated by rhel9-setup.sh section 2 ("System packages").*

### Install

```bash
sudo dnf install -y gcc gcc-c++
```

AppStream packages (11.5.0). Pulls in `make` (4.3) as a dependency — no
separate install needed, though don't assume this on a minimal production
image without confirming.

### Verify

```bash
gcc --version    # gcc (GCC) 11.5.0
g++ --version    # g++ (GCC) 11.5.0
make --version   # GNU Make 4.3
```

---

## Optional: Podman + local SQL Server

Local dev/testing convenience only. Production's SQL Server is a separate,
already-existing instance outside this box, never containerized as part of
deployment. Not part of `rhel9-setup.sh` — three standalone scripts, run
only if you want RHEL9 to have its own SQL Server instead of reaching
across to Ubuntu's.

Same port (1433) as Ubuntu's Docker SQL Server, so `infra/.env` never needs
environment-specific values. Consequence: only one of the two can be
reachable at a time (same shared-IP conflict as Redis/Valkey above) — stop
one before starting the other. Diagnose with
[infra/scripts/check-port.sh](../../infra/scripts/check-port.sh).

### Order of operations

```bash
bash infra/scripts/rhel9/rhel9-setup.sh                    # once
cp <your .env> infra/.env                             # once
bash infra/scripts/rhel9/rhel9-setup-podman-mssql.sh         # once, if wanted
bash infra/scripts/rhel9/rhel9-start.sh                      # every session
bash infra/scripts/rhel9/rhel9-start-podman-mssql.sh         # every session, if wanted
```

`rhel9-setup-podman-mssql.sh` installs Podman and creates the SQL Server
container — it does not start it. `rhel9-start-podman-mssql.sh` starts it
and waits for it to accept connections; exits without error if Podman
isn't installed. `rhel9-stop-podman-mssql.sh` stops it the same way.

Podman is RHEL9's own container tool — ships directly in AppStream, no
daemon process, runs containers as your own user rather than root
("rootless"). Rootless mode needs a range of subordinate user/group IDs
(subuid/subgid) reserved for your account; `rhel9-setup-podman-mssql.sh`
checks for this and assigns it if missing.

**Confirmed**: this is normally already done for you. RHEL9's
`/etc/login.defs` sets `SUB_UID_COUNT`/`SUB_GID_COUNT`, so `useradd`
assigns every new account a subuid/subgid range automatically — see
[RHEL9_WSL_INSTALL.md](RHEL9_WSL_INSTALL.md) — no project script does this.

### `podman create` vs `podman start` vs `podman run`

- `podman create` builds a container (filesystem, network, config) without
  starting it.
- `podman start` starts an already-created container.
- `podman run` does both in one step — not used here, deliberately, so
  setup and start stay separate actions.

### Data directory

SQL Server's data lives at `/var/lib/risk-workbench/mssql`, bind-mounted
into the container — not a Podman-managed named volume (which would
default to `~/.local/share/containers/storage/volumes/...` under
`cinreadm`'s home directory). Same reasoning as Valkey's data directory
above: not tied to one personal account, survives `cinreadm` being
replaced by a real service account later. Stopping or removing the
container never touches this directory.

### Directory ownership for the container's internal user

SQL Server's container runs internally as UID `10001`, GID `0` (fixed by
the image, same on every machine). Rootless Podman maps that internal UID
to a host UID from `cinreadm`'s own subuid range (confirmed:
`165536 + 10001 - 1 = 175536` on this machine) — not to `cinreadm`'s real
UID. A plain `cinreadm`-owned directory is invisible to the container for
writing until this is fixed:

```bash
podman unshare chown -R 10001:0 /var/lib/risk-workbench/mssql
```

`podman unshare` runs the command inside the same UID mapping rootless
containers use, so `chown 10001:0` resolves to the correct real host UID
regardless of what `cinreadm`'s actual subuid range happens to be — the
`10001:0` numbers never need to change per machine.
`rhel9-setup-podman-mssql.sh` runs this automatically.

### Stopping does not shut down cleanly — known image limitation

This container's own entrypoint script
(`/opt/mssql/bin/launch_sqlservr.sh`, confirmed by reading it directly)
starts `sqlservr` as a background process and waits on it, with no signal
handler to forward SIGTERM down to it. `podman stop` always ends up
forcing a SIGKILL after its timeout, regardless of how long that timeout
is set to — this is a gap in the container image itself, not something a
longer wait or a different Podman flag fixes.

Accepted as-is for this local dev/testing convenience: SQL Server's own
crash-recovery journaling protects data through an unexpected stop, the
same mechanism that protects against a real power loss.

### `"/" is not a shared mount` warning — known WSL2 quirk, not a failure

Podman prints this on most rootless operations on this machine:

```
WARN[0000] "/" is not a shared mount, this could cause issues or missing mounts with rootless containers
```

WSL2 mounts its root filesystem with a mount-propagation mode other than
`shared`, which some rootless container features expect. This is a
property of how WSL2 itself boots, not something this project's scripts
configure. Confirmed harmless in practice: every setup/start/stop run
tonight showed this warning, and none of them actually failed because of
it — the container starts, permissions apply correctly, and data persists
as expected regardless. A real fix exists (`sudo mount --make-rshared /`)
but is unverified and not applied, since the warning hasn't caused an
actual problem.
