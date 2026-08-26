# Contract — gateway additions (spec 011)

Two new worker-only functions in `app/services/irp_gateway.py` (protocol +
`_RealGateway` + module-level wrappers), mirrored in
`tests/unit/fakes/fake_irp.py`. Signatures confirmed against the **active**
wheel, irp-integration 0.6.2 (`AnalysisManager`); re-confirm at
implementation — the wheel is pre-release and moves.

## `get_analysis_stats(*, analysis_id: int, perspective_code: str, exposure_resource_id: int) -> list[dict]`

Wraps `client.analysis.get_stats(analysis_id, perspective_code,
exposure_resource_id)` — `GET /platform/riskdata/v1/analyses/{id}/stats` with
`exposureResourceType=PORTFOLIO`. Returns RM's row list verbatim. Row shape
(capture `ep_stats-aal_response`): `analysisId`, `exposureResourceId`,
`exposureResourceType`, `perspectiveCode`, `epType`, `purePremium` (= AAL),
`totalStdDev`, `cv`, plus `-1.0`-filled treaty fields.

## `get_analysis_ep(*, analysis_id: int, perspective_code: str, exposure_resource_id: int) -> list[dict]`

Wraps `client.analysis.get_ep(...)` — `GET .../analyses/{id}/ep`. Returns RM's
element list verbatim: one element per `epType` ∈ {`OEP`, `AEP`, `TCE-OEP`,
`TCE-AEP`}, each with `value.returnPeriods` / `value.positionValues` arrays
(10,004 points, return periods 1–50,000; every stored target present exactly —
capture `ep_curve_response`).

## Constraints

- **Worker-only** (Article 11): callable from `retrieve_analysis_results`
  only; never from a route handler.
- **Perspective validation (T-02)**: the wheel checks `perspective_code`
  against `PERSPECTIVE_CODES` client-side and raises `IRPValidationError` for
  anything outside it. Since 0.6.2 that list is RM's full vocabulary, so all
  five codes pass; the gateway does not (and must not) bypass the wheel.
- The verbatim row lists are the gateway boundary; extraction into the
  [loss-results.md](loss-results.md) shape happens in the worker's pure
  builder, so response-shape knowledge stays in one testable place.

## FakeIRP

- Accepts all five perspective codes from day one.
- Serves configurable per-(analysis, perspective) fixtures shaped like the
  captures (including the TCE elements, so the OEP/AEP filter is exercised);
  default: rows for GR/GU, empty lists otherwise (drives the FR-004
  explicitly-empty path in unit tests).
- Records calls for idempotency assertions (re-fired trigger → zero new
  calls).
