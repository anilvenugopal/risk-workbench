"""Route tests for the breakout modal + confirm (spec 005 T041 —
FR-001/FR-006/FR-006c/FR-007/FR-002a/FR-002b).

Owns the HTTP surface over the REAL service and a real (SQLite) WORKBENCH:
modal states, the untruncated preview list, the three overlap forms, the
FR-006c large-fan-out statement, CSRF, the four 409 refusal variants (each
writing NO job row), the persisted plan, enqueue idempotency, the body-partial
success response, and the 404 fragment.

Harness: unlike the monkeypatch-based route suites, these tests need the DB —
TestClient dispatches handlers on a worker thread, so the engine uses
``StaticPool`` (one shared connection) instead of the per-thread pool the
``iteration2_db`` fixture builds.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from db import execute, execute_command
from db.connection import register_engine
from tests.iteration1_mirror import (
    BREAKOUT_DIMENSION_SEED,
    IRP_ANALYSIS_STATUS_SEED,
    IRP_JOB_RESOURCE_TYPE_SEED,
    IRP_JOB_TYPE_SEED,
    ITERATION1_SCHEMA,
    ITERATION2_SCHEMA,
    ITERATION3_SCHEMA,
    RWB_JOB_REQUESTOR_TYPE_SEED,
    RWB_JOB_STATUS_SEED,
    RWB_JOB_TYPE_SEED,
    STATUS_SEED,
    TREATY_SEED,
)
from tests.unit.test_breakout_gate import (
    AS_OF,
    RM_STAMP,
    SUMMARY,
    _breakout_jobs,
    _mk_backfill_job,
    _mk_breakout_job,
    _mk_edm,
    _mk_portfolio,
)

ANALYST_ID = "analyst-1"


@pytest.fixture()
def routes_db() -> SimpleNamespace:
    """The iteration2 WORKBENCH schema on a ``StaticPool`` engine, so the
    TestClient worker thread and the test thread share one connection."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        for ddl in (*ITERATION1_SCHEMA, *ITERATION2_SCHEMA, *ITERATION3_SCHEMA):
            conn.execute(text(ddl))
        conn.execute(text(
            "INSERT INTO app_user (id, email, display_name) "
            "VALUES (:a, 'analyst@example.com', 'Analyst')"), {"a": ANALYST_ID})
        for table, rows in (
            ("submission_status_kind", STATUS_SEED),
            ("treaty_type_kind", TREATY_SEED),
            ("irp_job_type_kind", IRP_JOB_TYPE_SEED),
            ("irp_job_resource_type_kind", IRP_JOB_RESOURCE_TYPE_SEED),
            ("rwb_job_type_kind", RWB_JOB_TYPE_SEED),
            ("rwb_job_requestor_type_kind", RWB_JOB_REQUESTOR_TYPE_SEED),
            ("rwb_job_status_kind", RWB_JOB_STATUS_SEED),
            ("irp_analysis_status_kind", IRP_ANALYSIS_STATUS_SEED),
            ("breakout_dimension_kind", BREAKOUT_DIMENSION_SEED),
        ):
            for code, label, order in rows:
                conn.execute(text(
                    f"INSERT INTO {table} (code, label, sort_order) "
                    "VALUES (:c, :l, :o)"), {"c": code, "l": label, "o": order})
    register_engine("WORKBENCH", engine)
    yield SimpleNamespace(engine=engine, user_a=ANALYST_ID)
    engine.dispose()


class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.services.auth_service import CurrentUser
        request.state.user = CurrentUser(
            id=ANALYST_ID, email="analyst@example.com", display_name="Analyst",
            session_id="s", role_codes=["analyst"], is_admin=False,
            must_change_password=False, entra_oid=None, is_active=True)
        return await call_next(request)


@pytest.fixture()
def client() -> TestClient:
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import portfolios

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(portfolios.router)
    return TestClient(app, follow_redirects=False)


def _csrf() -> str:
    from app.auth.csrf import generate_csrf_token
    return generate_csrf_token()


