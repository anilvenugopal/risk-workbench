# Implementation Plan: Import Analyses by Risk Modeler Id

**Branch**: `101-import-analyses-by-id` | **Date**: 2026-09-16 | **Issue**: [#101](https://github.com/premiumiq/risk-workbench/issues/101)

<!-- Issue #101 is the spec: it owns the user stories, scope, and requirements.
     This plan exists for the Constitution Check the constitution requires of
     every feature plan. -->

## Plan status

**Built:** Yes — the code landed on this branch before the plan was written.
This plan records the one constitution violation the review found and the
amendment that resolves it.

## Design summary

- The Results grid's summary bar gains an **Import** button on the
  submission's grid only. It opens a modal where the analyst enters one
  `appAnalysisId` at a time — the id the Risk Modeler UI shows, not the
  Platform `analysisId` the API takes.
- `POST /submissions/{sid}/analyses/import/check` resolves one typed id:
  `irp_gateway.resolve_app_analysis_id` searches Risk Modeler for the Platform
  `analysisId`, `irp_gateway.get_analysis_metadata` reads that analysis, and
  `_refuse_if_in_deal` rejects an analysis already in the deal under any
  origin. A checked entry is carried back into the modal as a hidden
  `ImportCandidate`; nothing is written.
- `POST /submissions/{sid}/analyses/import` re-reads each entry's metadata,
  re-checks the deal, and inserts one `irp_analysis` row per entry on the
  `submission_id` leg of `ck_irp_analysis_origin` — `submission_id` set,
  `edm_id`/`rdm_id` NULL, `imported_at` stamped — then enqueues
  `retrieve_analysis_results` for it. Entries are independent: one refusal is
  reported and the rest still import. An enqueue failure soft-deletes the row
  it just inserted, so the analyst can import the id again.
- `imported_at` is the column that separates the two kinds of row on the
  submission leg: a group the Workbench composed, and an analysis the analyst
  pulled in. Delete removes an imported row from the deal only — it never
  calls `DELETE /platform/riskdata/v1/analyses/{id}` against an analysis the
  Workbench did not create.

---

## Constitution Check

**One article was amended for this feature.** Article 11's interface contract
said the web layer MUST NOT call IRP `get_*` methods. Issue #101 requires
"Each ID is validated against Risk Modeler as it is entered", and that
validation needs `get_analysis_metadata` on the request path
(`app/services/analysis_import_service.py`, `check_analysis`). This is the
first `get_*` on a request path in the repo — every other one lives in
`app/workers/`.

The amendment follows the spec-005 precedent (v3.1.0 → v3.2.0, which added the
DataBridge request-path carve-out): **v4.0.0 → v4.1.0**, MINOR — the article
gains a carve-out, nothing is redefined, 13-article numbering stable. The new
clause permits a bounded, single-analysis Platform metadata read on the request
path when it answers a point-of-action validation the analyst is waiting on,
through `irp_gateway`, one analysis per call. Enumerations and
result-retrieval methods (`get_elt`, `get_ep`) stay worker-side;
`poll_*_to_completion` stays forbidden everywhere. Why the read cannot move to
a worker is in [research.md](research.md).

Amending a governing document is not a clean review, and it is the decision a
reviewer should weigh first.

Material interactions — where an article actively shapes this design:

- **Article 3 (Kind Tables)**: no new categorical. `imported_at` is a
  timestamp, not a status; `status_code` on an imported row is the existing
  `irp_analysis_status_kind` value `ready`, matching the broker-row insert in
  `app/workers/entity_jobs.py`.
- **Article 8 (Server-Rendered; No SPA)**: the import dialog is a Jinja2
  fragment fetched over HTMX; checked entries round-trip as hidden inputs, so
  the modal holds no client-side state the server does not render.
- **Article 10 (SQL Table Is the Queue)**: each imported row enqueues one
  existing `retrieve_analysis_results` `rwb_job`, on the existing queue with
  its atomic claim.
- **Article 11 (IRP Behind the Gateway)**: `resolve_app_analysis_id` is a
  search, not a `get_*`, and has precedent on the request path in
  `app/services/name_check.py`. `get_analysis_metadata` is the amended
  clause's permitted call. Results retrieval stays worker-side.

No new architecture guard test. Enforcement of the Article 11 interface
contract stays with review, as it already is for `edm_service.list_edms` and
`name_check.search_edms`.

## Testing

| Tier | What it covers |
|---|---|
| Unit (`tests/unit/test_analysis_import_service.py`, `test_submission_routes.py`) | The per-id check, the duplicate refusals across every origin, the insert, the name suffix, the enqueue and its compensating soft-delete, and the modal routes — against the SQLite WORKBENCH mirror and the fake gateway. |
| SQL Server (`tests/sqlserver`) | The `imported_at` column and `ix_irp_analysis_irp_id` in the real migration. |
| IRP sandbox (`tests/irp/test_import_by_app_analysis_id.py`) | `resolve_app_analysis_id` against a real `appAnalysisId` (`IRP_TEST_APP_ANALYSIS_ID`), and the `LookupError` an unknown id raises. |
