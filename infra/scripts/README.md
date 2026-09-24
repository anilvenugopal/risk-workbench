# infra/scripts

The directory a script sits in says where it may run.

| Directory | Runs on | Contents |
|---|---|---|
| `dev/` | a developer machine only — WSL2 or the `linux-box` container | database creation, the loss dev mirror, the fixture admin, the `wsl-*` and `start-all`/`stop-all` process scripts, `generate-requirements.sh`, hand-applied `patches/` |
| `rhel9/` | the production server only | one-time provisioning, code delivery, install, start/stop, worker health, drain check, export-archive sweep |
| here (top level) | both | `user_setup.py` and its `run_user_setup.sh` wrapper, `check-port.sh`, `mail_smoke_test.py` |

Nothing in `dev/` is safe on the production server. The deploy copies every tracked
file, so `dev/` is present there — what stops it running is the refusal inside each
script, not its absence. `dev/bootstrap_db.py` creates databases as the SA login and
`dev/seed_dev_fixtures.py` inserts an account whose password is in the repository;
both refuse to run unless `APP_ENV` is `development`.
`dev/bootstrap_loss.py` refuses unless `MSSQL_LOSS_DATABASE` is `rwb_loss`, because at
CIC the loss repository is `CRE_Trial_ELT_Repository` and the DBA installs
`loss_schema.sql` into it by hand.

## Rebuilding the Workbench schema

The Workbench keeps one Alembic revision, edited in place, so a schema change is a
rebuild rather than an upgrade: empty the database, then `alembic upgrade head`.
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