def _url(edm_id: str, pid: str) -> str:
    return f"/edms/{edm_id}/portfolios/{pid}/breakout"


def _eligible_pair(fake_irp) -> tuple[str, str]:
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)
    return edm_id, pid


def _confirm(client, edm_id: str, pid: str, *, dimension: str = "lob",
             as_of: str = AS_OF, htmx: bool = True, csrf: str | None = None):
    return client.post(
        _url(edm_id, pid),
        data={"dimension": dimension, "summary_as_of": as_of,
              "csrf_token": csrf if csrf is not None else _csrf()},
        headers={"HX-Request": "true"} if htmx else {})


# ── GET — modal states ─────────────────────────────────────────────────────────────

def test_modal_eligible_renders_list_count_and_hidden_as_of(routes_db, client):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    r = client.get(_url(edm_id, pid))
    assert r.status_code == 200
    # header: source name, RM id, and the stored account total (P-13)
    assert "usfl_commercial · Portfolio #1 · 1,701 accounts" in r.text
    # LOB is the first eligible dimension → selected; both choosers render
    assert 'aria-pressed="true"' in r.text
    assert "By line of business" in r.text
    assert "By geography (state)" in r.text
    # preview list: value, generated name, account count per row (FR-006)
    assert "usfl_commercial - EQ Comm" in r.text
    assert "usfl_commercial - FLD Comm" in r.text
    assert ">900<" in r.text and ">801<" in r.text
    # the count + confirm button, and the FR-002b hidden field
    assert "sub-portfolios will be created" in r.text
    assert "Create 2 sub-portfolios" in r.text
    assert f'name="summary_as_of" value="{AS_OF}"' in r.text
    # below the threshold: no several-minutes statement (FR-006c)
    assert "large run" not in r.text


def test_modal_dimension_param_selects_state(routes_db, client):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    r = client.get(_url(edm_id, pid) + "?dimension=state")
    assert r.status_code == 200
    # state values with their labels, sorted by value; generated names use
    # the label, never the bare code (P-12 as revised 2026-08-05)
    assert "CALIFORNIA" in r.text and "TEXAS" in r.text
    assert "usfl_commercial - CALIFORNIA" in r.text
    assert "usfl_commercial - TEXAS" in r.text
    assert 'name="dimension" value="state"' in r.text


def test_modal_quick_chooser_never_offers_peril(routes_db, client):
    # P-19: peril is grouping-only — no pill in the quick-mode chooser even
    # when the stored summary carries multiple peril values, and a
    # hand-crafted confirm refuses with 409 and no job row.
    edm_id = _mk_edm()
    summary = dict(SUMMARY, breakout_values=dict(
        SUMMARY["breakout_values"],
        peril=[{"value": "1", "label": None, "accounts": 517},
               {"value": "2", "label": None, "accounts": 1701}]))
    pid = _mk_portfolio(edm_id, summary=summary)

    r = client.get(_url(edm_id, pid))
    assert r.status_code == 200
    assert "By peril" not in r.text
    assert "By line of business" in r.text and "By geography (state)" in r.text

    refused = _confirm(client, edm_id, pid, dimension="peril")
    assert refused.status_code == 409
    assert _breakout_jobs() == []


def test_modal_marks_existing_rows_as_already_created(routes_db, client):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    execute_command(
        "INSERT INTO irp_portfolio (id, edm_id, name, irp_id, "
        "source_portfolio_id, breakout_dimension_code, breakout_value, "
        "inserted_at, updated_at) "
        "VALUES (:i, :e, 'usfl_commercial - EQ Comm', '11', :s, 'lob', "
        "'EQ Comm', :now, :now)",
        {"i": str(uuid.uuid4()), "e": edm_id, "s": pid,
         "now": datetime.utcnow()}, connection="WORKBENCH")
    r = client.get(_url(edm_id, pid))
    assert "already created" in r.text


