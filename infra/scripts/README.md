# infra/scripts

| Folder | Runs on |
|---|---|
| `infra/scripts/` | Local development, through `make` (WSL2, or inside `linux-box` at `/workspace/scripts`) and CI. `load_cedants.py`, `run_user_setup.sh`, `user_setup.py` and `sweep-export-archives.sh` also run on the server. |
| `infra/scripts/deploy/` | The RHEL9 server, or a WSL2 RHEL9 rehearsal distro. No `make`, no uv. `rhel9-ssh-deploy.sh` runs on the machine that pushes the code. |

Each script's header gives its usage.
