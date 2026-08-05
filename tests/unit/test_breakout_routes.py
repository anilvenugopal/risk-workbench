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

from db import execute_command
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
    # The same value set against three denominators forces each arm (FR-007).
    edm_id = _mk_edm()
    heavy = dict(SUMMARY, account_total=1000, breakout_values={
        "lob": [{"value": "A", "label": None, "accounts": 700},
                {"value": "B", "label": None, "accounts": 600}]})
    pid = _mk_portfolio(edm_id, summary=heavy)
    flat = " ".join(client.get(_url(edm_id, pid)).text.split())
    assert ("300 of this portfolio's 1,000 accounts match more than one "
            "line of business" in flat)
    assert "included in full in each one" in flat
    assert "tend to be the largest" in flat

    clean = dict(SUMMARY, account_total=1300,
                 breakout_values=heavy["breakout_values"])
    pid2 = _mk_portfolio(edm_id, name="clean", irp_id="2", summary=clean)
    flat2 = " ".join(client.get(_url(edm_id, pid2)).text.split())
    assert ("None of this portfolio's 1,300 accounts match more than one "
            "line of business" in flat2)
    assert "partition the source cleanly" in flat2

    absent = {k: v for k, v in heavy.items() if k != "account_total"}
    pid3 = _mk_portfolio(edm_id, name="absent", irp_id="3", summary=absent)
    flat3 = " ".join(client.get(_url(edm_id, pid3)).text.split())
    # qualitative sentence alone; the header omits the account count too
    assert "so the sub-portfolios can overlap" in flat3
    assert "absent · Portfolio #3</span>" in flat3


def test_modal_geography_disclosure_states_multi_state_consequence(
        routes_db, client):
    # US2 (FR-007/T047): every overlap form of the state dimension states the
    # multi-state-account consequence explicitly; the lob forms never do.
    edm_id = _mk_edm()
    # SUMMARY's state counts (220 + 1,481) equal account_total → partition arm
    pid = _mk_portfolio(edm_id)
    flat = " ".join(
        client.get(_url(edm_id, pid) + "?dimension=state").text.split())
    assert ("None of this portfolio's 1,701 accounts match more than one "
            "state" in flat)
    assert ("A commercial account with locations in several states would "
            "land whole in every state sub-portfolio it touches; here none "
            "does." in flat)

    # quantified arm: a lower denominator forces repeats > 0
    heavy = dict(SUMMARY, account_total=1500)
    pid2 = _mk_portfolio(edm_id, name="heavy", irp_id="2", summary=heavy)
    flat2 = " ".join(
        client.get(_url(edm_id, pid2) + "?dimension=state").text.split())
    assert ("201 of this portfolio's 1,500 accounts match more than one "
            "state" in flat2)
    assert ("a commercial account with locations in several states lands "
            "<strong>whole</strong> in every state sub-portfolio it touches"
            in flat2)

    # qualitative arm: no account_total
    absent = {k: v for k, v in SUMMARY.items() if k != "account_total"}
    pid3 = _mk_portfolio(edm_id, name="absent", irp_id="3", summary=absent)
    flat3 = " ".join(
        client.get(_url(edm_id, pid3) + "?dimension=state").text.split())
    assert "so the sub-portfolios can overlap" in flat3
    assert "lands <strong>whole</strong> in every state sub-portfolio" in flat3

    # the lob dimension carries no multi-state sentence in any form
    flat_lob = " ".join(client.get(_url(edm_id, pid)).text.split())
    assert "several states" not in flat_lob


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


def test_modal_blank_value_disclosure(routes_db, client):
    edm_id = _mk_edm()
    pid = _mk_portfolio(edm_id)
    flat = " ".join(client.get(_url(edm_id, pid)).text.split())
    assert ("Exposure with no line of business value is not included in any "
            "sub-portfolio." in flat)


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
