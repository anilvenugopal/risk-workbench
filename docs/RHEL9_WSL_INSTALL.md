# RHEL9 WSL Install

Installs a real Red Hat Enterprise Linux 9.8 distro under WSL2 on Windows,
alongside your existing Ubuntu WSL2 distro. Ubuntu is not touched by any step
below — this creates a second, separate distro.

This is a one-time, per-developer, Windows-side setup. Run it yourself; no
infra request needed for any step here (registration uses your own free Red
Hat Developer subscription, not a corporate one).

Covers: getting the RHEL9 image, installing it as a WSL2 distro, verifying it
boots, registering it with Red Hat, and creating your personal login account.

Does **not** cover: installing project dependencies (Python, Redis, the ODBC
driver, etc.) — see [RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md). Does not
cover cloning the repo or running the app — see
[RHEL9_DEV_SETUP.md](RHEL9_DEV_SETUP.md).

---

## Why WSL2 + real RHEL9, not a rebuild (AlmaLinux/Rocky) or a VM

Production runs RHEL9. Red Hat and Microsoft officially support running real
RHEL9 (not a rebuild) as a WSL2 distro, under the free **Red Hat Developer
Subscription for Individuals** — announced November 2024, listed by Red Hat
as a "Validated Software Platform." Two documented limits: OpenSCAP
compliance tooling doesn't work under WSL images, and there's no separate
disk/storage config since WSL instances don't have their own disk image.
Neither limit matters for local development.

This gets you the actual RHEL9 userspace — same package names, same `dnf`
behavior, same defaults as the real server — with none of the binary-rebuild
questions a distro like AlmaLinux would raise, and none of the networking/
filesystem friction a separate virtual machine would add on top of the
Docker Desktop WSL2 integration you already use for SQL Server.

---

## Step 1 — Get a free Red Hat Developer account

Red Hat requires an account to build and download a RHEL9 WSL image.

1. Go to <https://developers.redhat.com> and sign up for a free account (or
   log in if you already have one). This grants the **Red Hat Developer
   Subscription for Individuals**, which entitles RHEL use including on WSL2.
2. Note: the general RHEL downloads page
   (`access.redhat.com/downloads/content/rhel`) only lists ISOs, DVD images,
   and KVM guest images — none of those import into WSL2. The WSL2 image is
   built on demand via Image Builder, in the next step.

## Step 2 — Build a RHEL9 WSL image with Image Builder

1. Go to <https://console.redhat.com/insights/image-builder> (logged in with
   your Developer account).
2. Click **Create image blueprint**.
3. Select **Package mode**.
4. Choose RHEL version **9.8** (or the current 9.x if 9.8 is no longer the
   latest by the time you read this).
5. Architecture: **x86_64**.
6. Target environment: **Windows Subsystem for Linux**.
7. Build the blueprint/image, then wait — the build takes a few minutes and
   shows "Image build in progress" until done.
8. Once complete, click **Download (.wsl)**. The download link expires in 7
   days, but the file itself does not expire once saved to disk.

You'll get a file named something like
`composer-api-<uuid>-image.wsl` in your Downloads folder.

## Step 3 — Install it as a WSL2 distro

In a plain Windows PowerShell window (Start menu → "PowerShell"):

```powershell
wsl --install --from-file "$env:USERPROFILE\Downloads\<your-file>.wsl" --name RHEL9
```

`--from-file` registers a new WSL distro from that exact file instead of
pulling one from the Microsoft Store catalog. `--name RHEL9` is what you'll
use in every command afterward (`wsl -d RHEL9`).

This also auto-launches the distro for its first boot. You may see a warning
like `Failed to start the systemd user session for 'cloud-user'` — this is a
known first-boot rough edge with Image Builder's WSL images, not a lasting
problem. Confirm with the next step.

## Step 4 — Verify it's registered and boots

```powershell
wsl --list --verbose
```

Expect to see `RHEL9` listed with `VERSION 2`. State may show `Stopped` —
that's normal; WSL stops idle distros automatically.

