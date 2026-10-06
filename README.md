# Risk Analysis Workbench

Analyst workbench for catastrophe reinsurance workflows over Moody's Risk Modeler.

## Start here

Pick your goal. Each document ends with a "Next" section.

| Goal | Start with | Then |
|---|---|---|
| Develop locally in WSL2 (Ubuntu, or RHEL9 to match the server) | [docs/LOCAL_DEV_SETUP.md](docs/LOCAL_DEV_SETUP.md). On RHEL9, [docs/RHEL9_WSL_INSTALL.md](docs/RHEL9_WSL_INSTALL.md) first | [docs/DEVELOPER_PLAYBOOK.md](docs/DEVELOPER_PLAYBOOK.md#claude-code-setup) (VS Code, Claude Code, SpecKit) |
| Develop on Windows with Docker only | [docs/DEVELOPER_PLAYBOOK.md](docs/DEVELOPER_PLAYBOOK.md) Option A | [docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md) |
| Deploy to the RHEL9 server | [docs/deploy/RHEL9_QUICKSTART.md](docs/deploy/RHEL9_QUICKSTART.md) | [docs/deploy/RHEL9_DEPLOYMENT.md](docs/deploy/RHEL9_DEPLOYMENT.md), [docs/ENTRA_SETUP.md](docs/ENTRA_SETUP.md) |
| Rehearse a deployment on a WSL2 RHEL9 distro | [docs/RHEL9_WSL_INSTALL.md](docs/RHEL9_WSL_INSTALL.md) | [docs/deploy/RHEL9_QUICKSTART.md](docs/deploy/RHEL9_QUICKSTART.md) |
| Understand or take over the system | [docs/handover/index.html](docs/handover/index.html) | |
| Change the code | [AGENTS.md](AGENTS.md) | [docs/PRD.md](docs/PRD.md), [docs/DATA_MODEL.md](docs/DATA_MODEL.md), [.specify/memory/constitution.md](.specify/memory/constitution.md) |

## Local development and deployment are separate

Local development runs from a checkout through `make`: the `make wsl-*` targets
in WSL2, or the Docker targets that run inside the `linux-box` container. It
uses uv, and SQL Server runs in a Docker or Podman container.

Deployment runs the scripts in `infra/scripts/deploy/` on the RHEL9 server. The
server has a plain `.venv`, no uv and no `make`, and SQL Server is a separate
host. Every script's header says where it runs and what it needs;
[infra/scripts/README.md](infra/scripts/README.md) lists the few top-level
scripts that also run on the server.

## Repository layout

| Path | Holds |
|---|---|
| `app/` | FastAPI app: routers, services, Jinja2 templates, workers, poller |
| `db/` | Database access (`db.execute`) and the `rwb_loss` bootstrap SQL |
| `alembic/` | `rwb_workbench` schema revisions |
| `infra/` | Docker Compose, `infra/.env.example`, and `infra/scripts/` (local development) |
| `infra/scripts/deploy/` | Server setup, deploy, start, stop and health scripts |
| `deploy/nginx/` | nginx configuration |
| `tests/` | `unit` (no database), `sqlserver`, `irp` |
| `specs/` | SpecKit feature specs, plans and tasks |
| `docs/` | Setup guides, deploy runbooks (`docs/deploy/`), handover pages (`docs/handover/`) |

## Everyday commands

```bash
make help                   # every make target
uv run pytest tests/unit    # unit tests; no database or container needed
```

The full command reference is in
[docs/LOCAL_DEV_SETUP.md](docs/LOCAL_DEV_SETUP.md#make-command-reference).
