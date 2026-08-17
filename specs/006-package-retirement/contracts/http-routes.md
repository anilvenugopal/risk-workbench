# HTTP Contract: Package Retirement

All routes require authentication. State-changing routes require CSRF validation.
Completed and cancelled submissions reject add/detach requests. No route calls Risk
Modeler polling or result-read methods.

## Submission detail

| Method | Path | Response |
|---|---|---|
| GET | `/submissions/{submission_id}` | Full submission page with always-visible EDM and RDM tables. |
| GET | `/submissions/{submission_id}/edms/table` | EDM table partial, including empty/loading/error states. |
| GET | `/submissions/{submission_id}/rdms/table` | RDM table partial, including empty/loading/error states. |

The package list, package card polling, and package modal mount are removed.

## Add a new or existing EDM

| Method | Path | Response |
|---|---|---|
| GET | `/submissions/{submission_id}/edms/add` | Add modal with Import new and Add existing choices. |
| GET | `/submissions/{submission_id}/edms/candidates?q={q}&page={page}` | Paginated candidate partial using case-insensitive name contains. |
| POST | `/submissions/{submission_id}/edms/import` | Save EDM, association, and upload head; return refreshed EDM table or redirect to contextual detail. |
| POST | `/submissions/{submission_id}/edms/attach` | Insert selected associations only; return refreshed EDM table. |
| POST | `/submissions/{submission_id}/edms/{edm_id}/detach` | Delete one association only; return refreshed EDM table. |

Candidate POST predicates require a live EDM with no association to the
target submission. Duplicate IDs are ignored. A stale candidate is reported without
undoing valid selections in the same request.

## Add a new or existing RDM

| Method | Path | Response |
|---|---|---|
| GET | `/submissions/{submission_id}/rdms/add` | Add modal with Import new and Add existing choices. |
| GET | `/submissions/{submission_id}/rdms/candidates?q={q}&page={page}` | Paginated candidate partial using case-insensitive name contains. |
| POST | `/submissions/{submission_id}/rdms/import` | Save RDM, association, and standalone upload head; return refreshed RDM table or redirect to RDM detail. |
| POST | `/submissions/{submission_id}/rdms/attach` | Insert selected associations only; return refreshed RDM table. |
| POST | `/submissions/{submission_id}/rdms/{rdm_id}/detach` | Delete one association only; return refreshed RDM table. |

## Contextual EDM and RDM detail

| Method | Path | Response |
|---|---|---|
| GET | `/submissions/{submission_id}/edms/{edm_id}` | EDM detail with source-submission context, EDM selector, and submission RDM rows. |
| GET | `/submissions/{submission_id}/edms/{edm_id}/body` | Pollable stored EDM-detail partial preserving submission context. |
| POST | `/submissions/{submission_id}/edms/{edm_id}/sync` | Enqueue EDM detail and submission-RDM backfills; return body partial or redirect. |
| POST | `/submissions/{submission_id}/edms/{edm_id}/notes` | Validate the association and update the shared EDM note. |
| GET | `/submissions/{submission_id}/edms/{edm_id}/rdms/{rdm_id}/analyses` | Stored analysis rows for one RDM, loaded on disclosure expand. |
| GET | `/submissions/{submission_id}/rdms/{rdm_id}` | RDM detail with source-submission context and an RDM selector. |
| GET | `/submissions/{submission_id}/rdms/{rdm_id}/body` | Pollable stored RDM-detail partial preserving submission context. |
| POST | `/submissions/{submission_id}/rdms/{rdm_id}/sync` | Enqueue the RDM analysis backfill; return the body partial or redirect. |
| POST | `/submissions/{submission_id}/rdms/{rdm_id}/notes` | Validate the association and update the shared RDM note. |

Every route validates the named association. A missing submission, missing entity, or
entity not related to the submission returns 404. The analyses route also validates
the RDM association to the same submission.

The context link contains only the named submission. Each selector contains only
entities of its type related to the named submission and links to its contextual route.

## Direct library detail

`GET /edms/{edm_id}` remains the EDM Library detail route. It renders no source
submission context, no submission EDM selector, and no submission-scoped RDM list.
`GET /rdms/{rdm_id}` remains the RDM Library detail route and renders no source
submission context or submission RDM selector.

`POST /edms/{edm_id}/notes` and `POST /rdms/{rdm_id}/notes` update the shared
resource note. Each request submits `notes`, `original_notes`, and `csrf_token`.
Notes longer than 250 characters return 422. A changed `original_notes` value
returns 409 with the submitted text and newer saved note. The conflict response
uses the newer note as the next original value, so a second save replaces it.

## Removed routes

Remove every `/packages/...` route and `/submissions/{submission_id}/packages/...`
route. Job-list query parameters no longer accept `package_id`.
