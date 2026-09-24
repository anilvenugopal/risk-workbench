# infra/scripts

Script placement describes the operating system or the data a script manages. It
does not distinguish production from development by itself.

| Location | Runs on | Contents |
|---|---|---|
| `dev/` | a development database only | database creation, the loss dev mirror, and the fixture admin |
| `rhel9/` | RHEL9 deployment tasks | provisioning, code delivery, install, start/stop, worker health, drain check, database rebuild, and export-archive sweep; client scripts such as `rhel9-ssh-deploy.sh` run from the pushing machine |
| `patches/` | the database named by the operator | hand-applied SQL changes for an existing non-empty Workbench database |
| top-level `wsl-*` | Ubuntu under WSL2 | environment loading and native process commands; these may use any `APP_ENV` whose database is exposed through the WSL2 host |
| top-level `start-all.sh` / `stop-all.sh` | the Linux application container | container process launch and shutdown for Docker or Podman |
| other top-level scripts | any host with the named prerequisites | dependency export, user provisioning, port checks, and mail checks |

The deployment copies every tracked script. A directory name is therefore not a
production check. The three executable scripts in `dev/` refuse unsafe targets
before changing anything: `bootstrap_db.py` and `seed_dev_fixtures.py` require
`APP_ENV=development`, and `bootstrap_loss.py` requires
`MSSQL_LOSS_DATABASE=rwb_loss`. `wsl-env.sh` intentionally changes database hosts
to `localhost`; source it only from Ubuntu WSL2 where SQL Server is exposed through
the host. RHEL9 scripts use their own documented prerequisites and can be exercised
on RHEL9 under WSL before they run on the server.

## Rebuilding the Workbench schema

Through spec 017, the Workbench keeps one Alembic revision edited in place, so a
schema change is a rebuild rather than an upgrade: empty the database, then
`alembic upgrade head`.
Everything stays inside the Workbench database and needs no server-level rights, so
the production app login can do it — no SA login, no `CREATE DATABASE`.

| Environment | Command | Empties the database with |
|---|---|---|
| Docker | `make db-rebuild` | `alembic downgrade base` |
| WSL2 | `make wsl-db-rebuild` | `alembic downgrade base` |
| RHEL9 | `APP_DIR=/rms bash rhel9/rhel9-db-rebuild.sh` | `rhel9/drop_workbench_tables.py` |

The server empties the database differently, and that difference is the point.
`downgrade()` is a hand-written list of DROPs describing the schema the *current*
migration file builds. A developer's database was built from the file they have, so
the two agree. The production database was built from an older edit, so they do not:
the moment the file gains a table, `downgrade()` tries to drop something production
never had and alembic stops there. `drop_workbench_tables.py` reads `sys.tables` and
drops what is actually present, which does not depend on when the database was built.

Dev keeps `downgrade base` because it is also the test that `downgrade()` is honest —
which starts to matter shortly, when real per-change revisions replace this.

### This section expires after spec 017

Spec 017 holds the last in-place edit of `0001_initial.py`; 016 does not touch it.
Once both have merged, rebuild the RHEL9 database one final time. That leaves the
server at exactly what `0001` builds, which is the baseline real migrations need.
From then on `0001` is frozen, every schema change is a new revision file, and
`alembic upgrade head` deploys it without dropping anything — so
`rhel9/rhel9-db-rebuild.sh` and `rhel9/drop_workbench_tables.py` get deleted along
with this section.

The `make` targets add the fixture admin and `dev/bootstrap_loss.py --reset-stage`,
which rebuilds the loss dev mirror so a changed column in `loss_schema.sql` takes
effect. The RHEL9 script does neither: the loss repository is CIC's, and no script
creates an account on the server. `dev/seed_dev_fixtures.py` creates
`admin@example.com` on a developer machine and refuses to run anywhere else; every
account on the server is provisioned with `user_setup.py`.

A rebuild is what a schema change needs, not `alembic upgrade head` —
`rhel9/rhel9-app-install.sh` runs `upgrade head`, which does nothing once the single
revision is stamped, however much that revision changed.

`tests/unit/test_architecture_guards.py` asserts that `downgrade()` drops every table
`upgrade()` creates; without that, a table left behind breaks the next `upgrade()`.

## The exposure repository

There isn't one. It has no schema, no reads and no writes, it is out of MVP
(`docs/PRD.md`), and it does not exist at CIC — so the `EXPOSURE` connection was
removed rather than left reporting itself down forever in `/api/health`. Reinstating it
means an `MSSQL_EXPOSURE_*` block in `infra/.env.example` and a `get_connection`
("EXPOSURE") call site.
