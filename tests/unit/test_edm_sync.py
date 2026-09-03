"""Unit tests for the manual per-EDM Sync action (spec 004 Addendum A, T056).

FR-003 as amended 2026-07-23: automatic backfill stays forward-only, but the EDM
detail page's Sync button re-runs ``backfill_edm_detail`` on demand.
``edm_service.sync_detail`` keys the head ``(analyst_request, edm_id)`` via
``ensure_pending_rwb_job`` (revives a terminal row) + ``dispatch``;
``latest_backfill_status`` reads BOTH that key and the poller's
``irp_job``-keyed rows (newest ``updated_at`` wins) so ``detail_state`` and
``EdmDetail.sync_running`` stay truthful whichever path ran last. The worker
name-resolves a missing exposureId (pre-capability EDMs) — stricter than the
poller: zero or ambiguous hits skip gracefully rather than taking the newest.
Routes: ``POST /edms/{edm_id}/sync`` (CSRF; HTMX swap of the live
``#edm-detail`` body, PRG fallback) + ``GET /edms/{edm_id}/body`` (the
self-terminating poll target).
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.config import settings
from app.poller import run as poller
from app.services import edm_service
from app.workers import dispatch, entity_jobs
from db import execute, execute_command, execute_one
from tests.unit.test_backfill_edm_detail import EXPOSURE_A


def _edm_ready(drive, fake, actor, name="EDM") -> str:
    """Import a standalone EDM and drive it to ``ready`` (submit → FINISHED →
    poll). Leaves the poller-enqueued backfill head PENDING (undrained)."""
    res = edm_service.import_edm(name=name, source_file_path=str(drive / "edm1.bak"),
                                 actor_id=actor)
    entity_jobs.run_pending(worker_id="w1")
    row = execute_one(
        "SELECT irp_id FROM irp_job WHERE irp_edm_id=:e AND irp_job_type='import_edm'",
        {"e": res.entity_id}, connection="WORKBENCH")
    fake.finish(str(row["irp_id"]))
    poller.poll_once()
    return res.entity_id


def _legacy_edm(*, name="legacy_edm", irp_id=None) -> str:
    """A pre-capability EDM: ``ready``, no backfill head ever enqueued, no
    ``as_of`` — exactly the forward-only gap the Sync button exists for."""
    eid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO irp_edm (id, name, status, source_file_path, irp_id, "
        "inserted_at, updated_at) VALUES (:i, :n, 'ready', '/share/legacy.bak', "
        ":x, '2026-01-01', '2026-01-01')",
        {"i": eid, "n": name, "x": irp_id}, connection="WORKBENCH")
    return eid


def _analyst_heads(edm_id: str) -> list[dict]:
    return execute(
        "SELECT id, status_code, attempt_count, updated_by FROM rwb_job "
        "WHERE requestor_type='analyst_request' AND requestor_id=:r "
        "AND rwb_job_type='backfill_edm_detail'",
        {"r": edm_id}, connection="WORKBENCH")


# ── sync_detail: enqueue, dispatch, revive, guards ────────────────────────────────

def test_sync_enqueues_analyst_head_and_dispatches(iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    entity_jobs.run_pending(worker_id="w1")  # drain the poller head → succeeded

    sent: list[tuple[str, str]] = []
    dispatch.configure(lambda *, rwb_job_id, rwb_job_type: sent.append(
        (rwb_job_id, rwb_job_type)))
    try:
        job_id = edm_service.sync_detail(edm_id=edm_id,
                                         actor_id=iteration2_db.user_a)
    finally:
        dispatch.reset()

    assert job_id is not None
    heads = _analyst_heads(edm_id)
    assert len(heads) == 1
    assert heads[0]["status_code"] == "pending"
    assert sent == [(str(job_id), "backfill_edm_detail")]


def test_sync_revives_terminal_head_with_attempt_and_actor(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    entity_jobs.run_pending(worker_id="w1")
    first = edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    entity_jobs.run_pending(worker_id="w1")  # analyst head → succeeded

    again = edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_b)
    assert again == first  # the SAME row revived, not a duplicate
    heads = _analyst_heads(edm_id)
    assert len(heads) == 1
    assert heads[0]["status_code"] == "pending"
    assert heads[0]["attempt_count"] == 1
    assert str(heads[0]["updated_by"]) == str(iteration2_db.user_b)


def test_sync_skips_while_backfill_in_flight_either_key(
        iteration2_db, fake_irp, drive):
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    # The poller-enqueued (irp_job-keyed) head is still pending — Sync must not
    # stack a second concurrent run on top of it.
    assert edm_service.sync_detail(edm_id=edm_id,
                                   actor_id=iteration2_db.user_a) is None
    assert _analyst_heads(edm_id) == []

    entity_jobs.run_pending(worker_id="w1")
    assert edm_service.sync_detail(edm_id=edm_id,
                                   actor_id=iteration2_db.user_a) is not None
    # ... and an in-flight analyst head blocks a re-click the same way.
    assert edm_service.sync_detail(edm_id=edm_id,
                                   actor_id=iteration2_db.user_a) is None
    assert len(_analyst_heads(edm_id)) == 1


def test_sync_noop_when_edm_importing_or_missing(iteration2_db, fake_irp, drive):
    res = edm_service.import_edm(name="EDM", source_file_path=str(drive / "edm1.bak"),
                                 actor_id=iteration2_db.user_a)  # pending_import
    assert edm_service.sync_detail(edm_id=res.entity_id,
                                   actor_id=iteration2_db.user_a) is None
    assert _analyst_heads(res.entity_id) == []
    assert edm_service.sync_detail(edm_id=str(uuid.uuid4()),
                                   actor_id=iteration2_db.user_a) is None


# ── detail_state visibility through the analyst-keyed head ────────────────────────

def test_sync_populates_pre_capability_edm_with_visible_states(
        iteration2_db, fake_irp, drive):
    edm_id = _legacy_edm(irp_id=77001)
    fake_irp.add_portfolio(edm_exposure_id=77001, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "unavailable"
    assert detail.sync_running is False

    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "pending"  # analyst-keyed head IS visible
    assert detail.sync_running is True

    entity_jobs.run_pending(worker_id="w1")
    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "populated"
    assert detail.sync_running is False
    assert [p.name for p in detail.portfolios] == ["Primary 2026"]


def test_sync_failure_shows_failed_state_and_is_recoverable(
        iteration2_db, fake_irp, drive):
    edm_id = _legacy_edm(irp_id=77002)
    fake_irp.add_portfolio(edm_exposure_id=77002, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    fake_irp.raise_on_list_portfolios = True
    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    entity_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "failed"
    assert detail.status == edm_service.READY  # FR-005 — never reverted

    fake_irp.raise_on_list_portfolios = False
    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    entity_jobs.run_pending(worker_id="w1")
    assert edm_service.get_edm_detail(edm_id).detail_state == "populated"


def test_detail_state_prefers_most_recently_updated_head(
        iteration2_db, fake_irp, drive):
    # Poller head succeeded (zero-portfolio EDM → "empty"), then a newer manual
    # sync goes pending — the page must say "pending", not stale "empty".
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    entity_jobs.run_pending(worker_id="w1")
    assert edm_service.get_edm_detail(edm_id).detail_state == "empty"

    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "pending"
    assert detail.sync_running is True


def test_backfill_status_sees_breakout_fired_heads_quick_and_group(iteration2_db):
    # A completed breakout auto-fires backfill_edm_detail keyed on its own
    # run_breakout_* job row (FR-013). That job's requestor is the source
    # portfolio for a quick breakout, but the breakout_group row for a custom
    # group — both must resolve to the EDM, single and batched read alike.
    def _portfolio(edm_id: str) -> str:
        pid = str(uuid.uuid4())
        execute_command(
            "INSERT INTO irp_portfolio (id, edm_id, name, irp_id, inserted_at, "
            "updated_at) VALUES (:i, :e, 'src', '1', '2026-01-01', '2026-01-01')",
            {"i": pid, "e": edm_id}, connection="WORKBENCH")
        return pid

    def _job(requestor_type: str, requestor_id: str, job_type: str,
             status: str) -> str:
        jid = str(uuid.uuid4())
        execute_command(
            "INSERT INTO rwb_job (id, requestor_type, requestor_id, "
            "rwb_job_type, status_code, attempt_count, inserted_at, updated_at) "
            "VALUES (:i, :rt, :r, :t, :s, 1, '2026-01-01', '2026-01-01')",
            {"i": jid, "rt": requestor_type, "r": requestor_id, "t": job_type,
             "s": status}, connection="WORKBENCH")
        return jid

    quick_edm = _legacy_edm(name="quick_edm")
    quick_job = _job("analyst_request", _portfolio(quick_edm),
                     "run_breakout_lob", "succeeded")
    _job("rwb_job", quick_job, "backfill_edm_detail", "pending")

    group_edm = _legacy_edm(name="group_edm")
    group_row_id = str(uuid.uuid4())
    execute_command(
        "INSERT INTO breakout_group (id, source_portfolio_id, group_key, "
        "label, filters, name, number, cart_id, inserted_at, updated_at) "
        "VALUES (:i, :p, 'k1', 'Coastal', :f, 'src - Coastal', 'src-Coastal', "
        ":c, '2026-01-01', '2026-01-01')",
        {"i": group_row_id, "p": _portfolio(group_edm),
         "f": '{"state": ["FL"]}', "c": str(uuid.uuid4())},
        connection="WORKBENCH")
    group_job = _job("breakout_group", group_row_id,
                     "run_breakout_custom", "succeeded")
    _job("rwb_job", group_job, "backfill_edm_detail", "pending")

    assert edm_service.latest_backfill_status(quick_edm) == "pending"
    assert edm_service.latest_backfill_status(group_edm) == "pending"
    assert edm_service.latest_backfill_statuses([quick_edm, group_edm]) == {
        quick_edm: "pending", group_edm: "pending"}


# ── the Risk Modeler treaties deep link (Treaties polish, 2026-07-24) ─────────────

def test_detail_carries_rm_treaties_deep_link(iteration2_db, monkeypatch):
    # https://<RISK_MODELER_TENANT_NAME>.<rm-domain>/riskmodeler/datasources/
    # <edm-name>/treaties — the RM web UI lives on the TENANT subdomain of the
    # API base URL's domain (rms-ppe.com in the sandbox, rms.com in prod), NOT
    # on the API host itself. The EDM name is URL-encoded; a missing tenant or
    # base URL yields None (link hidden).
    edm_id = _legacy_edm(name="townsend edm")
    monkeypatch.setattr(settings, "risk_modeler_base_url",
                        "https://api-euw1.rms-ppe.com/")
    monkeypatch.setattr(settings, "risk_modeler_tenant_name", "acme")
    assert edm_service.get_edm_detail(edm_id).rm_treaties_url == (
        "https://acme.rms-ppe.com/riskmodeler/datasources/townsend%20edm/treaties")

    monkeypatch.setattr(settings, "risk_modeler_tenant_name", "")
    assert edm_service.get_edm_detail(edm_id).rm_treaties_url is None


# ── worker: pre-capability EDMs without an exposureId (name resolution) ───────────

def test_sync_resolves_missing_exposure_id_by_name(iteration2_db, fake_irp, drive):
    fake_irp.add_edm_name("legacy_named")
    xid = fake_irp.search_edms("legacy_named")[0].irp_id
    fake_irp.add_portfolio(edm_exposure_id=xid, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    edm_id = _legacy_edm(name="legacy_named", irp_id=None)

    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    entity_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "populated"
    assert detail.irp_id == int(xid)  # exposureId resolved by name AND persisted
    assert len(_analyst_heads(edm_id)) == 1
    assert _analyst_heads(edm_id)[0]["status_code"] == "succeeded"


def test_sync_skips_gracefully_when_name_unresolvable(
        iteration2_db, fake_irp, drive):
    edm_id = _legacy_edm(name="unknown_edm", irp_id=None)  # not in RM at all
    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    entity_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "unavailable"  # succeeded-as-skip, no as_of
    assert detail.irp_id is None
    assert _analyst_heads(edm_id)[0]["status_code"] == "succeeded"


# ── route: POST /edms/{edm_id}/sync + GET /edms/{edm_id}/body (live UX) ───────────
#
# The route contract is tested with edm_service monkeypatched: the fixture SQLite
# engine is thread-local and TestClient dispatches handlers on a worker thread, and
# the service behavior is already covered above — the route tests only own the HTTP
# surface. An HTMX POST swaps the
# ``#edm-detail`` body partial, which self-polls ``GET /edms/{id}/body`` every 3s
# while the backfill head is in flight and stops emitting the trigger once it lands.
# The no-JS fallback is Post/Redirect/Get, so a refresh never re-prompts the form.

class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.services.auth_service import CurrentUser
        request.state.user = CurrentUser(
            id="analyst-1", email="analyst@example.com", display_name="Analyst",
            session_id="s", role_codes=["analyst"], is_admin=False,
            must_change_password=False, entra_oid=None, is_active=True)
        return await call_next(request)


def _client() -> TestClient:
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import edms
    from app.services.breakout_service import display_value
    from app.templating import TEMPLATE_DIRS

    app = FastAPI()
    templates = Jinja2Templates(directory=TEMPLATE_DIRS)
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    templates.env.filters["breakout_display"] = display_value
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(edms.router)
    return TestClient(app, follow_redirects=False)


def _detail_obj(**over) -> edm_service.EdmDetail:
    base = dict(
        id="edm-1", name="legacy_edm", status="ready", as_of=None,
        source_file_path="/share/legacy.bak", irp_id=77001,
        created_by_irp_job_irp_id=None,
        inserted_at="2026-01-01", updated_at="2026-01-01",
        portfolio_count=0, portfolios=[], detail_state="unavailable",
        sync_running=False)
    base.update(over)
    return edm_service.EdmDetail(**base)


def test_sync_route_bad_csrf_redirects_without_service_call(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(edm_service, "sync_detail",
                        lambda **kw: calls.append(kw))
    r = _client().post("/edms/edm-1/sync", data={"csrf_token": "garbage"})
    assert r.status_code == 303
    assert r.headers["location"] == "/edms/edm-1"
    assert calls == []


def test_sync_route_nonhtmx_post_redirects_prg(monkeypatch):
    # No-JS fallback: Post/Redirect/Get back to the canonical URL — the browser
    # never parks on /sync, so refreshing never re-prompts a form re-submission.
    calls: list[dict] = []
    monkeypatch.setattr(edm_service, "sync_detail",
                        lambda **kw: calls.append(kw) or "job-1")
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/edms/edm-1/sync",
                       data={"csrf_token": generate_csrf_token()})
    assert r.status_code == 303
    assert r.headers["location"] == "/edms/edm-1"
    assert calls == [{"edm_id": "edm-1", "actor_id": "analyst-1"}]


def test_sync_route_htmx_returns_live_body_partial(monkeypatch):
    # HTMX path: the POST swaps the #edm-detail wrapper in place (URL untouched);
    # the swapped-in render shows the disabled Syncing… button AND carries the
    # self-poll trigger because the head is now in flight.
    calls: list[dict] = []
    monkeypatch.setattr(edm_service, "sync_detail",
                        lambda **kw: calls.append(kw) or "job-1")
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(detail_state="pending",
                                                   sync_running=True))
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/edms/edm-1/sync",
                       data={"csrf_token": generate_csrf_token()},
                       headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert calls == [{"edm_id": "edm-1", "actor_id": "analyst-1"}]
    assert 'id="edm-detail"' in r.text
    assert 'hx-get="/edms/edm-1/body"' in r.text and "every 3s" in r.text
    assert "Syncing" in r.text and "disabled" in r.text
    assert "</html>" not in r.text  # a partial — no shell around it


def test_sync_route_htmx_bad_csrf_forces_full_refresh(monkeypatch):
    # A stale/garbage token on the HTMX path must not swap a nested full page
    # into the wrapper — HX-Refresh reloads the page (and its fresh tokens).
    calls: list[dict] = []
    monkeypatch.setattr(edm_service, "sync_detail",
                        lambda **kw: calls.append(kw))
    r = _client().post("/edms/edm-1/sync", data={"csrf_token": "garbage"},
                       headers={"HX-Request": "true"})
    assert r.status_code == 204
    assert r.headers["hx-refresh"] == "true"
    assert calls == []


def test_body_poll_partial_polls_while_running_then_stops(monkeypatch):
    # In flight → the wrapper emits its own every-3s poll; landed → the trigger
    # disappears (polling self-terminates) and the fresh synced stamp is shown.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(detail_state="pending",
                                                   sync_running=True))
    html = _client().get("/edms/edm-1/body").text
    assert 'hx-get="/edms/edm-1/body"' in html and "every 3s" in html

    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(detail_state="populated",
                                                   as_of="2026-07-24 10:00:00"))
    html = _client().get("/edms/edm-1/body").text
    assert "every 3s" not in html
    assert "synced" in html
    # The stamp is a <time data-utc> element: app.js rewrites it to the
    # browser's local timezone (h:mm:ss AM/PM), re-run after every HTMX swap.
    assert '<time data-utc="2026-07-24 10:00:00"' in html
    assert ">Sync</button>" in html  # the button is offered again, enabled


def test_body_poll_populated_mid_sync_returns_204_no_swap(monkeypatch):
    # Re-syncing an already-populated page: a 3s outerHTML swap would collapse
    # every <details> the analyst opened, so the poll target answers 204 (htmx
    # swaps nothing, the poll keeps ticking) until the sync lands — then the
    # fresh body renders exactly once (trigger gone, poll ends).
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(detail_state="populated",
                                                   sync_running=True,
                                                   as_of="2026-07-24 10:00:00"))
    r = _client().get("/edms/edm-1/body")
    assert r.status_code == 204

    # The sync POST itself still renders the body — it wires the poll up.
    monkeypatch.setattr(edm_service, "sync_detail", lambda **kw: "job-1")
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/edms/edm-1/sync",
                       data={"csrf_token": generate_csrf_token()},
                       headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "every 3s" in r.text


def test_body_poll_partial_live_while_importing(monkeypatch):
    # The same wrapper keeps the page fresh through an in-flight import too —
    # The import statuses remain live while the worker runs.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(status="pending_import",
                                                   detail_state="importing"))
    html = _client().get("/edms/edm-1/body").text
    assert "every 3s" in html


def test_body_poll_partial_when_edm_gone(monkeypatch):
    # EDM hard-gone mid-poll: swap in a terminal notice with no trigger, so
    # polling ends instead of 404-looping.
    monkeypatch.setattr(edm_service, "get_edm_detail", lambda edm_id: None)
    r = _client().get("/edms/edm-1/body")
    assert r.status_code == 200
    assert "no longer exists" in r.text
    assert "every 3s" not in r.text


# ── The breakout-episode poll: the Portfolios section, not the whole body (T-11) ──
#
# #edm-detail is the page's scrolling element (.shell is height:100vh, overflow
# hidden), so the spec-005 whole-body swap threw the analyst back to the top of
# the page every 3 seconds. A breakout changes only the Portfolios section, so
# that section polls GET /edms/{id}/portfolios-section and the body stays quiet.

def _banner(**over):
    from app.services.breakout_service import BreakoutBanner
    base = dict(source_name="cbhu", noun="line of business", created=3,
                adopted=0, skipped_existing=0, failed=0, ok=True,
                filling_in=True, error=None)
    base.update(over)
    return BreakoutBanner(**base)


def test_breakout_run_polls_the_section_not_the_body(monkeypatch):
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="populated", breakout_running=True,
                            as_of="2026-08-05 10:00:00"))
    html = _client().get("/edms/edm-1/body").text
    assert 'hx-get="/edms/edm-1/portfolios-section"' in html
    assert 'hx-get="/edms/edm-1/body"' not in html   # the scroller is not swapped


def test_body_poll_204_while_a_breakout_fills_figures_in(monkeypatch):
    # The FR-013 follow-up backfill sets sync_running, so the body poll is live
    # again — it must still answer 204 on a populated page. The section poll is
    # what shows the figures landing.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="populated", sync_running=True,
                            breakout_banner=_banner(),
                            as_of="2026-08-05 10:00:00"))
    assert _client().get("/edms/edm-1/body").status_code == 204


def test_section_poll_emits_trigger_and_oob_fragments_while_running(monkeypatch):
    # The poll response is the section plus one out-of-band swap: the header
    # meta line carries a portfolio count that moves as the worker creates
    # sub-portfolios.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="populated", portfolio_count=4,
                            breakout_running=True,
                            as_of="2026-08-05 10:00:00"))
    r = _client().get("/edms/edm-1/portfolios-section")
    assert r.status_code == 200
    assert 'id="edm-portfolios"' in r.text and "every 3s" in r.text
    assert 'id="edm-detail-meta" hx-swap-oob="true"' in r.text
    assert "4 portfolios" in r.text
    assert 'id="edm-detail"' not in r.text     # never the scrolling wrapper
    assert "</html>" not in r.text             # a fragment — no shell


def test_section_poll_stops_when_the_breakout_episode_is_terminal(monkeypatch):
    # Run terminal and the follow-up backfill landed: the trigger disappears,
    # so the section's poll self-terminates (the body-poll precedent).
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="populated", portfolio_count=4,
                            breakout_banner=_banner(filling_in=False, failed=1,
                                                    ok=False),
                            as_of="2026-08-05 10:00:00"))
    html = _client().get("/edms/edm-1/portfolios-section").text
    assert "every 3s" not in html
    assert "1 failed" in html                  # the durable partial-run banner


def test_section_poll_when_edm_gone(monkeypatch):
    monkeypatch.setattr(edm_service, "get_edm_detail", lambda edm_id: None)
    r = _client().get("/edms/edm-1/portfolios-section")
    assert r.status_code == 200
    assert "no longer exists" in r.text
    assert "every 3s" not in r.text


def test_page_render_carries_the_section_and_oob_targets_without_oob_attrs(
        monkeypatch):
    # The ids exist on the full page render (they are the OOB targets), but
    # hx-swap-oob appears only in the poll response — on a page load it would
    # be inert markup, and on a body swap a nested one is ignored.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="populated", portfolio_count=4,
                            as_of="2026-08-05 10:00:00"))
    html = _client().get("/edms/edm-1").text
    for anchor in ('id="edm-portfolios"', 'id="edm-detail-meta"',
                   'id="edm-treaties"'):
        assert anchor in html
    assert "hx-swap-oob" not in html


def test_expanded_row_lineage_on_generated_rows_only(monkeypatch):
    # FR-014 as revised 2026-08-11: the collapsed table row carries only the
    # Breakout marker; expanding a generated row shows the base portfolio and
    # the breakout criteria in the Risk Modeler description format — the
    # dimension label + display value for a quick breakout, the AND-joined
    # filter set for a custom group. Base rows render neither.
    from app.services.portfolio_service import PortfolioRow
    common = dict(edm_id="edm-1", exposure_detail=None, as_of=None)
    base = PortfolioRow(id="p0", name="cbhu", irp_id="1", **common)
    quick = PortfolioRow(id="p1", name="cbhu - Homeowners", irp_id="2",
                         source_portfolio_id="p0", source_name="cbhu",
                         breakout_dimension_code="lob",
                         breakout_dimension_label="Line of business",
                         breakout_value="Homeowners", **common)
    quick_peril = PortfolioRow(id="p3", name="cbhu - WS", irp_id="4",
                               source_portfolio_id="p0", source_name="cbhu",
                               breakout_dimension_code="peril",
                               breakout_dimension_label="Peril",
                               breakout_value="2", **common)
    custom = PortfolioRow(id="p2", name="Coastal HU", irp_id="3",
                          source_portfolio_id="p0", source_name="cbhu",
                          breakout_dimension_code="custom",
                          breakout_value="a1b2c3",
                          breakout_group_label="Coastal HU",
                          breakout_group_filters={"state": ["FL", "GA"],
                                                  "lob": ["Homeowners"],
                                                  "peril": ["2"]},
                          **common)
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="populated", portfolio_count=4,
                            portfolios=[base, quick, quick_peril, custom],
                            as_of="2026-08-11 10:00:00"))
    html = _client().get("/edms/edm-1/portfolios-section").text
    assert html.count("dt-frommark") == 3      # the three generated rows only
    assert "Line of business IN (Homeowners)" in html
    # peril reads as its mnemonic, not the stored loccvg.PERIL code (D4) —
    # on the quick row and inside the custom filter set alike
    assert "Peril IN (WS)" in html
    assert "lob IN (Homeowners) AND peril IN (WS) AND state IN (FL, GA)" in html
    assert html.count("Base portfolio") == 3


def test_treaties_header_holds_export_and_rm_link(monkeypatch):
    # Treaties polish (2026-07-24): the Export button sits IN the header row
    # (no sec__action block below it any more) and the old read-only note is a
    # real deep link into Risk Modeler's treaties screen, opening a new tab.
    from app.services.treaty_service import TreatyRow
    t = TreatyRow(id="t1", edm_id="edm-1", name="Cat XoL", irp_id="1042",
                  attributes={"treatyType": "CATA"}, as_of="2026-07-24")
    rm_url = "https://rm.example.com/riskmodeler/datasources/legacy_edm/treaties"
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="populated",
                            as_of="2026-07-24 10:00:00", treaties=[t],
                            rm_treaties_url=rm_url))
    html = _client().get("/edms/edm-1").text
    assert 'href="/edms/edm-1/treaties.xlsx"' in html
    assert "sec__action" not in html          # in-line with the header now
    assert f'href="{rm_url}"' in html
    assert 'target="_blank"' in html


def test_treaties_header_without_treaties_or_rm_url(monkeypatch):
    # No treaties → no Export offer; no configured base URL → the plain
    # read-only note falls back in place of the link.
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            detail_state="empty", as_of="2026-07-24 10:00:00"))
    html = _client().get("/edms/edm-1").text
    assert "treaties.xlsx" not in html
    assert "edit in Risk Modeler" in html


def test_treaties_rm_link_hidden_until_import_finishes(monkeypatch):
    # The RM datasource URL is name-based and 404s until the import job
    # finishes, so the deep link only appears once the EDM is ready — the
    # plain read-only note holds its place while the import is in flight.
    rm_url = "https://rm.example.com/riskmodeler/datasources/legacy_edm/treaties"
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(
                            status="importing", detail_state="importing",
                            rm_treaties_url=rm_url))
    html = _client().get("/edms/edm-1").text
    assert rm_url not in html
    assert "edit in Risk Modeler" in html


def test_sync_button_rendered_by_state(monkeypatch):
    # ready + unavailable → the Sync form is offered (header + state box),
    # wired as an HTMX swap of the #edm-detail wrapper
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj())
    html = _client().get("/edms/edm-1").text
    assert 'hx-post="/edms/edm-1/sync"' in html
    assert 'hx-target="#edm-detail"' in html
    assert "Sync now" in html  # the unavailable state box offers the action
    # importing → no Sync form (the import is still in flight)
    monkeypatch.setattr(edm_service, "get_edm_detail",
                        lambda edm_id: _detail_obj(status="pending_import",
                                                   detail_state="importing"))
    html = _client().get("/edms/edm-1").text
    assert "/edms/edm-1/sync" not in html
