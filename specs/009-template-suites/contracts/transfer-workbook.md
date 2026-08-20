# Transfer Workbook Contract (009 export/import)

> **Out of MVP scope (spec P-02).** Excel export/import is deferred as a nice-to-have
> enhancement — nothing in this contract is built or seeded in the MVP; setup is manual. This
> document is retained as the worked design for the enhancement; the FR/decision references below
> describe the requirements as they stood when the design was parked.
>
> **Stale since 2026-08-20 (spec P-11):** currency came off templates entirely, so the
> `Currency Scheme` / `Currency Vintage` / `Currency` columns below no longer have template
> columns to map to. A revival of this design must drop them; the rest stands.

One `.xlsx` workbook (openpyxl). Export writes three sheets: `Templates` and `Suites` (the data
sheets — import reads the identical layout) plus `Reference Data` (advisory pick lists —
import recognizes the name and never reads it). Header row is fixed; column order is not
significant on import, but every header must be known — an unrecognized header or sheet name is a
validation error. `Reference Data` is the one optional sheet: present or absent, its
content is ignored.

## Sheet `Templates` — one row per template

Export always writes everything — every live template, including templates no suite references
(FR-016; there is no per-suite selection).

| Header | Type | Required | Maps to |
|---|---|---|---|
| `Name` | text | yes | `analysis_template.name` (import matching key, P-05) |
| `Model Profile` | text | yes | `analysis_profile_name` |
| `Output Profile` | text | yes | `output_profile_name` |
| `Event Rate Scheme` | text | when profile classifies DLM | `event_rate_scheme_name` |
| `Currency Scheme` | text | yes (P-10) | `currency_scheme_code` (P-07); required — templates always store a concrete scheme, never NULL |
| `Currency Vintage` | text | yes — blank fills at import | `currency_vintage` (P-07 — submission vintage code, e.g. "RL25"); blank → filled with the row's scheme's latest cached vintage at import (builder-pre-fill mirror; error when the scheme is unresolved or has no cached vintages) — a concrete vintage is always stored, never NULL |
| `Currency` | text | yes | `currency_code` (P-07 — submission currency code, e.g. "USD") |
| `Min Loss Threshold` | number, 2 dp | yes | `min_loss_threshold` |
| `Num Max Loss Events` | integer | yes | `num_max_loss_event` |
| `Franchise Deductible` | `TRUE`/`FALSE` | yes | `franchise_deductible` |
| `Unrecognized Occupancy` | `Treat as unknown` / `Skip location during analysis` | yes | `treat_construction_occupancy_as_unknown` (R8 mapping) |
| `Tags` | text, `;`-separated | no | `analysis_template_tag.tag_name` rows |