def test_modal_large_fanout_untruncated_with_several_minutes_note(
        routes_db, client):
    # 40 LOB values: every row renders (no truncation) + the FR-006c statement.
    values = [{"value": f"LOB {i:02d}", "label": None, "accounts": 10}
              for i in range(40)]
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, summary=dict(
        SUMMARY, breakout_values={"lob": values}))
    r = client.get(_url(edm_id, pid))
    assert r.status_code == 200
    for i in range(40):
        assert f"usfl_commercial - LOB {i:02d}" in r.text
    assert "40 sub-portfolios is a large run" in r.text
    assert "several minutes" in r.text


def test_modal_overlap_statement_three_forms(routes_db, client):
    # The same value set against three coverage readings forces each arm
    # (FR-007). The value counts are identical throughout — only the measured
    # coverage moves, which is the point: Σ accounts cannot tell these apart.
    edm_id = _mk_edm()
    values = {"lob": [{"value": "A", "label": None, "accounts": 700},
                      {"value": "B", "label": None, "accounts": 600}]}
    heavy = dict(SUMMARY, account_total=1000, breakout_values=values,
                 breakout_coverage={"lob": {"covered": 1000,
                                            "multi_value": 300}})
    pid = _mk_portfolio(edm_id, summary=heavy)
    flat = " ".join(client.get(_url(edm_id, pid)).text.split())
    assert "Warning: overlapping accounts" in flat
    assert ("300 of 1,000 accounts match more than one line of business and "
            "are included in full in each matching sub-portfolio." in flat)
    # P-21: the explanatory prose is cut
    assert "inflation" not in flat
    assert "tend to be the largest" not in flat

    clean = dict(SUMMARY, account_total=1300, breakout_values=values,
                 breakout_coverage={"lob": {"covered": 1300,
                                            "multi_value": 0}})
    pid2 = _mk_portfolio(edm_id, name="clean", irp_id="2", summary=clean)
    flat2 = " ".join(client.get(_url(edm_id, pid2)).text.split())
    assert ("No overlapping accounts — none of the 1,300 accounts that carry "
            "a line of business matches more than one." in flat2)
    assert "Warning" not in flat2

    absent = {k: v for k, v in heavy.items() if k != "breakout_coverage"}
    pid3 = _mk_portfolio(edm_id, name="absent", irp_id="3", summary=absent)
    flat3 = " ".join(client.get(_url(edm_id, pid3)).text.split())
    # qualitative sentence alone — no count is invented from the value totals
    assert ("Accounts matching more than one line of business are included "
            "in full in each matching sub-portfolio." in flat3)
    assert "match more than one" not in flat3


def test_modal_no_repeats_but_uncovered_accounts_is_not_a_clean_partition(
        routes_db, client):
    # The case summed − account_total reported as a clean partition: 100 of
    # 1,701 accounts carry a state and none carries two.
    edm_id = _mk_edm()
    summary = dict(SUMMARY, breakout_values={
        "state": [{"value": "TX", "label": None, "accounts": 60},
                  {"value": "CA", "label": None, "accounts": 40}],
        "lob": SUMMARY["breakout_values"]["lob"]},
        breakout_coverage={"state": {"covered": 100, "multi_value": 0}})
    pid = _mk_portfolio(edm_id, summary=summary)
    flat = " ".join(
        client.get(_url(edm_id, pid) + "?dimension=state").text.split())
    assert ("No overlapping accounts — none of the 100 accounts that carry a "
            "state matches more than one." in flat)
    assert ("1,601 of 1,701 accounts carry no state value and are left out."
            in flat)
    assert "None left out" not in flat


