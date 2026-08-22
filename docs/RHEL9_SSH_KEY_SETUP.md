# RHEL9 SSH Key Setup

How to generate an SSH key pair and use it to log into the RHEL9 server
without a password — needed for any deployment mechanism that logs in
remotely (a local test from Ubuntu/Windows, or a real CI/CD pipeline).

**The rule that never changes**: the **private** key stays on whichever
machine is *initiating* the connection (never shared, never leaves that
machine). The **public** key goes onto the machine being connected to —
RHEL9, in the account's `~/.ssh/authorized_keys` file. This is true
whether "the machine initiating the connection" is your own laptop, a CI
runner, or anything else.

RHEL9's `sshd` only accepts key-based login, not passwords — confirmed
directly: a plain password attempt returned "Permission denied
(publickey,gssapi-keyex,gssapi-with-mic)." A key pair is not optional here.

---

## Finding RHEL9's IP address

Every connection in this guide needs RHEL9's address. There's no external
lookup for this — you have to ask the machine itself, from inside it.

On **RHEL9**:

```bash
ip addr show eth0
```

Look for the line starting with `inet` — e.g.
`inet 172.19.253.47/20 brd 172.19.255.255 scope global eth0`. The address
before the `/` (here, `172.19.253.47`) is what every `ssh`/`scp` command
in this guide uses in place of `172.19.253.47`.

**This address is not permanent.** WSL2 assigns it automatically, and it's
likely stable for the length of a work session, but may change after a
Windows reboot or `wsl --shutdown`. If a connection that used to work
suddenly can't reach RHEL9, re-run this command on RHEL9 to get its
current address before assuming anything else is broken.

