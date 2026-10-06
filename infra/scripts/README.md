# infra/scripts

Each script's header says where it runs (`Runs on:`), what it needs (`Needs:`),
and how to call it (`Usage:`).

| Folder | Runs on |
|---|---|
| `infra/scripts/` | Local development, from a checkout through `make`: the `wsl-*` targets in WSL2, the Docker targets inside `linux-box` (mounted at `/workspace/scripts`), and CI. `load_cedants.py`, `run_user_setup.sh`, `user_setup.py` and `sweep-export-archives.sh` also run on the server. |
| `infra/scripts/deploy/` | The RHEL9 server, or a WSL2 RHEL9 distro used to rehearse a deployment. No `make`, no uv. `rhel9-ssh-deploy.sh` is the exception: it runs on the machine that pushes the code. |
| `infra/scripts/patches/` | Hand-run SQL against `rwb_workbench`, one file at a time in numbered order. |

Setup guides: [docs/LOCAL_DEV_SETUP.md](../../docs/LOCAL_DEV_SETUP.md) for
local development, [docs/deploy/RHEL9_QUICKSTART.md](../../docs/deploy/RHEL9_QUICKSTART.md)
for deployment.
