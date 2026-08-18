# Research: Analysis Templates & Template Suites (009)

Evidence base: irp-integration 0.2.0 wheel source (`.venv/Lib/site-packages/irp_integration/`) and a
read-only sandbox probe run 2026-08-18 (`reference_data.get_model_profiles / get_output_profiles /
get_event_rate_schemes / search_currencies` against the CIC sandbox tenant).

## R1 — Reference-data volumes and shapes (sandbox probe, 2026-08-18)

| Call | Count | Fields the cache needs |
|---|---|---|
| `get_model_profiles()` | 3,474 | `id`, `name`, `softwareVersionCode`, `perilCode`, `modelRegionCode`, `analysisType`, `peril`, `region`, `rmsDefault` |
| `get_output_profiles()` | 875 | `id`, `name`, `rmsDefault` (also carries a large `metricRequests` JSON — not cached) |
| `get_event_rate_schemes()` | 151 (active only — the wheel filters `isActive=True`) | `eventRateSchemeId`, `eventRateSchemeName`, `perilCode`, `modelRegionCode`, `modelVersionCode`, `isDefault`, `isHD` |
| `search_currencies()` | 266 (all `isActive=True` in sandbox) | `currencyId`, `currencyCode`, `currencyName`, `countryName`, `currencySymbol` |

~4.8k rows total per sync — small enough to refresh in one worker transaction. 3,474 model
profiles confirms the spec's "filterable, just get to UDCT" requirement is not optional.

## R2 — DLM/HD classification (closes part of the spec's classification assumption)