Start it:

```powershell
wsl -d RHEL9
```

You should land on a prompt like `[cloud-user@piq ~]$`. `cloud-user` is the
account Image Builder's cloud-init setup creates by default. `piq` (or
similar) is the hostname WSL assigned — cosmetic.

Confirm the OS is genuinely RHEL9.8, not a rebuild:

```bash
cat /etc/os-release
```

Expect `ID="rhel"`, `VERSION_ID="9.8"`, `PLATFORM_ID="platform:el9"`.

Confirm `cloud-user` has working sudo (cloud-init images are provisioned with
passwordless sudo for the initial account):

```bash
sudo whoami
```

Should print `root` with no password prompt.

## Step 5 — Register the subscription

Unregistered, `dnf` installs will fail or be restricted. Get your
**Organization ID** and an **Activation Key** from
<https://access.redhat.com/management/activation_keys> (create one scoped to
Red Hat Enterprise Linux if you don't already have one), then:

```bash
sudo subscription-manager register --org=YOUR_ORG_ID --activationkey=YOUR_ACTIVATION_KEY
```

Do not commit or paste your Organization ID / Activation Key anywhere outside
this command.

## Step 6 — Fix the missing locale

Image Builder's WSL image ships without English locale data installed, even
though the shell environment defaults to `LANG=en_US.UTF-8`. This shows up as
`locale: Cannot set LC_CTYPE to default locale: No such file or directory`
and `Failed to set locale, defaulting to C.UTF-8` on every `dnf` command (and
potentially in other tools that shell out or parse locale-sensitive output).
`dnf` still works with the `C.UTF-8` fallback, but fix it now rather than
carry the warning through every later step.

Confirm the gap:

```bash
locale -a
```

If `en_US.utf8` is missing (only `C`, `C.utf8`, `POSIX` listed), install it:

```bash
sudo dnf install -y glibc-langpack-en
```

Re-run `locale` — the `Cannot set locale` errors should be gone.

**Production applicability — unconfirmed, verify before assuming either way.**
This is a gap in Image Builder's specific WSL image output, not a property of
RHEL9 itself. A production server is unlikely to be provisioned the same way
(Kickstart, a cloud image, or infra's own golden image typically include
locale packages by default) — but confirm on the actual server rather than
assuming it's already there or that it needs requesting. If `locale -a` on
the production box is missing `en_US.utf8`, request `glibc-langpack-en` from
infra; if it's already present, no request needed.

## Step 7 — Create your personal development account

Production has no standing sudo for you — privileged changes go through a
request to infra. Developing as `cloud-user` (free passwordless sudo) hides
that constraint. Create a separate, personal account and use it for all
development work from here on; keep `cloud-user` only as a fallback admin
account.

```bash
sudo useradd -m -s /bin/bash dev-user
sudo passwd dev-user
sudo usermod -aG wheel dev-user
```

- `useradd -m -s /bin/bash dev-user` — creates the account with a home
  directory and bash as the login shell. Replace `dev-user` with whatever
  username you want.
- `passwd dev-user` — sets a login password. A freshly created account has no
  password and cannot log in until this runs.
- `usermod -aG wheel dev-user` — adds the account to `wheel`, the RHEL/Fedora
  group that grants `sudo` (the RHEL equivalent of Ubuntu's `sudo` group).

Verify:

```powershell
wsl -d RHEL9 -u dev-user
```

```bash
sudo whoami
```

This time it should prompt for **dev-user's** password (not passwordless) and
then print `root`.

---

## Result

- A real RHEL9.8 WSL2 distro named `RHEL9`, separate from your existing
  Ubuntu distro.
- Registered with Red Hat via your free Developer Subscription.
- A personal, password-protected, sudo-capable account (`dev-user`) for all
  further work — matching the no-standing-sudo reality of the production
  server.

Next: [RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md) to install project
dependencies (Python 3.12, Redis, the ODBC driver, nginx, build tools).