def test_modal_disclosure_prose_is_cut_in_every_form(routes_db, client):
    # P-21 (D11): the two short quantified lines replace the multi-sentence
    # explanation — no exposure-inflation sentences, no geography paragraphs.
    edm_id = _mk_edm()
    # SUMMARY's state coverage is every account, none repeating → the zero arm
    pid = _mk_portfolio(edm_id)
    flat = " ".join(
        client.get(_url(edm_id, pid) + "?dimension=state").text.split())
    assert ("No overlapping accounts — none of the 1,701 accounts that carry "
            "a state matches more than one." in flat)

    # quantified arm: measured repeats > 0
    heavy = dict(SUMMARY, breakout_coverage={"state": {"covered": 1701,
                                                       "multi_value": 201}})
    pid2 = _mk_portfolio(edm_id, name="heavy", irp_id="2", summary=heavy)
    flat2 = " ".join(
        client.get(_url(edm_id, pid2) + "?dimension=state").text.split())
    assert ("201 of 1,701 accounts match more than one state and are "
            "included in full in each matching sub-portfolio." in flat2)

    # qualitative arm: no breakout_coverage
    absent = {k: v for k, v in SUMMARY.items() if k != "breakout_coverage"}
    pid3 = _mk_portfolio(edm_id, name="absent", irp_id="3", summary=absent)
    flat3 = " ".join(
        client.get(_url(edm_id, pid3) + "?dimension=state").text.split())
    assert ("Accounts matching more than one state are included in full in "
            "each matching sub-portfolio." in flat3)

    for rendered in (flat, flat2, flat3):
        assert "commercial account" not in rendered
        assert "inflation" not in rendered
        assert "several states" not in rendered


def test_modal_state_large_fanout_untruncated_with_note(routes_db, client):
    # US2 (T047/FR-006c): a 43-division state fan-out renders every row —
    # values with null labels render the code alone (un-geocoded EDM) — plus
    # the several-minutes statement; nothing refused for size (P-15).
    values = [{"value": f"S{i:02d}", "label": None, "accounts": 5}
              for i in range(43)]
    summary = dict(SUMMARY, breakout_values={
        "state": values, "lob": SUMMARY["breakout_values"]["lob"]})
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, summary=summary)
    r = client.get(_url(edm_id, pid) + "?dimension=state")
    assert r.status_code == 200
    for i in range(43):
        assert f"usfl_commercial - S{i:02d}" in r.text
    assert "bo-row__label" not in r.text     # null labels → code alone
    assert "43 sub-portfolios is a large run" in r.text
    assert "several minutes" in r.text
    assert "Create 43 sub-portfolios" in r.text


def test_modal_blank_value_disclosure_states_the_measured_shortfall(
        routes_db, client):
    # FR-007(b): SUMMARY's lob coverage is 1,641 of 1,701 accounts, so 60 carry
    # no line of business and land in no sub-portfolio — stated as a number.
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    flat = " ".join(client.get(_url(edm_id, pid)).text.split())
    assert ("60 of 1,701 accounts carry no line of business value and are "
            "left out." in flat)
    # the state dimension covers every account → the positive form
    flat_state = " ".join(
        client.get(_url(edm_id, pid) + "?dimension=state").text.split())
    assert ("None left out — every account carries a state value."
            in flat_state)


def test_modal_blank_value_disclosure_stays_qualitative_without_coverage(
        routes_db, client):
    # A summary written before the 2026-08-05 revision carries no
    # breakout_coverage: the fixed sentence, no invented number.
    edm_id = _mk_edm()
    no_coverage = {k: v for k, v in SUMMARY.items() if k != "breakout_coverage"}
    pid = _mk_portfolio(edm_id, summary=no_coverage)
    flat = " ".join(client.get(_url(edm_id, pid)).text.split())
    assert ("Accounts with no line of business value are left out." in flat)
    assert "carry no line of business value" not in flat


def test_modal_missing_summary_disables_both_with_sync_pointer(
        routes_db, client):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, detail=None, as_of=None)
    r = client.get(_url(edm_id, pid))
    assert r.status_code == 200
    assert r.text.count("bo-dim--disabled") == 2
    assert "exposure summary not available" in r.text
    assert "run Sync" in r.text
    assert f'hx-post="/edms/{edm_id}/sync"' in r.text
    # no confirm form when nothing is selectable
    assert 'name="dimension"' not in r.text


