# Sequence-diagram fact-check report

Audit date: 2026-07-31

Historical evidence: the audit predates spec 006. References to the retired domain and
deleted diagram paths describe the repository state on the audit date, not implemented
behavior after package retirement.

## Scope and method

This audit compared the current-state diagrams and the cross-cutting claims in
`docs/sequence_diagrams/README.md` with the FastAPI routes, services, Dramatiq workers,
poller, templates, migration seeds, and focused unit tests. The files under `planned/`
were checked only for correct classification as unimplemented design-ahead flows; their
README already makes that status explicit.

Overall, the diagrams describe the implemented request/worker/poller split well. The
submission, entity import, package sync, detail backfill, manual sync, read-only detail,
library, treaty export, shell, and health flows substantially match the implementation.
The audit found **three material discrepancies**, **three smaller documentation errors**,
and **one implementation defect exposed by the comparison**.

## Findings

### F1 — High — Password-user creation does not assign the documented role

`auth/user_administration.md` says `POST /admin/users/new` accepts a chosen role and writes
both `app_user` and `user_role`. The route accepts only display name, email, password, and
CSRF, and inserts only `app_user`. Consequently, after the mandatory password change the
new user still has no role and middleware sends them to access-pending. The diagram's
“full access” outcome is not reachable until an admin separately assigns a role.

Evidence: `app/routers/admin.py` (`create_user`), `app/auth/middleware.py`, and
`docs/sequence_diagrams/auth/user_administration.md`.

Recommendation: either add role selection and an atomic `user_role` insert to the route,
or amend the diagram to show the separate assign-role step and access-pending gate.

### F2 — Medium — The login diagram incorrectly says JIT provisioning inserts `user_role`

The records-written table in `auth/login_and_session.md` lists an `app_user, user_role`
insert for a previously unknown OIDC user. `jit_provision_oidc_user` deliberately inserts
only `app_user`; the absence of a role is what makes the fail-closed access-pending path
work. The sequence and surrounding prose describe the actual behavior correctly, so this
is an internally contradictory table entry rather than a runtime defect.

Evidence: `app/auth/provisioning.py` and
`docs/sequence_diagrams/auth/login_and_session.md`.

Recommendation: change that table row to `app_user` only.

### F3 — High — A ready EDM can have no exposure ID, making package deletion skip Risk Modeler

The import diagram states that terminal success backfills `irp_edm.irp_id` and that
downstream work may rely on it once the EDM is ready. In code, `_resolve_edm_exposure_id`
is best-effort: search failure or no match returns `None`, but the poller still marks the
EDM `ready`. Later, `_delete_edm_body` interprets `irp_id IS NULL` as “never imported,”
marks it deleted locally, and never calls Risk Modeler. This can orphan a real RM exposure.
The delete diagram's `irp_id IS NULL` branch therefore conflates “never imported” with
“successfully imported but ID resolution failed.”

Evidence: `app/poller/run.py` (`_resolve_edm_exposure_id` and
`_handle_import_edm_terminal`), `app/workers/package_jobs.py` (`_delete_edm_body`), and
the import/delete diagrams.

Recommendation: do not mark an imported EDM ready without a resolved exposure ID, or
persist enough provenance to distinguish unresolved from never imported and block/recover
deletion safely. Update both diagrams to show the chosen failure/recovery branch.

### F4 — Medium — Unknown-email password attempts are logged twice

The login diagram says every password-login attempt creates one `login_attempt`. For an
unknown email, `login_submit` invokes `fail("account_not_found")` once without returning,
then invokes and returns it a second time. Each invocation inserts a failure row. This is
an implementation defect; the one-row-per-attempt diagram expresses the sensible intended
behavior.

Evidence: `app/routers/auth.py` (`login_submit`).

Recommendation: retain any timing-oracle mitigation, but log and render the failure once.

### F5 — Low — Cancelled RM jobs are misspelled in several diagrams

The canonical RM status is `CANCELLED`, as seeded by the migration and used by the poller,
services, and job counts. Several diagrams use `CANCELED`. Most are prose-only, but
`submissions/view_submission.md` presents the failed-count formula with `CANCELED`; the
actual implementation correctly counts `CANCELLED`.

Affected current-state files include `entities/import_edm.md`,
`packages/delete_package.md`, and `submissions/view_submission.md`.

Recommendation: normalize every RM terminal-status reference to `CANCELLED`.

### F6 — Low — User administration overstates atomic/idempotent provisioning

The administration diagram renders creation and pre-provisioning as cohesive operations.
In implementation, `app_user` and `user_role` are separate autocommit-style calls rather
than one explicit transaction. Pre-provisioning is operationally idempotent in the normal
case, but concurrent requests can still race between the email lookup and insert.

Evidence: `app/routers/admin.py` (`provision_oidc_user`).

Recommendation: either document the non-atomic boundary or wrap user creation and role
assignment in one transaction (and rely on a database uniqueness constraint for races).

### F7 — Low — “Every login attempt” needs a qualification for OIDC aborts

The login document's broad statement that every login attempt is recorded is true for
password submissions, and for successful OIDC callbacks, but not for OIDC state failures,
token-exchange failures, or callbacks lacking an email claim. Those paths log to the
application logger and redirect without inserting `login_attempt`.

Evidence: `app/routers/auth.py` (`oidc_callback`).

Recommendation: say “every password attempt and every successful OIDC callback,” or add
database audit rows for failed OIDC callbacks.

## Coverage summary

No material mismatch was found in these areas:

- submission creation, listing, detail assembly, optimistic edits, reassignment, status
  event sourcing, and CRM-tag behavior;
- EDM/RDM import queueing, per-pair RDM fan-out, submit-failure recording, terminal
  polling, and backfill chaining (apart from F3 and status spelling);
- package save/sync fan-out, RDM-before-EDM deletion ordering, and package finalization;
- EDM portfolio/treaty snapshots, Data Bridge degradation, RDM analysis snapshots, and
  manual sync/revival semantics;
- entity libraries and detail-page HTMX polling, treaty XLSX export, shell navigation,
  health checks, and the documented missing `/api/search` endpoint;
- the classification of analysis, GeoHaz, grouping, result retrieval, Loss Repo export,
  and subportfolio flows under `planned/` as not implemented.

## Verification note

The unit suite was also invoked as corroboration with a workspace-local pytest base temp
directory: **683 passed** (two non-failing warnings). The findings above come from direct
source tracing; passing tests do not negate the uncovered untested branches and
documentation contradictions.
