# Risk Analysis Workbench

Analyst workbench for catastrophe reinsurance workflows over Moody's Risk Modeler.

## Start here

| Goal | Start with | Then |
|---|---|---|
| Develop in WSL2 (Ubuntu or RHEL9) | [docs/LOCAL_DEV_SETUP.md](docs/LOCAL_DEV_SETUP.md); on RHEL9, [docs/RHEL9_WSL_INSTALL.md](docs/RHEL9_WSL_INSTALL.md) first | [docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md) |
| Develop on a machine that cannot run WSL2 | [docs/LOCAL_DEV_SETUP.md](docs/LOCAL_DEV_SETUP.md#docker-only-stack-no-wsl2) | [docs/USER_PROVISIONING.md](docs/USER_PROVISIONING.md) |
| Deploy to the RHEL9 server | [docs/deploy/RHEL9_QUICKSTART.md](docs/deploy/RHEL9_QUICKSTART.md) | [docs/deploy/RHEL9_DEPLOYMENT.md](docs/deploy/RHEL9_DEPLOYMENT.md), [docs/ENTRA_SETUP.md](docs/ENTRA_SETUP.md) |
| Rehearse a deployment on WSL2 RHEL9 | [docs/RHEL9_WSL_INSTALL.md](docs/RHEL9_WSL_INSTALL.md) | [docs/deploy/RHEL9_QUICKSTART.md](docs/deploy/RHEL9_QUICKSTART.md) |
| Take over the system | [docs/handover/index.html](docs/handover/index.html) | |
| Change the code | [AGENTS.md](AGENTS.md) | [docs/PRD.md](docs/PRD.md), [docs/DATA_MODEL.md](docs/DATA_MODEL.md), [.specify/memory/constitution.md](.specify/memory/constitution.md) |

Local development uses `make` and uv. The server uses the scripts in
`infra/scripts/deploy/`, a plain `.venv`, and no `make` or uv. See
[infra/scripts/README.md](infra/scripts/README.md).

## Repository layout

| Path | Holds |
|---|---|
| `app/` | FastAPI app: routers, services, Jinja2 templates, workers, poller |
| `db/` | Database access (`db.execute`) and the `rwb_loss` bootstrap SQL |
| `alembic/` | `rwb_workbench` schema revisions |
| `infra/` | Docker Compose, `.env.example`, and dev scripts in `infra/scripts/` |
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

Full reference: [docs/LOCAL_DEV_SETUP.md](docs/LOCAL_DEV_SETUP.md#make-command-reference).