def test_modal_single_value_dimension_disabled_with_reason(routes_db, client):
    edm_id = _mk_edm()
    summary = dict(SUMMARY, breakout_values={
        "lob": [{"value": "FLD Comm", "label": None, "accounts": 1701}],
        "state": SUMMARY["breakout_values"]["state"]})
    pid = _mk_portfolio(edm_id, summary=summary)
    r = client.get(_url(edm_id, pid))
    assert "only one line of business present" in r.text
    # the state dimension is still confirmable
    assert 'name="dimension" value="state"' in r.text


def test_modal_breakout_in_flight_replaces_chooser(routes_db, client):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    _mk_breakout_job(pid, "lob", status="running")
    r = client.get(_url(edm_id, pid))
    assert "breakout is already running for this portfolio" in r.text
    assert "bo-spinner" in r.text
    assert "bo-dims" not in r.text          # chooser replaced
    assert 'name="dimension"' not in r.text  # no confirm form


def test_modal_sync_in_flight_disables_with_reason(routes_db, client):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    _mk_backfill_job(edm_id)
    r = client.get(_url(edm_id, pid))
    assert "this EDM is syncing — the exposure summary is being rewritten" in r.text
    assert "Close this dialog" in r.text
    assert 'name="dimension"' not in r.text


def test_modal_missing_portfolio_renders_404_fragment(routes_db, client):
    edm_id = _mk_edm()
    r = client.get(_url(edm_id, str(uuid.uuid4())))
    assert r.status_code == 404
    assert 'id="breakout-modal"' in r.text   # a fragment, not an error page
    assert "no longer exists" in r.text
    deleted = _mk_portfolio(edm_id, deleted=True)
    assert client.get(_url(edm_id, deleted)).status_code == 404


# ── POST — CSRF, success, refusals ─────────────────────────────────────────────────