**Why not use a hostname instead**: RHEL9 and Ubuntu share the same
hostname (`piq`) on this machine, due to a WSL2 networking quirk covered
in [RHEL9_SYSTEM_SETUP.md](RHEL9_SYSTEM_SETUP.md#redis-valkey) — a
hostname would be ambiguous between the two. Use the IP address directly
for this WSL2 setup. A real production RHEL9 server would have its own
unique hostname or a stable IP assigned by infra; this ambiguity is
specific to running two WSL2 distros side by side, not something that
exists in a real deployment target.

---

## From Ubuntu (WSL2) to RHEL9 — tested, confirmed working

### Step 1 — generate the key pair

```bash
ssh-keygen -t ed25519 -f ~/.ssh/risk-workbench-deploy -C "risk-workbench-deploy"
```

- `-t ed25519` — a modern, secure key type.
- `-f ~/.ssh/risk-workbench-deploy` — where to save it, and what to name
  it. `~/.ssh/` is the default folder SSH tools check automatically; a
  distinct name (not the generic default `id_ed25519`) keeps this key
  separate from any other key you might have. SSH automatically appends
  `.pub` to this name for the public half.
- `-C "risk-workbench-deploy"` — a plain-text label baked into the key
  file, purely for your own reference later (e.g. when looking at a list
  of authorized keys).

When prompted for a passphrase, leave it **empty** (press Enter twice) —
this key is meant to be used unattended, by a script or pipeline with no
human present to type a passphrase. Security here comes from controlling
who can access the private key file at all, not a second password on top
of it. Treat the private key file itself as a real secret.

### Step 2 — get the public key onto RHEL9

The public key must land in a specific file on RHEL9:
`~/.ssh/authorized_keys` for the account being logged into. `ssh-copy-id`
is the normal tool for this, but it needs password auth to work the first
time — which RHEL9 doesn't allow — so use the same file-transfer approach
already established for this project (see
[RHEL9_WSL_INSTALL.md](RHEL9_WSL_INSTALL.md) for why a direct `scp`
between these two WSL2 distros didn't work either).

On **Ubuntu**, copy only the **public** key (never the private one) to the
Windows-shared folder:

```bash
cp ~/.ssh/risk-workbench-deploy.pub /mnt/c/Users/venug/Downloads/risk-workbench-deploy.pub
```
NOTE: Remember to replace the location on local Windows workstation.

On **RHEL9**, append it to the authorized keys file:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat /mnt/c/Users/venug/Downloads/risk-workbench-deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

NOTE: Remember to replace the location on local Windows workstation.

The `chmod` commands are not optional — `sshd` silently refuses to use
`authorized_keys` at all if the folder or file permissions are too loose
(writable by anyone besides the owner). `700` on the folder means "only
the owner can read/write/enter it"; `600` on the file means "only the
owner can read/write it."

### Step 3 — verify

```bash
ssh -i ~/.ssh/risk-workbench-deploy dev-user@172.19.253.47
```

(replace `172.19.253.47` with RHEL9's actual current address — see
"Finding RHEL9's IP address" above.)

Confirmed working: this logs straight into RHEL9 with **no password
prompt**. If it asks for a password or refuses, the key isn't correctly
recognized yet — check the `chmod` steps above first.

**Expected, benign warning**: every connection prints

```
** WARNING: connection is not using a post-quantum key exchange algorithm.
** This session may be vulnerable to "store now, decrypt later" attacks.
```

This is OpenSSH's standard advisory when one side doesn't yet support its
newest key-exchange method — confirmed the cause directly: Ubuntu runs
OpenSSH 10.2 (supports it), RHEL9.8 ships OpenSSH 9.9 (doesn't yet), so
they correctly negotiate down to the older, still-secure-today method
instead of failing. Not a misconfiguration, not something this project's
setup caused — it will go away on its own once RHEL9 ships a newer OpenSSH
in a future minor release. Not worth acting on for this dev/test setup.

---

## From Windows 11 (not WSL2) to RHEL9 — not yet tested

If connecting directly from a native Windows terminal (PowerShell,
Windows Terminal) rather than from inside a WSL2 Linux distro, the key
pair is generated and stored differently, since Windows has its own
OpenSSH client built in (no separate install needed on Windows 10/11).

### Step 1 — generate the key pair

In PowerShell:

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\risk-workbench-deploy -C "risk-workbench-deploy"
```

Windows's default SSH folder is `%USERPROFILE%\.ssh\` (typically
`C:\Users\<you>\.ssh\`) — the direct Windows equivalent of Linux's `~/.ssh/`.

### Step 2 — get the public key onto RHEL9

Since this key was generated directly on Windows (not inside WSL2), no
`/mnt/c/` step is needed — Windows can reach WSL2 distros directly via
`wsl.exe`:

```powershell
Get-Content $env:USERPROFILE\.ssh\risk-workbench-deploy.pub | wsl -d RHEL9 -- bash -c "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

This pipes the public key's contents from Windows directly into a command
run inside the RHEL9 WSL2 distro, appending it to `authorized_keys` in one
step — untested in this project, verify it works before relying on it.

### Step 3 — verify

```powershell
ssh -i $env:USERPROFILE\.ssh\risk-workbench-deploy dev-user@172.19.253.47
```

(replace `172.19.253.47` with RHEL9's actual current address — see
"Finding RHEL9's IP address" above.)

Same expected result as the Ubuntu case: no password prompt.

**This entire section is unverified** — everything in it follows directly
from documented Windows OpenSSH and WSL2 behavior, but it has not been run
and confirmed working in this project the way the Ubuntu path has. Treat
it as a starting point to test, not a proven procedure.

---

## GitHub Actions

Researched from GitHub's own documentation (see sources below) — not yet
implemented or tested in this project.

**Where the secret is stored**: repository page → **Settings** → **Secrets
and variables** → **Actions** → **Secrets** tab → **New repository
secret**. Paste the full private key contents as the value.

**How it's used**: GitHub has no first-party "SSH deploy" action. The
de facto standard is the third-party Marketplace action
**`webfactory/ssh-agent`**, which loads the key into an in-memory
`ssh-agent` for the duration of the job — per that action's own
documentation, the key is never written to disk.

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: webfactory/ssh-agent@v0.9.0
        with:
          ssh-private-key: ${{ secrets.SSH_PRIVATE_KEY }}
      - run: ssh -o StrictHostKeyChecking=no dev-user@<rhel9-host> "deploy.sh"
```

**Persistence**: not written to disk by `webfactory/ssh-agent`; lives only
in the runner's `ssh-agent` memory for the job's duration, on a temporary
runner destroyed after the job finishes. (If a workflow instead manually
did something like `echo "$KEY" > ~/.ssh/id_rsa`, it *would* hit disk —
that would be a choice made in the workflow's own steps, not something
GitHub or this action does by default.)

Sources: [Secrets - GitHub Docs](https://docs.github.com/en/actions/concepts/security/secrets),
[Using secrets in GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions),
[webfactory/ssh-agent](https://github.com/marketplace/actions/webfactory-ssh-agent).

---

## Azure DevOps Pipelines

Researched from Microsoft's own documentation — not yet implemented or
tested in this project. Two mechanisms exist; per Microsoft's own task
docs, the first is the one intended for "run a command on a remote Linux
server":

**SSH service connection + `SSH@0` task** (the recommended approach here):

Menu path: **Project Settings → Service connections → New service
connection → SSH**. Fields: host name, port (default 22), username,
private key (full file contents pasted directly into the form — not
uploaded as a file), passphrase (if the key has one), connection name.

```yaml
steps:
- task: SSH@0
  inputs:
    sshEndpoint: myServerSshConnection   # name of the SSH service connection
    runOptions: 'commands'
    commands: 'cd /opt/risk-workbench && bash infra/scripts/rhel9-app-install.sh'
```

A sibling task, `CopyFilesOverSSH@0`, handles file transfer using the same
service connection.

**Secure Files + `InstallSSHKey@0`** — a separate, older pattern Microsoft
documents mainly for authenticating to a Git remote (e.g. cloning a
private repo from the pipeline), not the general "run a command on a
server" case. Mentioned here for completeness; the SSH service connection
above is the documented fit for this project's actual need.

**Microsoft-hosted vs. self-hosted agents**: nothing about key delivery
changes based on agent type. `SSH@0` requires agent version 2.206.1+.

**Persistence**: Microsoft's `SSH@0` reference does not state whether the
key ever touches the agent's disk during a run — **unverified, flag as
open** rather than assumed either way. (`InstallSSHKey@0`, the separate
older mechanism, does confirm writing to and later restoring the agent's
SSH config file — but that's a different task from the one recommended
above.)

Sources: [Service connections - Azure Pipelines](https://learn.microsoft.com/en-us/azure/devops/pipelines/library/service-endpoints),
[SSH@0 task](https://learn.microsoft.com/en-us/azure/devops/pipelines/tasks/reference/ssh-v0),
[InstallSSHKey@0 task](https://learn.microsoft.com/en-us/azure/devops/pipelines/tasks/reference/install-ssh-key-v0).

---

## Summary

| | Where private key lives | Mechanism | Status |
|---|---|---|---|
| Ubuntu → RHEL9 | `~/.ssh/risk-workbench-deploy` on Ubuntu | plain `ssh -i` | **Tested, confirmed working** |
| Windows 11 → RHEL9 | `%USERPROFILE%\.ssh\risk-workbench-deploy` | plain `ssh -i` | Documented, not tested |
| GitHub Actions | GitHub-encrypted secret, injected at run time | `webfactory/ssh-agent` (third-party) | Researched, not implemented |
| Azure DevOps | SSH service connection | `SSH@0` task (first-party) | Researched, not implemented |