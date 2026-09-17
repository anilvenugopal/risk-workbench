# Quickstart: Analysis Run Details (spec 015)

How to verify the feature. Contracts: [contracts/](contracts/); column
content: [data-model.md](data-model.md).

## Prerequisites

- irp-integration release carrying the describe method
  ([contracts/irp-gateway.md](contracts/irp-gateway.md)) pinned with
  `make irp-testpypi`; `make irp-status` names it.
- Docker stack up (`make dev-up`) or WSL2 native — **starting the stack is
  the developer's call**; agents report and stop if it is down.
- DB lifecycle: **Refresh** — no migration. Rows captured before the change
  show the new fields blank (FR-015): run a manual RDM sync to recapture
  broker analyses, and run a fresh suite for own analyses.
- Worker processes for `finalize_analysis` and `backfill_rdm_analyses`
  (CR-04 per-queue commands) plus the poller.
- A submission with an EDM holding a DLM template and an HD template, two
  treaties, and an RDM whose broker analyses include one with a treaty
  (sandbox RDM `usfl_broker_results` fits: 5689560 has two).

## 1. Unit tier (no containers)

```bash
uv run pytest tests/unit
```

Covers the collapse, the group property branch over every captured shape,
both writers' success and blank-and-continue paths, the plan item's
`treaty_names`, the reader over every capture, the Compare modal line, and
zero gateway calls on render (plan.md Testing).

## 2. SQL Server tier

```bash
make test-sql        # or make wsl-test-sql
```

No new tests. Run once to confirm the existing suite still passes with the
larger JSON content.

## 3. IRP sandbox tier (opt-in)

```bash
make shell
uv run pytest tests/irp -k describe --run-irp
```

Reads 5741781, 5733173 and 5689560 live and re-checks the fixtures' claims.

## 4. Click-through, one story at a time

**Story 1 — broker scheme.** Manual-sync an RDM. Open the submission's RDM
analyses section, expand a DLM analysis: Event rate scheme names the scheme.
Open the Group dialog with that analysis ticked and inspect: the compose
screen's scheme matches. Expand an analysis whose describe read failed (force
with the fake in unit tests; live, none should): the field reads *not
returned* and the rest of the row renders.

**Story 2 — HD simulation set.** Run the HD template and a DLM template on
the same portfolio; wait for `ready`. Expand the HD row: Simulation set reads
`<PET name> (<N> periods)` and no Event rate scheme entry appears. Expand the
DLM row: the reverse.

**Story 3 — mixed group.** Group the HD and DLM analyses, choose a simulation
set for the DLM partition, Finish, wait for `ready`. Expand the group: one
list entry per region and peril, each naming its scheme and the simulation
set chosen on the compose screen. Group two DLM analyses: schemes only.

**Story 4 — treaties.** Run a template with two treaties selected; expand the
finished row: two entries, `number · name · currency`. Expand broker 5689560's row: its
two treaties. Expand an analysis run with no treaties: no Treaties entry.

**FR-016 check.** Open the Compare modal on the same table: the metadata line
names the same scheme the expanded row shows for each analysis.