def test_confirm_csrf_failure_htmx_refreshes_and_nojs_redirects(
        routes_db, client, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    r = _confirm(client, edm_id, pid, csrf="bogus")
    assert r.status_code == 204
    assert r.headers["HX-Refresh"] == "true"
    r2 = _confirm(client, edm_id, pid, csrf="bogus", htmx=False)
    assert r2.status_code == 303
    assert _breakout_jobs() == []


def test_confirm_success_returns_body_partial_with_toast_and_plan(
        routes_db, client, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    r = _confirm(client, edm_id, pid)
    assert r.status_code == 200
    # the EDM body partial, retargeted at the page wrapper
    assert 'id="edm-detail"' in r.text
    assert r.headers["HX-Retarget"] == "#edm-detail"
    assert r.headers["HX-Reswap"] == "outerHTML"
    toast = json.loads(r.headers["HX-Trigger"])["rwb:toast"]
    assert toast["message"] == "Breakout started — 2 sub-portfolios"
    # one job with the approved plan persisted (FR-006a)
    jobs = _breakout_jobs()
    assert len(jobs) == 1
    plan = json.loads(jobs[0]["input_data"])["plan"]
    assert [(e["value"], e["accounts"]) for e in plan] == [
        ("EQ Comm", 801), ("FLD Comm", 900)]
    # the body shows the in-flight indicator on the source row
    assert "bo-spinner" in r.text
    assert "0 of 2" in r.text


def test_confirm_double_post_yields_one_job_and_409(routes_db, client, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    assert _confirm(client, edm_id, pid).status_code == 200
    second = _confirm(client, edm_id, pid)
    assert second.status_code == 409
    assert "already running" in second.text
    assert len(_breakout_jobs()) == 1


def test_confirm_gate_refusal_409_with_no_job_row(routes_db, client, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    execute_command("UPDATE irp_edm SET status = 'importing' WHERE id = :e",
                    {"e": edm_id}, connection="WORKBENCH")
    r = _confirm(client, edm_id, pid)
    assert r.status_code == 409
    assert "the EDM is not ready" in r.text
    assert _breakout_jobs() == []


def test_confirm_stale_stamp_409_with_sync_pointer_and_no_job_row(
        routes_db, client, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    fake_irp.set_portfolio_stamp(edm_exposure_id="90001", irp_id="1",
                                 stamp="2026-08-04T08:00:00.000Z")
    r = _confirm(client, edm_id, pid)
    assert r.status_code == 409
    assert "Portfolio data has changed in Risk Modeler" in r.text
    assert f'hx-post="/edms/{edm_id}/sync"' in r.text  # "Sync the EDM" action
    # the stale refusal offers no second confirm
    assert 'name="dimension"' not in r.text
    assert _breakout_jobs() == []


def test_confirm_rewritten_summary_409_rerenders_fresh_preview(
        routes_db, client, fake_irp):
    # FR-002b with a MATCHING stamp: the confirm carries a different as_of than
    # the stored summary → 409, fresh preview (new hidden as_of), no job row.
    edm_id, pid = _eligible_pair(fake_irp)
    r = _confirm(client, edm_id, pid, as_of="2026-08-02 09:00:00")
    assert r.status_code == 409
    assert "synced while you were reviewing" in r.text
    assert "confirm again" in r.text
    # the re-render is a full fresh preview carrying the CURRENT as_of
    assert f'name="summary_as_of" value="{AS_OF}"' in r.text
    assert "Create 2 sub-portfolios" in r.text
    assert _breakout_jobs() == []
    assert fake_irp.stamp_reads == []       # refused before the RM read


def test_confirm_nojs_success_is_prg(routes_db, client, fake_irp):
    edm_id, pid = _eligible_pair(fake_irp)
    r = _confirm(client, edm_id, pid, htmx=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/edms/{edm_id}"
    assert len(_breakout_jobs()) == 1


# ── custom grouping (follow-on FR-018–021) ────────────────────────────────────────

GROUP_SUMMARY = dict(SUMMARY, breakout_values=dict(
    SUMMARY["breakout_values"],
    peril=[{"value": "1", "label": None, "accounts": 517},
           {"value": "2", "label": None, "accounts": 1701}]))


def _custom_pair(fake_irp) -> tuple[str, str]:
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, summary=GROUP_SUMMARY)
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="1",
                           name="usfl_commercial", stamp=RM_STAMP)
    return edm_id, pid


def _add_group(client, edm_id, pid, *, label="Coastal",
               selections=None, carted=(), csrf=None):
    data = {"csrf_token": csrf if csrf is not None else _csrf(),
            "group_label": label}
    for dim, values in (selections or {"state": ["TX"]}).items():
        data[f"values:{dim}"] = list(values)
    if carted:
        data["group"] = [json.dumps(g) for g in carted]
    return client.post(_url(edm_id, pid) + "/group-preview", data=data,
                       headers={"HX-Request": "true"})


def _confirm_cart(client, edm_id, pid, groups, *, as_of=AS_OF, csrf=None,
                  htmx=True):
    data = {"csrf_token": csrf if csrf is not None else _csrf(),
            "summary_as_of": as_of}
    if groups:
        data["group"] = [g if isinstance(g, str) else json.dumps(g)
                         for g in groups]
    return client.post(_url(edm_id, pid) + "/groups", data=data,
                       headers={"HX-Request": "true"} if htmx else {})


def _group_row_ids() -> list[str]:
    return [r["id"] for r in execute(
        "SELECT id FROM breakout_group", {}, connection="WORKBENCH")]


def test_modal_custom_mode_renders_pills_checkboxes_and_cart(
        routes_db, client, fake_irp):
    edm_id, pid = _custom_pair(fake_irp)
    r = client.get(_url(edm_id, pid) + "?mode=custom")
    assert r.status_code == 200
    # mode tabs, with Custom selected
    assert "Quick breakout" in r.text and "Custom breakouts" in r.text
    # every eligible dimension is a pill — peril included (P-19)
    assert "Peril" in r.text
    # every dimension's checkboxes arrive in this one fetch (T-15)
    for name in ('name="values:lob"', 'name="values:state"',
                 'name="values:peril"'):
        assert name in r.text
    # no quick preview list, no quick confirm
    assert "Generated name" not in r.text
    assert 'name="dimension"' not in r.text
    assert "No breakouts yet" in r.text
    assert f'action="{_url(edm_id, pid)}/groups"' in r.text
    # the group-name input carries the as-you-type check (P-25) and the
    # 40-char RM name limit directly (P-24)
    assert f'hx-get="{_url(edm_id, pid)}/name-check"' in r.text
    assert 'maxlength="40"' in r.text


def test_modal_custom_mode_disables_single_value_pill(routes_db, client):
    summary = dict(GROUP_SUMMARY, breakout_values=dict(
        GROUP_SUMMARY["breakout_values"],
        lob=[{"value": "FLD Comm", "label": None, "accounts": 1701}]))
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id, summary=summary)
    r = client.get(_url(edm_id, pid) + "?mode=custom")
    flat = " ".join(r.text.split())
    assert ('disabled title="only one line of business present"'
            in flat)
    assert 'name="values:lob"' not in r.text     # no checkboxes for it


def test_group_preview_returns_cart_row_with_hidden_json(
        routes_db, client, fake_irp):
    edm_id, pid = _custom_pair(fake_irp)
    r = _add_group(client, edm_id, pid, label="Coastal HU",
                   selections={"state": ["TX", "CA"], "peril": ["2"]})
    assert r.status_code == 200
    assert "Coastal HU" in r.text                        # the label as typed (P-24)
    assert "usfl_commercial - Coastal HU" not in r.text  # no composed prefix
    assert 'name="group"' in r.text
    # upper bound = min(Σ state counts, Σ peril counts) = min(1701, 1701)
    assert "up to 1,701 accounts" in r.text
    flat = " ".join(r.text.split())
    assert "peril: 2 · state: CA, TX" in flat    # canonical filter line
    # preview writes NOTHING — no group row, no job
    assert _group_row_ids() == []
    assert _breakout_jobs() == []


def test_group_preview_blocks_cart_duplicate_and_warns_overlap(
        routes_db, client, fake_irp):
    edm_id, pid = _custom_pair(fake_irp)
    carted = ({"label": "Coastal HU", "filters": {"state": ["TX"]}},)
    dup = _add_group(client, edm_id, pid, label="Coastal HU",
                     selections={"state": ["TX", "CA"]}, carted=carted)
    assert dup.status_code == 409                        # blocked, never suffixed (P-25)
    assert "already exists in the cart" in dup.text
    ok = _add_group(client, edm_id, pid, label="Inland",
                    selections={"state": ["TX", "CA"]}, carted=carted)
    assert ok.status_code == 200
    assert "may overlap with Coastal HU" in ok.text      # shared TX (P-18)


def test_group_preview_blocks_name_taken_in_rm(routes_db, client, fake_irp):
    # A portfolio Risk Modeler holds but the workbench has no row for — the
    # Add-time RM leg (P-25) refuses it.
    edm_id, pid = _custom_pair(fake_irp)
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="77", name="Coastal")
    r = _add_group(client, edm_id, pid, label="Coastal")
    assert r.status_code == 409
    assert "already exists in this EDM" in r.text
    assert _group_row_ids() == []


def test_group_preview_shows_the_name_as_typed_for_adopted_sets(
        routes_db, client, fake_irp):
    # Re-adding a confirmed member set adopts the row (P-22 — no duplicate)
    # but the cart shows the name exactly as typed, and the set's own
    # approved name is never refused (the re-confirm heal path).
    edm_id, pid = _custom_pair(fake_irp)
    groups = [{"label": "Coastal", "filters": {"state": ["TX"]}}]
    assert _confirm_cart(client, edm_id, pid, groups).status_code == 200
    fake_irp.add_portfolio(edm_exposure_id="90001", irp_id="88", name="Coastal")

    r = _add_group(client, edm_id, pid, label="Fresh name",
                   selections={"state": ["TX"]})
    assert r.status_code == 200
    assert "Fresh name" in r.text and "Coastal" not in r.text
    assert "existing breakout" in r.text

    r2 = _add_group(client, edm_id, pid, label="Coastal",
                    selections={"state": ["TX"]})
    assert r2.status_code == 200


def test_breakout_name_check_renders_the_collision_fragment(
        routes_db, client, fake_irp):
    edm_id, pid = _custom_pair(fake_irp)
    url = _url(edm_id, pid) + "/name-check"
    blocked = client.get(url + "?group_label=usfl_commercial")
    assert blocked.status_code == 200
    assert 'data-nc="blocked"' in blocked.text
    flat = " ".join(blocked.text.split())
    assert "a portfolio with this name already exists in this EDM" in flat
    assert "Adding is blocked" in flat
    ok = client.get(url + "?group_label=Fresh")
    assert 'data-nc="ok"' in ok.text
    assert "this EDM" in ok.text
    pending = client.get(url + "?group_label=%20")
    assert "data-nc" not in pending.text


def test_group_preview_refusal_retargets_the_error_slot(
        routes_db, client, fake_irp):
    edm_id, pid = _custom_pair(fake_irp)
    r = _add_group(client, edm_id, pid, selections={"state": ["ZZ"]})
    assert r.status_code == 409
    assert r.headers["HX-Retarget"] == "#bo-cart-error"
    assert "unknown state value" in r.text

    r2 = _add_group(client, edm_id, pid, label="   ")
    assert r2.status_code == 409
    assert "needs a name" in r2.text

    r3 = _add_group(client, edm_id, pid, csrf="bogus")
    assert r3.status_code == 204
    assert r3.headers["HX-Refresh"] == "true"


def test_cart_confirm_success_rows_jobs_and_toast(routes_db, client, fake_irp):
    edm_id, pid = _custom_pair(fake_irp)
    r = _confirm_cart(client, edm_id, pid, [
        {"label": "A", "filters": {"state": ["TX"]}},
        {"label": "B", "filters": {"lob": ["EQ Comm"], "peril": ["2"]}},
    ])
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "#edm-detail"
    toast = json.loads(r.headers["HX-Trigger"])["rwb:toast"]
    assert toast["message"] == "Breakout started — 2 sub-portfolios"
    jobs = _breakout_jobs()
    assert len(jobs) == 2
    assert {j["requestor_type"] for j in jobs} == {"breakout_group"}
    assert set(_group_row_ids()) == {j["requestor_id"] for j in jobs}
    # the page shows the cart flight on the source row
    assert "0 of 2" in r.text


def test_cart_confirm_refusals_write_nothing(routes_db, client, fake_irp):
    edm_id, pid = _custom_pair(fake_irp)
    groups = [{"label": "A", "filters": {"state": ["TX"]}}]

    r = _confirm_cart(client, edm_id, pid, groups,
                      as_of="2001-01-01 00:00:00")
    assert r.status_code == 409
    assert "synced while you were reviewing" in r.text

    r2 = _confirm_cart(client, edm_id, pid, ["not-json"])
    assert r2.status_code == 409
    assert "cart row is malformed" in r2.text

    r3 = _confirm_cart(client, edm_id, pid, [])
    assert r3.status_code == 409
    assert "cart is empty" in r3.text

    r4 = _confirm_cart(client, edm_id, pid, groups, csrf="bogus")
    assert r4.status_code == 204

    assert _breakout_jobs() == []
    assert _group_row_ids() == []


def test_cart_confirm_while_running_is_409(routes_db, client, fake_irp):
    edm_id, pid = _custom_pair(fake_irp)
    groups = [{"label": "A", "filters": {"state": ["TX"]}}]
    assert _confirm_cart(client, edm_id, pid, groups).status_code == 200
    second = _confirm_cart(client, edm_id, pid, groups)
    assert second.status_code == 409
    assert "already running" in second.text
    assert len(_breakout_jobs()) == 1
    # ... and the live cart blocks the QUICK confirm too (FR-020)
    quick = _confirm(client, edm_id, pid)
    assert quick.status_code == 409
    assert len(_breakout_jobs()) == 1