*(2026-08-18: `Treaty Name Pattern` dropped with the template field, spec P-09. `Currency` was
briefly renamed `Currency Scheme`; P-07 as amended stores all three currency values, so the sheet
carries `Currency Scheme`, `Currency Vintage`, and `Currency` columns. 2026-08-19: scheme +
vintage briefly became an optional pair (P-10, blanks = "Default" stored NULL/NULL) — reversed
later the same day: both are required and never stored NULL; a blank scheme is a row error, and
a blank vintage only fills from its scheme's latest cached vintage at import.)*

## Sheet `Suites` — one row per suite item

| Header | Type | Required | Maps to |
|---|---|---|---|
| `Suite Name` | text | yes | `template_suite.name` (matching key) |
| `Template Name` | text | yes — blank only as the single-row empty-suite form (below) | must name a row on the `Templates` sheet **or** an existing live template |

*(2026-08-18: `Position` and `Portfolio Name Override` dropped — suites are unordered plain
membership, spec P-08.)*

A suite with zero items exports as one row with `Template Name` empty (and imports back as an
empty suite).

## Sheet `Reference Data` — advisory pick lists (P-11)

Export-only content: a snapshot of the local reference cache at export time, feeding the dropdown
validations below and giving a hand-author real values to build from (the zero-template bootstrap:
export from an empty environment → headers plus real reference data → author → import). Import
recognizes the sheet name and **never reads it** — validation authority is always the live cache
at import time, never this snapshot.

One list per column, header in row 1, values in rows 2..n sorted alphabetically:

| Header | Values (the import token for the matching `Templates` column) |
|---|---|
| `Model Profiles` | cached model profile names |
| `Output Profiles` | cached output profile names |
| `Event Rate Schemes` | cached event-rate scheme names |
| `Currencies` | cached currency codes |
| `Currency Schemes` | cached currency scheme codes |

Deliberately **no vintages column** — `Currency Vintage` stays free-typed with no dropdown; a
blank vintage next to a scheme fills at import per P-10.

Cell `G1` carries the snapshot stamp: `Synced as of <ISO-8601 UTC>` from the cache's last-synced
time, or `Cache never synced`.

## Dropdown validations (P-11)

Export attaches Excel data validations (openpyxl `DataValidation`, `type="list"`) to rows 2–1000
of the data sheets so added rows keep their dropdowns:

| Sheet / column | Source |
|---|---|
| `Templates` / `Model Profile` | `'Reference Data'!$A$2:$A$<n+1>` |
| `Templates` / `Output Profile` | `'Reference Data'!$B$2:$B$<n+1>` |
| `Templates` / `Event Rate Scheme` | `'Reference Data'!$C$2:$C$<n+1>` |
| `Templates` / `Currency` | `'Reference Data'!$D$2:$D$<n+1>` |
| `Templates` / `Currency Scheme` | `'Reference Data'!$E$2:$E$<n+1>` |
| `Templates` / `Franchise Deductible` | inline list `TRUE,FALSE` |
| `Templates` / `Unrecognized Occupancy` | inline list of the two R8 labels |
| `Suites` / `Template Name` | `Templates!$A$2:$A$1000` (live-updates as template rows are added) |

`Currency Vintage`, `Name`, `Suite Name`, and `Tags` get no validation. A cache list that is
empty gets no validation on its column (an empty-range dropdown is worse than none).

Every validation **warns, never blocks** (`errorStyle="warning"`, `allow_blank` mirroring the
column's Required flag): FR-019/R9 make values absent from the cache legal — a user may type a
profile that exists in Risk Modeler but has not synced, and the import validator (not Excel) is
the authority. No dependent dropdowns: scheme↔profile pairing and vintage↔scheme membership are
enforced by the import validator via the T-06 utility, not by `INDIRECT` gymnastics.

## Import semantics

1. **Validate everything first**: every error collected with `(sheet, row, message)` — missing
   required value, wrong type, duplicate `Name`/`(Suite Name, Template Name)` within the file,
   DLM template without a scheme (classification from the live cache;
   unresolved profile skips the rule per FR-019), scheme whose peril/region does not match the
   profile's when both resolve in the cache — both checks via the T-06 irp-integration validation
   utility, the same rules template save and analysis submit enforce — a blank `Currency Scheme`
   (required, P-10), a vintage that does not belong to its row's scheme when both resolve in
   the cache, a scheme with a blank vintage that cannot be filled (scheme unresolved or
   vintage-less in the cache — P-10), unknown header or sheet (`Reference Data` is recognized
   and skipped, P-11; any other extra sheet name is the error).
2. Any error → apply **nothing**, return the full list (P-04).
3. Clean file → one transaction: templates matched by live name are updated (tags replaced),
   unmatched created; suites matched by name have their item list replaced wholesale — items
   absent from the file are removed — unmatched created (P-05, FR-017).
4. Values absent from the cache (profile/scheme/currency/vintage not synced) import fine and
   surface as unresolved at read time (R9).

## Round-trip invariant (FR-020, SC-004)

`export(all)` → import into an empty environment → `export(all)` produces field-identical
`Templates` and `Suites` sheets (row order normalized by name). `Reference Data` is **excluded**
from the comparison — it is regenerated from the importing environment's cache, which legitimately
differs (P-11). This is the export/import unit test. (The import-time vintage
fill does not disturb it: exports always carry the vintage explicitly (stored NOT NULL), so the
fill only fires on hand-edited files — a hand-authored scheme-with-blank-vintage row re-exports
with the filled vintage, which is the intended builder-mirror behavior, P-10.)
