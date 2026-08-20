# RHEL9 System Setup

Installs the system-level packages Risk Workbench needs to run: git, Python
3.12, the Microsoft ODBC Driver 18 for SQL Server, Redis, nginx, and build
tools.

Each step is tagged:

- **[WSL: do yourself]** — run it yourself in your RHEL9 WSL2 distro; no
  approval needed, it's your own machine.
- **[Prod: request from infra]** — the equivalent action needed on the real
  production RHEL9 server, which you won't have standing sudo for. Submit
  this as a request rather than assuming it's already done.

Prerequisite: [RHEL9_WSL_INSTALL.md](RHEL9_WSL_INSTALL.md) completed —
registered RHEL9 distro, `dev-user` created with sudo, locale fixed.

This covers system packages only. For `uv` and running the app for
development, see [RHEL9_DEV_SETUP.md](RHEL9_DEV_SETUP.md).

---

## git

Needed to get the code onto the box at all — a genuine system-level
prerequisite, not a dev-setup nicety.

### [WSL: do yourself] / [Prod: request from infra]

```bash
sudo dnf install -y git
```

Direct AppStream package, no surprises expected.

### Verify

```bash
git --version
```

### Production considerations

Confirm with infra how the production server actually receives code —
`git clone`/`git pull` directly against GitHub assumes outbound internet
access and GitHub credentials configured on the server, which many
corporate RHEL9 servers won't have. A CI/CD pipeline that pushes a built
artifact to the server (rather than the server pulling from GitHub itself)
may not need `git` installed on the production server at all — this is a
[RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md) decision, not something to assume
either way.

---

## Application directory and ownership (`/opt/risk-workbench`)

Where the application code and its data actually live, and who owns it —
easy to do ad hoc and forget to write down, so captured here explicitly.

