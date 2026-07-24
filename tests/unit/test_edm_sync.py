"""Unit tests for the manual per-EDM Sync action (spec 004 Addendum A, T056).

FR-003 as amended 2026-07-23: automatic backfill stays forward-only, but the EDM
detail page's Sync button re-runs ``backfill_edm_detail`` on demand.
``edm_service.sync_detail`` keys the head ``(analyst_request, edm_id)`` via
``ensure_pending_rwb_job`` (revives a terminal row) + ``dispatch``;
``_latest_backfill_status`` reads BOTH that key and the poller's
``irp_job``-keyed rows (newest ``updated_at`` wins) so ``detail_state`` and
``EdmDetail.sync_running`` stay truthful whichever path ran last. The worker
name-resolves a missing exposureId (pre-capability EDMs) exactly like the
poller. Routes: ``POST /edms/{edm_id}/sync`` (CSRF; HTMX swap of the live
``#edm-detail`` body, PRG fallback) + ``GET /edms/{edm_id}/body`` (the
self-terminating poll target, package-card pattern).
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.poller import run as poller
from app.services import edm_service
from app.workers import dispatch, package_jobs
from db import execute, execute_command, execute_one
from tests.unit.test_backfill_edm_detail import EXPOSURE_A


def _edm_ready(drive, fake, actor, name="EDM") -> str:
    """Import a standalone EDM and drive it to ``ready`` (submit → FINISHED →
    poll). Leaves the poller-enqueued backfill head PENDING (undrained)."""
    res = edm_service.import_edm(name=name, source_file_path=str(drive / "edm1.bak"),
                                 actor_id=actor)
    package_jobs.run_pending(worker_id="w1")
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
    package_jobs.run_pending(worker_id="w1")  # drain the poller head → succeeded

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
    package_jobs.run_pending(worker_id="w1")
    first = edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")  # analyst head → succeeded

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

    package_jobs.run_pending(worker_id="w1")
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

    package_jobs.run_pending(worker_id="w1")
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
    package_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "failed"
    assert detail.status == edm_service.READY  # FR-005 — never reverted

    fake_irp.raise_on_list_portfolios = False
    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")
    assert edm_service.get_edm_detail(edm_id).detail_state == "populated"


def test_detail_state_prefers_most_recently_updated_head(
        iteration2_db, fake_irp, drive):
    # Poller head succeeded (zero-portfolio EDM → "empty"), then a newer manual
    # sync goes pending — the page must say "pending", not stale "empty".
    edm_id = _edm_ready(drive, fake_irp, iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")
    assert edm_service.get_edm_detail(edm_id).detail_state == "empty"

    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "pending"
    assert detail.sync_running is True


# ── worker: pre-capability EDMs without an exposureId (name resolution) ───────────

def test_sync_resolves_missing_exposure_id_by_name(iteration2_db, fake_irp, drive):
    fake_irp.add_edm_name("legacy_named")
    xid = fake_irp.search_edms("legacy_named")[0].irp_id
    fake_irp.add_portfolio(edm_exposure_id=xid, irp_id="501",
                           name="Primary 2026", exposure=EXPOSURE_A)
    edm_id = _legacy_edm(name="legacy_named", irp_id=None)

    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "populated"
    assert detail.irp_id == int(xid)  # exposureId resolved by name AND persisted
    assert len(_analyst_heads(edm_id)) == 1
    assert _analyst_heads(edm_id)[0]["status_code"] == "succeeded"


def test_sync_skips_gracefully_when_name_unresolvable(
        iteration2_db, fake_irp, drive):
    edm_id = _legacy_edm(name="unknown_edm", irp_id=None)  # not in RM at all
    edm_service.sync_detail(edm_id=edm_id, actor_id=iteration2_db.user_a)
    package_jobs.run_pending(worker_id="w1")

    detail = edm_service.get_edm_detail(edm_id)
    assert detail.detail_state == "unavailable"  # succeeded-as-skip, no as_of
    assert detail.irp_id is None
    assert _analyst_heads(edm_id)[0]["status_code"] == "succeeded"


# ── route: POST /edms/{edm_id}/sync + GET /edms/{edm_id}/body (live UX) ───────────
#
# The route contract is tested with edm_service monkeypatched: the fixture SQLite
# engine is thread-local and TestClient dispatches handlers on a worker thread, and
# the service behavior is already covered above — the route tests only own the HTTP
# surface. The Sync UX mirrors the package-card live pattern: an HTMX POST swaps the
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

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(edms.router)
    return TestClient(app, follow_redirects=False)


def _detail_obj(**over) -> edm_service.EdmDetail:
    base = dict(
        id="edm-1", name="legacy_edm", status="ready", as_of=None,
        source_file_path="/share/legacy.bak", irp_id=77001,
        created_by_irp_job_irp_id=None, package_id=None,
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


def test_body_poll_partial_live_while_importing(monkeypatch):
    # The same wrapper keeps the page fresh through an in-flight import too —
    # the import statuses are live exactly like the package card's.
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
