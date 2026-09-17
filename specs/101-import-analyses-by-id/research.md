# Research: Import Analyses by Risk Modeler Id (#101)

Evidence behind the decisions in [plan.md](plan.md).

## T-01 — The analysis metadata read runs on the request path

**Decision**: `check_analysis` and `_import_one`
(`app/services/analysis_import_service.py`) call
`irp_gateway.get_analysis_metadata` from the two import POST handlers, one
analysis per call. Article 11 was amended to v4.1.0 to permit it.

**Rationale**: Issue #101 requires "Each ID is validated against Risk Modeler
as it is entered." The analyst types one id and waits for the answer before
typing the next, so the validation is a point-of-action check on a single
analysis. Three of the four sentences the dialog can show come out of that
read or the search before it: Risk Modeler has no such analysis, it has more
than one, or it has one and here is its name for the analyst to recognize. The
read is bounded by what the analyst typed — one analysis, whatever the size of
the book — which is the property the v3.2.0 DataBridge carve-out already
turned on.

`_import_one` re-reads the same analysis because the insert needs the full
payload for `settings_metadata`; `ImportCandidate` carries six scalar fields,
not the payload. Import therefore costs one `get_analysis_metadata` per entry.

**Alternatives considered**:

- *Enqueue a validation `rwb_job` and poll the modal for its result.* Rejected:
  it answers one id the analyst is already waiting on with a claim, a
  heartbeat, a worker round trip, and a polling fragment, and it moves the
  error sentence away from the field that caused it. This is the same trade the
  v3.2.0 amendment made for the breakout empty-selection count.
- *Validate nothing on entry and report failures after Import.* Rejected: it
  contradicts the issue's requirement, and a mistyped id would be discovered
  only after the analyst had entered the whole list.
- *Carry the whole metadata payload in the hidden `entries` inputs to avoid the
  second read.* Rejected: the payload is an unbounded Risk Modeler document
  round-tripping through a form the client can edit, and the insert would then
  write what the browser posted rather than what Risk Modeler reports.

**Not amended**: enumerations (`search_analyses` over an RDM) and result
retrieval (`get_elt`, `get_ep`) stay worker-side — their result size grows with
the book, not with what the analyst typed. `poll_*_to_completion` stays
forbidden everywhere.

## T-02 — Delete removes an imported row from the deal only

**Decision**: the delete path refuses to call Risk Modeler's analysis delete
for a row with `imported_at` set (`app/services/analysis_service.py`, the
`and not row.imported` guard).

**Rationale**: `_import_one` always writes `irp_id`, so without the guard
Delete would fire `DELETE /platform/riskdata/v1/analyses/{id}` against an
analysis the Workbench never created and that other deals may have imported
too. Removing an imported analysis from a deal is a local row deletion; the
analysis stays in Risk Modeler.

**Known consequence**: the delete confirmation is one sentence for every kind
of row — "Delete the selected analyses? This cannot be undone." An own analysis
is also deleted in Risk Modeler, and the analyst confirms that without the
sentence saying so. The three-branch confirmation that named each case was
dropped as more client-side state than one sentence is worth; if the missing
warning proves to matter, it belongs on the own-analysis path, not in an Alpine
getter.
