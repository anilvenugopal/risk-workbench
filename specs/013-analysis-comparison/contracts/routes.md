# Contract — Routes (Analysis Comparison)

All routes are read-only (`GET`), issue no Risk Modeler call (Article 11), and
query `rwb_workbench` only through `db.execute` bound parameters (Article 7).

## 1. Compare modal fragment

One handler behind three scope routes, mirroring the analyses-fragment
families the merged section already polls:

| Route | Scope of the list |
|---|---|
| `GET /submissions/{submission_id}/analyses/compare` | Own analyses across the submission's EDMs + all its RDM broker groups |
| `GET /submissions/{submission_id}/edms/{edm_id}/analyses/compare` | Own analyses of the EDM + the submission's RDM broker groups (the contextual table) |
| `GET /edms/{edm_id}/analyses/compare` | Own analyses of the EDM only (the plain table has no submission-scoped RDM groups) |

Behavior:

- Renders `partials/compare_modal.html` into `#compare-modal-mount` — the
  mount sits **outside** the self-polling analyses section (breakout-modal
  precedent), so the 3s poll never removes an open modal.
- The list is `list_comparable_analyses` for the scope (data-model.md):
  every analysis of the table at hand, in table order. Submission-scoped
  routes include own and broker analyses; the plain EDM route includes own
  analyses only.
  Rows with `results_state != 'ready'` are rendered disabled with their state
  named ("retrieving…" / "retrieval failed") — listed, never tickable
  (FR-002). Rows with no recorded run currency are tickable but never
  pairable; the pair-add refusal names the missing currency (P-05).
- Each tickable row carries `data-currency="<code>"` and its id/name; the
  Alpine cart component reads only these attributes (T-02).
- The fragment also receives the entry point's `submission`/`edm` ids so the
  cart's Compare button can build the §2 URL (same values the section's View
  URL carries).
- A scope that no longer resolves (deleted submission/EDM) renders the
  gone-notice fragment, the analyses-section precedent.

Cart rules enforced in the modal (client-side convenience; §2 re-enforces):

- Exactly two ticks arm **Add pair**; the first tick is marked *base*
  (FR-003).
- Add pair is refused, with the reason, when the two `data-currency` values
  differ or either is missing (FR-005, P-05) — the ticks stay so the analyst
  can change a pick.
- A sixth pair is refused (FR-004). Removing a cart row re-arms adding.
- An analysis may sit in any number of pairs; two ticks are necessarily two
  distinct rows, so self-pairing cannot be expressed (P-04).
- **Compare N pairs** opens §2 in a new browser tab via `window.open` (the
  View button's pattern) and the modal closes; the cart is gone with it
  (P-06).

## 2. The comparison page

```
GET /results/comparison
    ?pairs=<baseId>:<secondId>[,<baseId>:<secondId>…]
    [&submission=<uuid>][&edm=<uuid>]
    [&perspective=<code>][&ep_type=OEP|AEP]
```

- **`pairs`** — comma-separated pairs, each `base:second` (UUIDs), in cart
  order. Order within a pair fixes base, column order, and percent-change
  direction (FR-003); order across pairs fixes render order.
- **`submission` / `edm`** — the entry point, exactly as `/results/analyses`
  takes them: submission entry → submission crumb + tab title; EDM entry →
  submission and EDM crumbs, EDM tab title (FR-007). Both link back.
- **`perspective`** (default `analysis_service.DEFAULT_PERSPECTIVE`, Pre-Cat
  Net) and **`ep_type`** (default `OEP`) — screen-wide (FR-012). Unknown
  values fall back to the defaults, the `/results/analyses` rule.

Render:

- Pairs are re-validated server-side (T-01): a pair is **dropped whole** when
  either id fails to parse or resolve, the ids are equal, either run currency
  is unrecorded, or the currencies differ; only the first 5 pairs render.
  Dropped pairs produce one notice above the table naming what was dropped —
  the missing analysis when a side is gone (FR-015), the two currencies when
  the mismatch is the cause (SC-003), and a generic dropped-pair line for
  every other cause — equal ids, an unrecorded currency, an id that does not
  parse, a pair beyond the first 5 (all reachable only by a hand-typed URL).
  Surviving pairs render normally.
- No surviving pairs (or no `pairs` at all) → the empty state directing the
  analyst to Compare on a submission or EDM page (P-06, FR-015). Never a 500,
  never an error page.
- Table shape: one shared return-period column; per pair three columns —
  base, second, **% Chg** — headed by analysis names with each side's run
  currency and engine on the sub-line (FR-008/FR-011). Rows: the 11 stored
  return periods for the selected EP type, then AAL and Std dev outside the
  EP-type selection (FR-010), each row with its percent change (FR-009).
- An absent perspective on one side: that side's column shows the absent
  message, its partner's numbers still render, % Chg is an em dash — never an
  error (FR-014).
- Perspective and EP-type selects re-render `#comparison-view` over HTMX,
  carrying `pairs`/`submission`/`edm` and each other's value — the
  `/results/analyses` toolbar contract. Units and Copy table are the existing
  client slivers; percent cells carry no `data-unit-value` (T-06).
- The whole render reads stored extracts only (FR-016).

## 3. Merged section changes (existing fragments)

- The section summary bar gains **Compare** beside View on every scope in §1.
  It needs no row selection and is always available; the modal reports the
  case where fewer than two of the scope's analyses have retrieved results
  (T-05, FR-001).
- The button `hx-get`s the matching §1 route into `#compare-modal-mount`.
  Nothing else in the section changes; the self-poll contract is untouched.
