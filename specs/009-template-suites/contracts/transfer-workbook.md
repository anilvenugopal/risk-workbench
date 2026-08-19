# Transfer Workbook Contract (009 export/import)

One `.xlsx` workbook (openpyxl), two sheets. Export writes it; import reads the identical layout.
Header row is fixed; column order is not significant on import, but every header must be known —
an unrecognized header or sheet name is a validation error (FR-018).

The starter-suite seed workbook, `infra/scripts/starter_suites.xlsx`, uses this exact format:
`seed_db.py` feeds it through the same import service (T-05, R10), so the seed file doubles as the
canonical example of the layout.

## Sheet `Templates` — one row per template

Export always writes everything — every live template, including templates no suite references
(FR-016; there is no per-suite selection).

| Header | Type | Required | Maps to |
|---|---|---|---|
| `Name` | text | yes | `analysis_template.name` (import matching key, P-05) |
| `Model Profile` | text | yes | `analysis_profile_name` |
| `Output Profile` | text | yes | `output_profile_name` |
| `Event Rate Scheme` | text | when profile classifies DLM | `event_rate_scheme_name` |
| `Currency Scheme` | text | no — pair with `Currency Vintage` (P-10) | `currency_scheme_code` (P-07); blank pair = Risk Modeler default, stored NULL/NULL, shown as "Default", resolved at submit time |
| `Currency Vintage` | text | when `Currency Scheme` is given | `currency_vintage` (P-07 — submission vintage code, e.g. "RL25"); blank with a scheme → filled with the scheme's latest cached vintage at import (builder-pre-fill mirror; error when the scheme is unresolved or has no cached vintages); given without a scheme → row error |
| `Currency` | text | yes | `currency_code` (P-07 — submission currency code, e.g. "USD") |
| `Min Loss Threshold` | number, 2 dp | yes | `min_loss_threshold` |
| `Num Max Loss Events` | integer | yes | `num_max_loss_event` |
| `Franchise Deductible` | `TRUE`/`FALSE` | yes | `franchise_deductible` |
| `Unrecognized Occupancy` | `Treat as unknown` / `Skip location during analysis` | yes | `treat_construction_occupancy_as_unknown` (R8 mapping) |
| `Tags` | text, `;`-separated | no | `analysis_template_tag.tag_name` rows |

*(2026-08-18: `Treaty Name Pattern` dropped with the template field, spec P-09. `Currency` was
briefly renamed `Currency Scheme`; P-07 as amended stores all three currency values, so the sheet
carries `Currency Scheme`, `Currency Vintage`, and `Currency` columns. 2026-08-19: scheme +
vintage became an optional pair, P-10 — export writes blanks for a "Default" template, and blanks
import back as NULL/NULL.)*

## Sheet `Suites` — one row per suite item

| Header | Type | Required | Maps to |
|---|---|---|---|
| `Suite Name` | text | yes | `template_suite.name` (matching key) |
| `Template Name` | text | yes | must name a row on the `Templates` sheet **or** an existing live template |

*(2026-08-18: `Position` and `Portfolio Name Override` dropped — suites are unordered plain
membership, spec P-08.)*

A suite with zero items exports as one row with `Template Name` empty (and imports back as an
empty suite).

## Import semantics

1. **Validate everything first**: every error collected with `(sheet, row, message)` — missing
   required value, wrong type, duplicate `Name`/`(Suite Name, Template Name)` within the file,
   DLM template without a scheme (classification from the live cache;
   unresolved profile skips the rule per FR-019), scheme whose peril/region does not match the
   profile's when both resolve in the cache — both checks via the T-06 irp-integration validation
   utility, the same rules template save and analysis submit enforce — `Currency Vintage` without
   a `Currency Scheme`, a vintage that does not belong to its row's scheme when both resolve in
   the cache, a scheme with a blank vintage that cannot be filled (scheme unresolved or
   vintage-less in the cache — P-10), unknown header or sheet.
2. Any error → apply **nothing**, return the full list (P-04).
3. Clean file → one transaction: templates matched by live name are updated (tags replaced),
   unmatched created; suites matched by name have their item list replaced wholesale — items
   absent from the file are removed — unmatched created (P-05, FR-017).
4. Values absent from the cache (profile/scheme/currency/vintage not synced) import fine and
   surface as unresolved at read time (R9).

## Round-trip invariant (FR-020, SC-004)

`export(all)` → import into an empty environment → `export(all)` produces field-identical sheets
(row order normalized by name). This is the export/import unit test. (The import-time vintage
fill does not disturb it: exports always carry the vintage explicitly or blank-as-a-pair, so the
fill only fires on hand-edited files — a hand-authored scheme-with-blank-vintage row re-exports
with the filled vintage, which is the intended builder-mirror behavior, P-10.)