**Confirmed earlier:** `/opt` itself is root-owned; a plain user cannot
create anything under it without `sudo` (see "`/opt` ownership for the
application directory" below for the permission-denied proof).

### [WSL: do yourself] — dry run, using `dev-user` as the account

For this dry run, `dev-user` doubles as the app's owner — no dedicated
service account was created. **This is deliberate, not an oversight**: the
real production account name is infra's to assign, unknown until then, and
will need substituting in regardless of what we pick here. Using `dev-user`
now avoids inventing a throwaway name that has to be replaced later anyway.

```bash
sudo mkdir -p /opt/risk-workbench
sudo chown dev-user:dev-user /opt/risk-workbench
```

### [Prod: request from infra]

Request `/opt/risk-workbench` (or infra's preferred path, if they don't
follow this FHS convention) be created, owned by **a dedicated service
account** — not a personal developer account, and not literally `dev-user`.
The request needs:

- The directory path.
- Confirmation of which account should own it (infra assigns this; get the
  exact name before finalizing any deployment script or systemd unit that
  references it).
- Whether that account needs a login shell at all, or should be locked down
  (`nologin`) since it only ever runs as a systemd service, never interactively.

---

## Python 3.12

**Why 3.12 specifically:** `pyproject.toml` pins `requires-python = ">=3.12"`
— a hard requirement, not a preference. RHEL9's own default Python is 3.9.

**Finding it:** RHEL9 dropped the module-stream approach RHEL8 used for
alternate Python versions. As of RHEL9.8, `python3.12` is a plain, direct
package in the AppStream repo — no EPEL, no module enablement needed.
Confirmed by listing what's actually available before installing anything:

```bash
sudo dnf list available 'python3.1*'
```

This showed `python3.11`, `python3.12`, and even `python3.14` all available
directly, alongside the system default 3.9.

### [WSL: do yourself] / [Prod: request from infra] — install the interpreter

```bash
sudo dnf install -y python3.12 python3.12-devel
```

- `python3.12` — the interpreter itself, installed as `/usr/bin/python3.12`
  (does not replace or alias the system's default `python3` → 3.9; both
  coexist).
- `python3.12-devel` — headers and build files needed at compile time by
  Python packages with C extensions — this project needs it for `pyodbc`
  (SQL Server connectivity).

Pulled in automatically as dependencies: `libnsl2`, `libtirpc`, `mpdecimal`,
`pkgconf` and related packages, `python3.12-libs`.

### [WSL: do yourself] / [Prod: request from infra] — install pip

`python3.12` does **not** pull in `pip` automatically — RHEL splits it into
its own package:

```bash
sudo dnf install -y python3.12-pip
```

Pulls in `python3.12-setuptools` as a dependency.

`venv` needs no separate install — it ships as a standard-library module
inside the `python3.12` package itself.

### Verify

```bash
python3.12 --version          # Python 3.12.13
python3.12 -m pip --version   # pip 23.2.1 from .../python3.12/site-packages/pip
python3.12 -m venv --help     # prints venv's usage text
```

### A note on `python3` / `python` aliasing — do not do this

It might seem natural to make `python3` (or a bare `python`) point at 3.12
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

### Production considerations

- This WSL box is registered under free Red Hat Developer
  Subscription. The production server will be registered separately — likely
  against a corporate Red Hat account or an internal Satellite server, not
  your individual subscription. Confirm with infra how the server is
  registered before assuming `dnf install` will resolve `python3.12` the same
  way; if the server draws from a mirrored/internal repo rather than Red
  Hat's public CDN directly, `python3.12` needs to be present in whatever
  repo set infra mirrors.
- The request to infra should be the **exact command**
  (`dnf install -y python3.12 python3.12-devel python3.12-pip`), not just
  "install Python 3.12" — the package names, and the fact that `pip` and
  `devel` are separate packages RHEL doesn't bundle automatically, are easy
  to lose in translation through a ticket.
- Confirm with infra whether `python3.12` is already present on the target
  server (some golden images pre-install multiple Python versions) before
  filing a request — don't assume it's missing just because RHEL9's default
  is 3.9.

### `uv` is not part of this document

`uv` is a dependency *installer*, not a runtime dependency. Once a `.venv/`
has been built, the running application only needs `.venv/bin/python` — a
self-sufficient interpreter with everything already installed into it. Per
[AGENTS.md](../AGENTS.md) and [SCAFFOLDING.md](SCAFFOLDING.md), production
runs plain Python with a virtual environment; `uv` is a developer tool only
and is never installed on the production server. See
[RHEL9_DEV_SETUP.md](RHEL9_DEV_SETUP.md) for `uv` installation and
[RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md) (once written) for how a
`uv.lock`-built environment reaches the server without `uv` present there.

---

## Microsoft ODBC Driver 18 for SQL Server

### [WSL: do yourself] / [Prod: request from infra] — register Microsoft's repo

```bash
sudo curl -fsSL https://packages.microsoft.com/config/rhel/9/prod.repo -o /etc/yum.repos.d/mssql-release.repo
```

The RHEL equivalent of SCAFFOLDING.md's
`packages.microsoft.com/config/ubuntu/24.04/prod.list` step — a `.repo` file
(dnf/yum's config format) in `/etc/yum.repos.d/`, the directory `dnf` scans
for repo definitions. Microsoft publishes this RHEL9-specific repo directly;
no rebuild-compatibility guessing needed.

### [WSL: do yourself] / [Prod: request from infra] — install the driver

```bash
sudo ACCEPT_EULA=Y dnf install -y msodbcsql18
```

`ACCEPT_EULA=Y` is required non-interactively, same as the `apt` install.
First run prompts to import Microsoft's GPG key — accept it.

This pulls in `unixODBC` (the runtime, from RHEL's own AppStream repo) as a
dependency automatically. **Correction to an earlier assumption**: no CRB
(CodeReady Builder) repo enablement is needed for the base driver install —
verified directly rather than assumed.

### [WSL: do yourself] / [Prod: request from infra] — install the devel headers

Needed at compile time for `pyodbc` (this project's SQL Server connectivity
library). Confirmed available without CRB — it ships from Microsoft's own
repo, not RHEL's:

```bash
sudo dnf list available unixODBC-devel   # confirm before installing
sudo dnf install -y unixODBC-devel
```

**Watch for this:** installing `unixODBC-devel` from Microsoft's repo
(version `2.3.11-1.rh`) causes `dnf` to *downgrade* the already-installed
`unixODBC` runtime from AppStream's `2.3.12-1.el9` to Microsoft's matching
`2.3.11-1.rh`, so both packages come from the same repo at the same version.
This is expected, not a conflict.

It also prints `warning: /etc/odbcinst.ini created as
/etc/odbcinst.ini.rpmnew` — `dnf` preserves the live config (which already
has the ODBC Driver 18 registration from the `msodbcsql18` install) rather
than overwriting it, and drops unixODBC's own default template alongside it
as `.rpmnew`. Verify nothing was lost rather than assuming:

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

### Production considerations

- Corporate networks commonly block or proxy outbound internet access.
  `packages.microsoft.com` must be reachable from the production server (or
  mirrored internally) for both the repo-registration `curl` and every
  subsequent `dnf install` against it — confirm this with infra before
  assuming the repo file alone is enough. If direct internet access isn't
  allowed, the RPMs may need to be downloaded elsewhere and transferred in,
  which changes this from a two-command request into an actual file-transfer
  request.
- `ACCEPT_EULA=Y` is a real legal acknowledgment (Microsoft's ODBC driver
  license), not a dummy flag — make sure whoever runs this on the production
  server (or approves the request) understands they're accepting that EULA
  on the organization's behalf.
- The devel headers (`unixODBC-devel`) are only needed to *build* `pyodbc`'s
  C extension. If the production deployment ships a prebuilt virtual
  environment (wheels already compiled elsewhere) rather than compiling on
  the server itself, `unixODBC-devel` may not be needed on production at
  all — only the runtime `unixODBC` and `msodbcsql18` would be. This is a
  [RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md) decision (build-on-server vs.
  ship-a-built-venv) that determines whether this package is even part of
  the production request.

---

## Redis (Valkey)

### Which one, and why

RHEL9.8 offers both `redis` (6.2.22) and `valkey` (8.0.9) directly from
AppStream — confirmed by checking, not assumed:

```bash
sudo dnf list available redis valkey
```

**This project uses Valkey by default.** Reasoning, verified against primary
sources rather than assumed from "it's a fork so it's fine":

- `redis` 6.2.22 in RHEL9 AppStream is frozen at a pre-license-change Redis
  release. Upstream Redis 6.2 itself reached end-of-life in August 2024 —
  RHEL9's `redis` package gets security backports only, no forward path.
- Red Hat's own RHEL9 release notes state Redis is being deprecated in favor
  of Valkey starting in RHEL9 and completed in RHEL10, and explicitly
  recommend migrating new deployments to `valkey`.
  ([RHEL9 Release Notes](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/9.8_release_notes/new-features))
- Valkey forked from Redis 7.2.4's source and kept the identical RESP2/RESP3
  wire protocol — confirmed directly from Valkey's own protocol
  documentation. `redis-py` (the Python client) connects to Valkey
  unmodified; Valkey's own team maintains a compatibility-focused
  `valkey-py` fork, confirming the intent.
- Every Redis command this project's Dramatiq broker actually issues
  (`ZADD`, `BRPOP`, `EVALSHA`, `HSET`, `LPOP`, etc. — checked directly against
  Dramatiq's `dispatch.lua` broker source) is unchanged in Valkey 8.0.9.
- AOF persistence directives (`appendonly yes`, `appendfsync everysec`) are
  Redis-7.2-compatible and supported identically by Valkey. The one migration
  caveat found — AOF must be disabled on first boot while an old RDB/AOF file
  imports, then re-enabled — doesn't apply here: this project's queue holds
  transient work items, not data worth preserving, so a fresh Valkey instance
  needs no file migration at all.

**Production is free to use either.** The application connects over the
plain RESP wire protocol via a single `REDIS_URL` connection string — nothing
in the code or its dependencies checks which server is listening on the
other end. If infra prefers `redis`, an existing corporate Redis/Valkey
instance, a managed service, or a cluster, that works with zero code changes;
only `REDIS_URL` in `.env` needs to point at it.

### Impact to code: none

Checked directly — 264 matches for "redis" across 39 files in this repo
sounds alarming until you look at what they actually are:

- **~25 lines of executable code, in 7 files** — every one is either the
  Python package name `redis` (the client library import, unchanged — we are
  not switching Python packages), Dramatiq's own `RedisBroker` class name
  (unchanged — that's the class's name inside the `dramatiq[redis]` library,
  not a claim about the server), a variable name (`redis_broker`,
  `redis_url`, `redis_lib` — cosmetic Python identifiers), or test mocks that
  stub out `redis_lib.from_url` entirely and never touch a real server at
  all, Valkey or Redis.
- **Everything else is prose** — markdown docs, specs, and code comments
  describing the *architectural role* Redis/Valkey plays (a low-latency
  wake-up signal for the `rwb_job` queue, per
  [app/workers/broker.py](../app/workers/broker.py)'s own module docstring:
  "the `rwb_job` SQL table is the queue *of record*... a lost or duplicated
  Redis message can never double-execute or lose work — the row is
  authoritative"). This is why a wrong guess here carries little downside:
  the SQL table, not Redis/Valkey, is the source of truth.

No renames, no `pyproject.toml` changes, no import changes are required for
correctness. Renaming `redis_broker`/`redis_url` or updating prose to say
"Valkey" throughout is optional cosmetic cleanup, not something this
migration depends on.

### [WSL: do yourself] — install

```bash
sudo dnf install -y valkey
```

### [Prod: request from infra]

Request either `valkey` or `redis` be installed and running, reachable at
whatever host/port gets configured into `REDIS_URL` — the choice is infra's
to make, not a project requirement. If infra already runs a Redis or Valkey
instance for other applications, reusing it is fine; this app needs no
dedicated instance beyond its own logical database index (`/0` in the
connection string, or another index if the shared instance needs
separation).

### `redis-cli` does not exist — use `valkey-cli`

Confirmed directly: `which redis-cli` finds nothing after installing
`valkey`; only `/usr/bin/valkey-cli` is provided. Any doc, script, or habit
that types `redis-cli` fails with "command not found" against this package.
Use `valkey-cli` throughout.

### WSL2-specific gotcha: shared networking between distros

If you also have an Ubuntu (or any other) WSL2 distro on the same Windows
machine, and WSL2 is running in **mirrored networking mode**, all WSL2
distros share one IP address and one port space with each other and the
Windows host — confirmed live: `ip addr show eth0` printed the identical
address on both RHEL9 and Ubuntu-26.04. This means Ubuntu's existing
`redis-server` (started by the project's `make wsl-start` on the Ubuntu
side) will already hold port 6379 by the time you try to start Valkey on
RHEL9, and Valkey's startup fails with "Address already in use."

This is a WSL2/Windows networking property, not anything specific to Redis,
Valkey, or this project — it would affect any port both distros' dev stacks
try to bind (redis, the app itself on 80/8000, etc.) if you ever ran full
stacks in both distros at the same time.

**Resolution used here:** stop Ubuntu's Redis before starting RHEL9's, and
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

(`tee -a` rather than `sudo echo ... >> file` — a plain `>>` redirect runs as
your own shell, not as root, so it can't write to a root-owned file even
under `sudo`; piping through `sudo tee` runs the write itself as root.)

### This is already solved — by the project's start script, not by `dnf`

**Installing `valkey`/`redis` via `dnf` (or `apt` on Ubuntu) only installs the
binary.** No systemd unit is enabled, no AOF, no data directory is
configured by the package itself on either distro. Ubuntu's SCAFFOLDING.md
`apt-get install redis-server` step looks like it "just works" — it doesn't;
the actual AOF/directory/idempotency logic lives entirely in
[infra/scripts/wsl-start.sh](../infra/scripts/wsl-start.sh), run via
`make wsl-start`:

```bash
redis-server \
    --daemonize yes \
    --logfile /tmp/rwb-redis.log \
    --bind 127.0.0.1 \
    --appendonly yes \
    --appendfsync everysec \
    --dir /tmp
```

Note `--dir /tmp` — the script sidesteps the exact permission problem
documented below by using a world-writable directory, rather than something
under `/opt` or another restricted path. This is the same pattern
[infra/scripts/start-all.sh](../infra/scripts/start-all.sh) uses for the
Docker/production-mirroring path (`--dir "$LOG_DIR"` instead of `/tmp`).

**For RHEL9 development, the equivalent script is the answer**, not manual
`valkey-server` invocations — see
[RHEL9_DEV_SETUP.md](RHEL9_DEV_SETUP.md) for an RHEL9 version of this script
(swapping only the binary name, `valkey-server` instead of `redis-server` —
every flag is identical on any distro; these are Valkey/Redis's own
command-line flags, nothing RHEL-specific about them).

The manual troubleshooting below is kept because it explains **why** `--dir`
matters and what breaks without it — useful background, not a step to repeat
by hand each time.

### AOF needs a writable `dir` set at startup — not changeable live

Starting Valkey with no explicit directory defaults to whatever directory
you launched it from. Confirmed live: launching from `/opt` (root-owned; see
above) caused AOF enablement to fail outright:

```
Can't open or create append-only dir appendonlydir: Permission denied
```

Also confirmed: `dir` is a **protected config** — `CONFIG SET dir ...` on an
already-running server is rejected (`can't set protected config`), so this
can't be patched around after the fact. It must be set at launch:

```bash
mkdir -p ~/valkey-data   # any directory dev-user (or the service account) owns
valkey-server --daemonize no --port 6379 \
  --dir /home/dev-user/valkey-data \
  --appendonly yes --appendfsync everysec &
```

Verify:

```bash
valkey-cli ping                    # PONG
valkey-cli CONFIG GET dir          # /home/dev-user/valkey-data
valkey-cli CONFIG GET appendonly   # yes
ls -la /home/dev-user/valkey-data/ # appendonlydir present
```

This is the gap SCAFFOLDING.md's Ubuntu instructions don't mention at all —
Ubuntu's `redis-server` package likely pre-configures a writable data
directory via its systemd unit, so this never surfaces there. Running Valkey
manually (no systemd unit yet) exposes it directly.

### Production considerations

- The `dir`/AOF-permission issue is really a **"what account runs the
  service, and does it own its data directory" question** — on production,
  this will be handled by the systemd unit's `User=` directive and a
  properly `chown`-ed data directory (e.g. `/var/lib/valkey`, owned by a
  `valkey` service account), decided in
  [RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md), not something to solve ad hoc
  on the server.
- The WSL2 shared-networking gotcha above is dev-machine-only — a real
  production server runs one OS, not multiple WSL2 distros sharing a NIC, so
  it does not apply there. Do not carry the "stop one before starting the
  other" workaround into any production runbook.
- Confirm with infra which port Valkey/Redis should listen on in production,
  and whether it binds to `127.0.0.1` only (recommended — this app expects a
  local, not network-exposed, broker) or needs to be reachable from other
  hosts.

---

## nginx

### Install

```bash
sudo dnf install -y nginx gettext
```

`gettext` provides `envsubst`, needed below.

### The static-file path problem — fixed

[deploy/nginx/nginx.conf](../deploy/nginx/nginx.conf) used to hardcode
`alias /workspace/app/static/;` — `/workspace` only exists inside the Docker
container. On any real checkout (RHEL9, Ubuntu native, or the production
server), that folder doesn't exist, so nginx can't find the app's CSS/JS/
image files. This was broken everywhere except Docker; it is now fixed.

**Fix:** the config file now uses a placeholder, `${APP_ROOT}`, instead of a
hardcoded path. Before starting nginx, run `envsubst` to fill in the real
checkout path for whichever environment you're in:

```bash
APP_ROOT=/home/dev-user/risk-workbench envsubst '$APP_ROOT' \
    < deploy/nginx/nginx.conf > /tmp/nginx.conf
nginx -c /tmp/nginx.conf
```

Replace `/home/dev-user/risk-workbench` with wherever the repo is actually
checked out. The Docker path (`infra/scripts/start-all.sh`) does this
automatically with `APP_ROOT=/workspace`.

### Production considerations

- The production request to infra should include running nginx as root or
  under a `systemd` unit (needed to bind port 80 — see the `/opt` ownership
  section above for the same class of permission requirement), with
  `APP_ROOT` set to wherever the app is actually deployed
  (`/opt/risk-workbench`, per the FHS layout discussed earlier).
- `gettext`/`envsubst` needs to be part of the production install request
  too — it's a small, standard package, but the config won't load without
  it.

---

## Build tools (gcc, g++)

### [WSL: do yourself] / [Prod: request from infra]

```bash
sudo dnf install -y gcc gcc-c++
```

Direct AppStream packages (11.5.0), no surprises. Pulls in `make` (4.3) as a
dependency — no separate install needed, though don't assume this on a
minimal production image without confirming.

### Verify

```bash
gcc --version    # gcc (GCC) 11.5.0
g++ --version    # g++ (GCC) 11.5.0
make --version   # GNU Make 4.3
```

### Production considerations

Same question as `unixODBC-devel` earlier: these are only needed if Python
packages with C extensions (`pyodbc`, etc.) get **compiled on the server
itself**. If the deployment ships a prebuilt virtual environment instead,
production may not need `gcc`/`gcc-c++`/`make` at all — this is the same
build-on-server vs. ship-a-built-venv decision for
[RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md) to settle.

---

## `/opt` ownership for the application directory

Confirmed live: a plain user cannot create a directory under `/opt` —
`/opt` is root-owned, and only root (or sudo) can write into it.

```bash
mkdir /opt/risk-workbench
# mkdir: cannot create directory '/opt/risk-workbench': Permission denied
```

### [WSL: do yourself]

```bash
sudo mkdir /opt/risk-workbench
sudo chown dev-user:dev-user /opt/risk-workbench
```

`chown` matters as much as `mkdir` here — without it, the directory exists
but is owned by `root`, and a non-root user still can't write files into it.

### [Prod: request from infra]

Request `/opt/risk-workbench` be created, owned by the application's service
account (not a personal developer account) — e.g. a dedicated account such as
`rwb-svc`, matching whatever account the systemd units will run as. The exact
account name and full directory layout (config, logs, variable data) is
formalized in [RHEL9_DEPLOYMENT.md](RHEL9_DEPLOYMENT.md).