The wheel's own submit path (`analysis.py:261`) classifies with `"HD" in
model_profile['softwareVersionCode']` → HD, else DLM, and enforces "event-rate scheme required for
DLM" at submit (`analysis.py:295-296`). Observed `softwareVersionCode` values: `RL18`–`RL25` (DLM,
1,326 profiles), `HDv1.0`–`HDv3.0` (HD, 2,142), and `Open` (6 — Open Modeling framework profiles,
which the wheel's rule classifies DLM).

**Decision**: derive the marker with the wheel's exact rule so template validation always agrees
with what submit will enforce; display the raw `software_version_code` next to the marker.
**Rejected**: a stored classification column (derivable; would go stale on re-sync) and a
`startswith("RL")` rule (diverges from the wheel for `Open`, inventing a third enforcement path).

## R3 — Accumulation profiles need a new irp-integration read (T-02)

No value in `softwareVersionCode` (R2) or `analysisType` (`Exceedance Probability`, `Scenario`,
`Footprint File`, `Historical`, `Spider-SDF`, `Simple Damage Footprint`, `Ensemble CEP`,
`Non-Runnable`, `User Defined`, `Maximum Historical`, `Maximum Credible`) identifies an
accumulation profile, and wheel 0.2.0 exposes no accumulation-profile read. Risk Modeler keeps
accumulation profiles as a separate resource the integration library does not fetch yet.

**Decision (T-02, approved 2026-08-18)**: irp-integration gains an accumulation-profile read,
built in the `../../IRP/irp-integration` checkout and consumed via `make irp-local` until
published. The `sync_irp_metadata` worker fetches accumulation profiles as a fifth set and stores
them in `irp_model_profile` with `is_accumulation = 1`. Marker: Accumulation when
`is_accumulation`, otherwise the R2 DLM/HD rule. The first task for T-02 is a sandbox spike in the
irp-integration repo to pin the accumulation endpoint and response shape — the accumulation-row
columns in data-model.md are provisional until that spike. **Rejected**: shipping the marker
two-way (FR-004 is approved three-way) and guessing accumulation from profile names.

## R4 — Event-rate pre-fill rule (closes the FR-007 spike)

Event-rate schemes are keyed by `(perilCode, modelRegionCode)` — the wheel resolves a scheme name
to an ID by filtering on the model profile's values (`analysis.py:277-296`). Probe: 31 distinct
`(peril, region)` pairs across the 151 active schemes; 14 pairs have exactly one scheme; of the 17
multi-scheme pairs, 13 have **more than one** `isDefault=True` row — `isDefault` cannot pick a
winner.

**Decision (T-03)**: the builder filters the scheme pick list to the chosen profile's
`(peril_code, model_region_code)` and pre-fills **only when exactly one active scheme matches**
(855 of 3,474 profiles); zero matches (890 profiles, mostly HD) leaves the field empty; multiple
matches shows the filtered list unselected. **Rejected**: `isDefault` (ambiguous, above) and
pre-filling the newest scheme (people are "very picky" about event rates — PRD §11.1a; a silent
guess is worse than no guess).

## R5 — Sync runs as a worker job, not on the request path (T-01)

**Decision (T-01)**: "Sync IRP Metadata" enqueues a `sync_irp_metadata` `rwb_job` (new
`rwb_job_type_kind` row; Dramatiq actor discovered by name). A sync requested while one is
pending or running is refused with a "sync already in progress" message (spec clarification
2026-08-18); the existing `UNIQUE(requestor_type, requestor_id, rwb_job_type)` pending-dedup
index plus a fixed sentinel requestor makes the refusal race-safe, and the single worker
serializes execution. The worker fetches all reference sets, then replaces the cache in one
WORKBENCH transaction (snapshot upsert + hard delete of rows the fetch no longer returned — no
soft delete for cache rows, per 2026-08-18 review) — a failed fetch aborts before any write,
satisfying FR-002's "failed run leaves the previous cache intact". With hard delete, every
surviving row was seen by the last successful sync, so there is no per-row `as_of`; the metadata
page's last-synced time comes from the latest succeeded `sync_irp_metadata` rwb_job.
**Rejected**: inline on the request path (the EDM-sync precedent, `edm_service.list_adoptable_edms`)
— that read is justified there by staleness ("a stale list would offer an EDM that is already
gone"); metadata has no such constraint, inline gives no concurrency guard, and a ~4.8k-row
refresh doesn't belong in an HTTP handler.

## R6 — Export/import format is one .xlsx workbook, two sheets (T-04)

**Decision (T-04)**: openpyxl (already a dependency — treaty export, spec 004 R5) writes one
workbook: a `Templates` sheet (one row per template, full field set, tags semicolon-joined) and a
`Suites` sheet (one row per suite item: suite name, position, template name, portfolio-name
override). Import parses the same layout with `UploadFile` (python-multipart already present).
**Rejected**: CSV — two related record sets don't fit one flat file, and the spec's requirement is
"opens in Excel"; two separate CSVs would make the all-or-nothing import contract awkward.

## R7 — Tags are stored as names

Wheel 0.2.0 has `get_tag_by_name` / `create_tag` / `get_tag_ids_from_tag_names` but **no list-all
tags read** (verified 2026-08-17 against the installed wheel; PRD §15.2's `get_tags()` does not
exist). Risk Modeler resolves names to IDs and creates missing tags at submit time
(`get_tag_ids_from_tag_names`). **Decision**: `analysis_template_tag.tag_name` (NVARCHAR), replacing
DATA_MODEL §7's `irp_tag_id`; no `irp_tag` cache table in this iteration.

## R8 — Analysis-settings defaults come from the wheel signature

`submit_portfolio_analysis_job` defaults: `min_loss_threshold=1.0`, `num_max_loss_event=1`,
`treat_construction_occupancy_as_unknown=True`, `franchise_deductible=False`. The spec's
"Unrecognized Occupancy Types" binary maps to the boolean API parameter: "Treat as unknown" =
`True`, "Skip location during analysis" = `False`. The builder pre-fills these values.

## R9 — Unresolved references are derived, never stored

FR-011/FR-019's "flagged unresolved" is a read-time LEFT JOIN from the template's saved names to
the live cache rows — no flag column. A re-sync that removes a profile makes the flag appear; a
later sync that restores it makes the flag disappear, with no write to the template.

## R10 — Starter suites seed through the import flow (T-05, revised 2026-08-18)

The four starter suites (US, Canada, US+Canada, Global, ~10 templates each, indicative settings
per P-02 built from `RMS Default *` profile names observed in the sandbox) live in a seed
workbook, `infra/scripts/starter_suites.xlsx`, in the transfer-workbook format (R6).
`infra/scripts/seed_db.py` imports it through the same service the `POST /templates/import` route
uses — one data file instead of hardcoded rows, and Cheryl's default-settings list (O14-4) lands
as a workbook edit, not a code change. The seed skips the import entirely when any live template
suite exists (one EXISTS check), because import's match-by-name update semantics (P-05) would
otherwise overwrite CIC's edits on the idempotent re-seed. At seed time the metadata cache is
empty, so every profile is unresolved and the DLM-scheme rule is skipped (FR-019) — the import
succeeds without a prior sync. **Rejected**: hardcoded rows in `seed_db.py` (a second
representation of the same data, superseded by 2026-08-18 review) and MERGE-style upsert (correct
for reference vocabularies, wrong for admin-editable data).

## R11 — Template field trim (2026-08-18 review)

`auto_name_pattern` is dropped from `analysis_template`: how Iteration 7 names generated analyses
is decided there (O7-3/O14-9), not stored per template now. `region_label` and `peril_code` are
dropped with it — DATA_MODEL §7 defined them as "display metadata; used in auto-naming", so
removing auto-naming removes their only purpose, and P-03 already establishes that names carry
region. Tags stay: PRD §11.1a lists tags as a per-analysis setting (`tag_names` on submit), and
O14-8 designates `analysis_template_tag` as the mechanism for the LOB axis in suites. DATA_MODEL
§7 reconciliation at implementation covers these deletions.
