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

**Reversed 2026-08-19 (user-corrected)**: `rmsDefault` is dropped from the model-profile cache and
the Model Profiles metadata tab loses its "Default" column — there is no "default model profile"
concept in Risk Modeler. (`get_output_profiles()`'s `rmsDefault` is unaffected — Output Profiles
keeps its own Default column.)

**Amended 2026-08-18 (design session, note 16 D3; re-amended same day)**: the
`search_currencies()` row **stands** — currencies stay cached because analysis submission needs a
currency code — and two further reads join it when the irp-integration release ships them:
`search_currency_schemes()` and `search_currency_scheme_vintages()` (R13). All four listed reads
stand.

## R2 — DLM/HD classification (closes part of the spec's classification assumption)

The wheel's own submit path (`analysis.py:261`) classifies with `"HD" in
model_profile['softwareVersionCode']` → HD, else DLM, and enforces "event-rate scheme required for
DLM" at submit (`analysis.py:295-296`). Observed `softwareVersionCode` values: `RL18`–`RL25` (DLM,
1,326 profiles), `HDv1.0`–`HDv3.0` (HD, 2,142), and `Open` (6 — Open Modeling framework profiles,
which the wheel's rule classifies DLM).

**Decision**: derive the marker with the wheel's exact rule so template validation always agrees
with what submit will enforce; display the raw `software_version_code` next to the marker.
**Revised 2026-08-18 (T-06)**: the rule is not re-implemented app-side after all — irp-integration
gains a pure classification/validation utility (extracted from the submit path, which today
inlines classification, DLM-requires-scheme, and the peril/region pairing at `analysis.py:246-296`
interleaved with live API calls); the workbench calls it at template save, and the
submit path refactors onto it.
**Landed & validated 2026-08-18**: `irp-integration==0.6.0rc1` (TestPyPI pre-release, pinned via
`make irp-testpypi`) ships `irp_integration.analysis_validation` with
`classify_model_profile(software_version_code) -> "DLM" | "HD"` and
`validate_analysis_settings(software_version_code, scheme_provided, profile_peril_code,
profile_model_region_code, scheme_peril_code=None, scheme_model_region_code=None) -> list[str]`
(empty list = valid). Probe-confirmed: `RL25`→DLM, `HDv3.0`/`HD`→HD; DLM without a scheme returns
the "Event rate scheme is required for DLM analyses" error; peril/region pairing is enforced only
when both scheme codes are supplied (either `None` skips the pair check — matching the plan's
"skipped when a side is absent from the cache"); the wheel's own submit path
(`analysis.py:263,276`) now calls the same functions. Module is pure — imports only `typing`,
needs no `IRPClient`. Caller note: `classify_model_profile` requires a non-null
`software_version_code`; the workbench must guard rows whose cached code is NULL before
classifying.
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

## R6 — Export/import format (T-04) — deferred out of MVP

**Out of MVP scope (spec P-02, 2026-08-19)**: Excel export/import is a nice-to-have enhancement;
the worked design (one openpyxl workbook — `Templates` + `Suites` data sheets plus an advisory
`Reference Data` dropdown sheet) is retained in `contracts/transfer-workbook.md`.

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

FR-011's "flagged unresolved" is a read-time LEFT JOIN from the template's saved names to
the live cache rows — no flag column. A re-sync that removes a profile makes the flag appear; a
later sync that restores it makes the flag disappear, with no write to the template.

## R10 — Starter suites (T-05) — deferred out of MVP

**Out of MVP scope (spec P-02, 2026-08-19)**: nothing is seeded — suites and templates are
created manually via the admin page. If starter content returns, it arrives with the deferred
Excel import enhancement (R6).

## R11 — Template field trim (2026-08-18 review)

`auto_name_pattern` is dropped from `analysis_template`: how Iteration 7 names generated analyses
is decided there (O7-3/O14-9), not stored per template now. `region_label` and `peril_code` are
dropped with it — DATA_MODEL §7 defined them as "display metadata; used in auto-naming", so
removing auto-naming removes their only purpose, and P-03 already establishes that names carry
region. Tags stay: PRD §11.1a lists tags as a per-analysis setting (`tag_names` on submit), and
O14-8 designates `analysis_template_tag` as the mechanism for the LOB axis in suites. DATA_MODEL
§7 reconciliation at implementation covers these deletions.

## R12 — Legacy currency names use the Risk Modeler creation limit

*(Briefly marked superseded on 2026-08-18 when currencies were thought droppable; **reinstated
the same day** — R13 as amended keeps the currency cache, so P-06's truncation stands.)*

Risk Modeler's create-currency screen requires a one-to-three-character code and limits the
currency name to 16 characters. Existing Risk Modeler data can exceed the name limit; one such
row caused SQL Server error 2628 while refreshing `irp_currency`.

**Decision (P-06)**: metadata sync stores the first 16 characters of every currency name. The
gateway still returns the Risk Modeler response unchanged. **Rejected**: failing the entire sync
or omitting the legacy currency, because either choice prevents the cache from supplying the
currency code to analysis templates.

## R13 — Currencies, currency schemes, and scheme vintages are all stored (design session 2026-08-18, note 16 D3/O15-2; amended same day)

CIC works in currency **schemes**: an analysis is tied to a scheme, the currency is pulled from
within it, and the same currency (e.g. EUR) appears in multiple schemes with different FX rates —
the scheme is the selection unit, and only ~2–5 will ever exist. Cheryl: "I don't think we need to
see currencies." But analysis submission requires a specific value from **all three** objects —
the wheel's currency block (`reference_data._build_analysis_currency_dict`, shipped in `0.6.0rc2`) is
`{code, scheme, vintage, asOfDate}`, with `asOfDate` derived from the chosen vintage's
`effectiveDate`.

**Decision (T-07, as amended)**: cache all three sets — keep `irp_currency` (and R12's
truncation), add `irp_currency_scheme` and `irp_currency_scheme_vintage`. The metadata screen's
fourth tab becomes **currency schemes** with their vintages (order: model profiles, output
profiles, event-rate schemes, currency schemes); individual currencies stay cached for the
builder's pick list but get no tab (D3). `analysis_template` keeps `currency_code` and gains
`currency_scheme_code` + `currency_vintage`; the builder defaults the vintage to the scheme's
latest by effective date. The scheme/vintage reads (`search_currency_schemes`,
`search_currency_scheme_vintages`, `get_latest_currency_scheme_vintage`) shipped in
`irp-integration==0.6.0rc2` (released & pinned 2026-08-19, same cross-repo pattern as T-06); the
cache columns and `CurrencySchemeEntry`/`CurrencySchemeVintageEntry` fields are pinned by that
release plus the same-day sandbox probe below. This **resolves O15-2**: the template stores the member
currency *and* the scheme (and the vintage). **Rejected**: replacing currencies with schemes
outright (the first 2026-08-18 reading of D3) — it would have left submission unable to fill
`code`; also rejected: storing `asOfDate` on the template (derivable from the cached vintage;
storing it invites drift).

**Amended 2026-08-19 (spec P-10 / plan T-09; reversed later the same day)**: scheme + vintage
were briefly ruled an optional pair (NULL pair = "Risk Modeler default", displayed "Default" and
resolved workbench-side at submit time), then **reversed the same day**: both are **required** on
every template. NULL is never stored for either and **no default logic runs at submit time** —
every template pins a concrete currency, scheme, and vintage, and Iteration 7 submits them as
stored (the submission API **never defaults these values** — Ben-confirmed: a full
`{code, scheme, vintage, asOfDate}` block must always be sent). The builder pre-selects the
chosen scheme's latest vintage by `effectiveDate` (changeable); a scheme with no vintages blocks
the save. Pick lists filter the **local cache** with substring/LIKE semantics.
**Rejected**: the optional-pair/"Default" design itself (its evergreen submit-time defaults are
traded for explicitness — the admin must consciously pin the scheme and vintage; nothing resolves
behind their back at submit time); live per-keystroke `where_clause` type-ahead against Risk
Modeler (`currencyCode="<input>"` is an exact-match lookup, not a search, and the cache already
holds all three small sets — the where-clause filters belong to the sync); validating
currency-in-scheme membership (the scheme must carry a rate for the chosen currency — real, but
deliberately deferred: currency is a minor slice of the feature and the admin is trusted; a
mispairing fails at submit).

**Probe 2026-08-19 (post-release, CIC sandbox — both reads called raw)**: 45 schemes, 51
vintages. Scheme items carry `currencySchemeId` / `currencySchemeName` / `currencySchemeCode`
(codes non-null, unique, ≤26 chars; names ≤29) plus `anchorCurrencyCode`, `isActive` (2 inactive
rows — the sync filters them out), and `isDefault`, which is true/false/**null** (1/28/16) — not
cached, nothing consumes it after the P-10 reversal (user-decided). Vintage items carry
`vintage`, `currencySchemeCode`, `effectiveDate`, `vintageDescription` + audit and **no id
field**; `(currencySchemeCode, vintage)` is **not unique** (two duplicate pairs of sandbox test
junk observed), and `vintage` values run up to **371 chars** — so the vintage cache is a raw
snapshot (no `irp_id`, no unique index, delete-all + insert per sync — user-decided) and
`vintage` columns are NVARCHAR(400), never truncated (the value must round-trip verbatim into
submission).

**Amended 2026-08-19 (user-directed)**: `anchorCurrencyCode` (probed above but not cached) is
added to `irp_currency_scheme` as a display-only `anchor_currency_code` column, surfaced as
"Anchor Currency" on the Currency Schemes metadata tab. Also added: `updateIntervalInDays` from
`search_currency_schemes`, cached as `update_interval_days` and shown as "Update Interval" —
**not** part of the 2026-08-19 probe above (that probe only exercised the raw wheel-method read);
the field name is user-confirmed rather than probe-verified. Same session: the Currency Schemes
tab's "Open in Risk Modeler ↗" link is split onto its own tenant-relative path,
`home/reference-data/currencies/currency-schemes` (previously it shared the plain `currencies`
tab's `.../currency` path); and the metadata table's vintage badges display `effectiveDate`
truncated to the day (`YYYY-MM-DD`) — the stored `DATETIME2` value and the submission round-trip
are unaffected, this is a display-only truncation in the metadata table row builder.

**Reversed 2026-08-19 (D3's "no tab" call, user-corrected)**: the metadata screen's Currencies
tab is **restored** alongside Currency Schemes — five tabs total (model profiles, output
profiles, event-rate schemes, currencies, currency schemes). D3's assumption that ~2–5 schemes
would make raw currencies uninteresting to browse didn't hold once the probe found 45 schemes
and 51 vintages; the user still wants a plain currency list. `irp_currency` was never dropped
from the cache (it was needed for submission's `code` regardless), so this reversal is UI-only —
the tab, its `_metadata_rows`/`counts` branches, and the RM deep link (same
`home/reference-data/currencies/currency` path as Currency Schemes) all return.

**Reversed 2026-08-20 (design note 17 D4/D5/D7 → spec P-11, plan T-10)**: currency is removed
from templates **entirely** — the third and final flip (optional pair → required NOT NULL → not
stored at all). Analysis currency, scheme, and vintage become **submit-time parameters** in
Iteration 7: chosen per run at the suite level, pre-filled from env-var defaults (USD; latest
RMS scheme; most-recent *currently-effective* vintage — a future-dated vintage never
auto-selects) and always put in front of the analyst; genuinely mixed-currency books run as
separate regional suites (80/20). Rationale: currency baked into a template triplicates
templates per currency and goes stale the moment a new scheme/vintage releases, and CIC — not
the system — decides when the default flips ("I want to flip the switch"). `analysis_template`
drops all three columns; `_validate_currency`, the builder's three currency selects, and the
vintage-options fragment come out with them. The three cache tables, the six-set sync, and both
metadata tabs **stand** — they serve the metadata view now and the Iteration-7 picker later.
O15-2's template-storage half is superseded; the env-var defaults are Iteration-7 app config,
never a modeled table (note 17 D6/O17-3). **Rejected**: keeping the stored values merely as
pre-filled defaults for submit (still stale, still triplicated — the point is that currency is
never hard-linked to a template).

## R14 — Design-session-16 trims (2026-08-18, note 16 D11/§2.1)

**Decisions**: (a) `analysis_template.treaty_name_pattern` is **dropped** (D11/O15-6) — treaties
are selected explicitly at run time in Iteration 7, never stored as a glob on the template; (b)
suites are **unordered** ("it's just a group… that we can run all together") —
`template_suite_item` loses `position` and `portfolio_name_override` (both flagged low-value at
the 8/18 review and confirmed dropped). Display order is normalized by name.
**Rejected**: keeping `position` for stable display (the design note floated it;
the approver decided 2026-08-18 to drop it outright — name-normalized ordering suffices).

## R15 — Duplicate-and-edit replaces Excel as the bulk path (design session 2026-08-20, note 17 D9/D11)

Ben spent ~60% of a day speccing the Excel import/export and hit dependent-dropdown validation,
model-profile metadata, and diff handling; CIC agreed to table Excel (revisit ~next month) and
treat go-live setup as manual. The near-term bulk affordances: **duplicate-and-edit** (this
feature — spec P-12/FR-021, plan T-11) and **direct SQL edits** of non-validated fields such as
names (operational, pending DB write access from Randy; no workbench code).

**Decision (P-12, user-confirmed 2026-08-20)**: Duplicate is persist-then-edit — pressing the
button immediately saves the copy (named `<name> (copy)`, counter on collision, base truncated to
fit NVARCHAR(200)) and opens its edit screen. A suite copy is **shallow**: it copies membership
rows referencing the same templates. A template copy repeats field values and tags; since the
original already passed save validation with identical values, only the name could reject, and
the suffix rule prevents that.
**Rejected**: an unsaved pre-filled "new" form (the user chose save-first: press Duplicate → the
copy exists → edit); deep-copying a suite's templates (templates are shared across suites — the
"swap in an updated model" use case is served by duplicating the *template* and swapping the copy
into the suite); the Excel seed/import itself (still deferred, design retained in
`contracts/transfer-workbook.md` — R6).
