# Research: Package Retirement

## R1 - Package is structural overhead with no remaining business rule

**Decision**: Remove Package instead of renaming it or hiding it in the UI.

**Evidence**: Design note 12 records the August 12 decision. CIC identifies data
by EDM/RDM names and does not use a forced EDM-to-RDM association. Design notes 8
and 9 had already removed the only claimed analytical link: broker analyses cannot
be attributed reliably to an EDM portfolio. Typical submissions contain 1-15 EDMs
and fewer RDMs, so a grouping level inside Submission does not help navigation.

**Affected implementation**: `package`, `submission_package`, `package_id` on four
tables, the package router, two package services, package worker chaining, package
cards/modal, package CSS/JavaScript, job counts, 28 unit/SQL test files, and current
execution diagrams. Repository search found Package references in 33 `app/` files,
28 `tests/` files, and 22 sequence-diagram files.

**Alternatives considered**: Keep Package as an internal grouping key. Rejected
because job targeting and RDM capture can use EDM/RDM identifiers directly, while
an internal Package would preserve the same membership and lifecycle complexity.

## R2 - Direct many-to-many associations preserve shared physical data

**Decision**: Add `submission_edm` and `submission_rdm`, each with a composite
primary key and insertion audit columns. Do not use a polymorphic association table.

**Rationale**: An EDM or RDM is one global Workbench/Risk Modeler resource and can
serve several deals. Separate join tables enforce real foreign keys, keep queries
plain, and avoid a type discriminator for two known entity types. Detaching one
association does not change the entity or other associations.

**Alternatives considered**: Put `submission_id` on `irp_edm`/`irp_rdm`; rejected
because it cannot represent reuse. Use `submission_resource(resource_type,
resource_id)`; rejected because SQL Server cannot enforce one foreign key against
two target tables and every query would dispatch on a type code.

## R3 - PR #57 should be ported by concern, not used as the base branch

**Decision**: Reimplement Package retirement from the current branch and port the
standalone-RDM changes from PR #57 function by function.

**Reuse from PR #57**:

- `irp_gateway.submit_rdm_import` passes `exposure_set_name=name`, never `edm_name`.
- `rdm_service.import_rdm` no longer accepts `applied_edm_ids`.
- `upload_rdm` submits once per RDM and records `irp_edm_id` as null.
- The poller stops chaining RDM upload after EDM completion.
- Analysis search filters only by `sourceRdmName`.
- Broker-analysis capture uses one row per `(rdm_id, irp_id)` with `edm_id` null.
- The fake gateway and standalone-RDM tests follow the same contract.

**Reimplement against submission associations**:

- EDM-facing RDM lists, analysis counts, manual sync selection, and add/remove
  candidates currently query `package_id` in PR #57.
- Candidate pagination, selected-value preservation, and stale-submit predicates
  are useful patterns, but their services, routes, and templates attach members to
  packages.

**Discard**: Package attach/detach, submission-to-package attach/detach, package
card wrappers, package modal extensions, and package-specific documentation.

**Why not overtake the branch**: PR #57 changes 56 files and combines the reusable
RDM import correction with a larger expansion of Package management. Package
retirement would delete much of the latter code and make conflict resolution harder
than a focused port.

## R4 - Standalone RDM import removes the EDM x RDM job grid

**Decision**: Import each RDM once against an exposure set of the same name. Start
EDM and RDM upload heads independently when the analyst submits a multi-file add.

**Rationale**: The app no longer asserts an EDM-to-RDM pairing. The current worker
creates one Risk Modeler import for every EDM/RDM pair and waits for EDM completion
before starting RDM work. With ten EDMs and ten RDMs, the current design creates 100
RDM applies for resources that should be imported ten times.

**Dependency finding**: The project uses `irp-integration` 0.4.0 from TestPyPI for
this feature. The installed wheel exposes
`submit_rdm_import_job(..., exposure_set_name=...)`; the signature was inspected
after syncing the project environment and lockfile on 2026-08-12.

## R5 - Submission context belongs in the URL

**Decision**: Add `/submissions/{submission_id}/edms/{edm_id}` and contextual HTMX
routes. Keep `/edms/{edm_id}` for library navigation.

**Rationale**: The same EDM can belong to several submissions. A contextual URL
makes the selected submission explicit, bookmarkable, and server-validatable. The
breadcrumb/context link and RDM query derive from the URL instead of browser history
or an arbitrary oldest association. The EDM selector preserves `submission_id`.

**Alternatives considered**: `?submission_id=` works but treats required navigation
context as an optional display hint. Listing every related submission repeats the
behavior the product decision removes. Picking the oldest submission gives incorrect
RDMs when the analyst arrived from another submission.

## R6 - Lazy loading is per RDM, from stored data

**Decision**: Render RDM identity and counts with the EDM page, then fetch one RDM's
stored analysis rows when its disclosure opens.

**Rationale**: The page can contain 10-15 RDMs and many analyses. An HTMX GET keeps
the first response small and preserves Article 11 because the route reads WORKBENCH
only. Backfill remains poller/worker work.

**Alternatives considered**: Render every analysis initially; rejected for response
size and unnecessary parsing. Fetch Risk Modeler on expand; rejected by Article 11
and the stored-snapshot design.

## R7 - Job execution is entity-scoped; submission is provenance

**Decision**: Drop `package_id` from `irp_job` and `irp_analysis`. Continue targeting
jobs through `irp_edm_id`, `irp_rdm_id`, and later `irp_portfolio_id`. Add nullable
`requested_from_submission_id` on `irp_job` only to store which contextual action
started the operation.

**Rationale**: A job acts on one physical Risk Modeler resource even when the resource
resource serves several submissions. Submission must not choose or duplicate the
execution target. A clearly named provenance column supports contextual job links
without implying ownership. Worker follow-up inherits the value from the originating
job; standalone library actions leave it null.

**Alternative considered**: Replace `package_id` with required `submission_id`.
Rejected because standalone imports exist and a shared entity has no single owner.

## R8 - Database migration is a rebuild, not row conversion

**Decision**: Edit `alembic/versions/0001_initial.py` and the SQLite mirrors, then
have the developer rebuild WORKBENCH using the established pre-go-live process.

**Rationale**: Design note 12 confirms demo data is disposable and the repository
maintains one revision until production cutover. A converter from package membership
to direct associations would be thrown away and would require choosing how package
rows with several submissions expand into joins. EDM sync from Risk Modeler restores
the useful external catalog after rebuild.

**Alternative considered**: Add a second Alembic migration and backfill joins from
`submission_package`; rejected by the stated pre-go-live schema strategy.

## R9 - Add-existing and removal rules

The add-existing picker returns every live eligible EDM/RDM not already related to
the target submission. Name search and pagination remain sufficient; existing
association counts can be shown as context.

Submission removal deletes only the association. Physical deletion from Risk Modeler
is deferred because deleting a shared resource affects every related submission.
