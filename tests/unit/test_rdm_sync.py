"""Unit tests for the manual RDM sync (analysis-details backfill re-run).

The automatic ``backfill_rdm_analyses`` runs only when an ``import_rdm`` apply
reaches FINISHED, so RDMs imported before spec-004 shipped hold name-only
``irp_analysis`` rows forever (no settings, no portfolio pointer). The Sync
action closes that gap:

  • ``rdm_service.sync_detail`` keys the head ``(analyst_request, rdm_id)`` via
    ``ensure_pending_rwb_job`` + ``dispatch``; the worker input carries NO
    ``edm_id``, which tells the body to derive every applied (RDM, EDM) pair
    from the ``import_rdm`` irp_job rows and re-capture each.
  • The EDM page's Sync refreshes BOTH: ``POST /edms/{id}/sync`` chains
    ``rdm_service.sync_analyses_for_edm`` (one per-RDM head per paired RDM) and
    ``EdmDetail.sync_running`` reflects in-flight analysis backfills so the live
    body keeps polling until the analyses land too.
  • Routes mirror the EDM Sync UX: ``POST /rdms/{rdm_id}/sync`` (CSRF; HTMX swap
    of the live ``#rdm-detail`` body, PRG fallback) + ``GET /rdms/{rdm_id}/body``
    (the self-terminating poll target, package-card pattern).
"""

from __future__ import annotations

import json
import uuid

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.poller import run as poller
from app.services import analysis_service, edm_service, rdm_service
from app.workers import dispatch, package_jobs
from db import execute, execute_command, execute_scalar


def _edm(drive, actor, name, fname):
    return edm_service.import_edm(name=name, source_file_path=str(drive / fname),
                                  actor_id=actor).entity_id


def _finish_all(fake, job_type):
    for row in execute("SELECT irp_id FROM irp_job WHERE irp_job_type=:t",
                       {"t": job_type}, connection="WORKBENCH"):
        fake.finish(str(row["irp_id"]))


def _analyst_heads(rdm_id: str) -> list[dict]:
    return execute(
        "SELECT id, status_code, attempt_count, updated_by FROM rwb_job "
        "WHERE requestor_type='analyst_request' AND requestor_id=:r "
        "AND rwb_job_type='backfill_rdm_analyses'",
        {"r": rdm_id}, connection="WORKBENCH")


def _rdm_ready(iteration2_db, fake_irp, drive, *, name="R", src="rdm1.mdf",
               edm_ids=()) -> str:
    """Import an RDM applied to ``edm_ids`` and drive it to ``ready`` (submit →
    FINISHED → poll → drain the pair backfills)."""
    res = rdm_service.import_rdm(
        name=name, source_file_path=str(drive / src),
        applied_edm_ids=list(edm_ids), actor_id=iteration2_db.user_a)
    package_jobs.run_pending()
    _finish_all(fake_irp, "import_rdm")
    poller.poll_once()
    package_jobs.run_pending()
    assert rdm_service.get_rdm(res.entity_id).status == rdm_service.READY
    return res.entity_id


# ── sync_detail: enqueue, dispatch, revive, guards ────────────────────────────────

def test_sync_enqueues_analyst_head_and_dispatches(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    rdm_id = _rdm_ready(iteration2_db, fake_irp, drive, edm_ids=[e1])

    sent: list[tuple[str, str]] = []
    dispatch.configure(lambda *, rwb_job_id, rwb_job_type: sent.append(
        (rwb_job_id, rwb_job_type)))
    try:
        job_id = rdm_service.sync_detail(rdm_id=rdm_id, actor_id=a)
    finally:
        dispatch.reset()

    assert job_id is not None
    heads = _analyst_heads(rdm_id)
    assert len(heads) == 1
    assert heads[0]["status_code"] == "pending"
    assert sent == [(str(job_id), "backfill_rdm_analyses")]
    # the analyst head carries NO edm_id — the worker derives every applied pair
    row = execute("SELECT input_data FROM rwb_job WHERE id=:i",
                  {"i": str(job_id)}, connection="WORKBENCH")[0]
    assert "edm_id" not in json.loads(row["input_data"])


def test_sync_revives_terminal_head_with_attempt_and_actor(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    rdm_id = _rdm_ready(iteration2_db, fake_irp, drive, edm_ids=[e1])
    first = rdm_service.sync_detail(rdm_id=rdm_id, actor_id=a)
    package_jobs.run_pending()  # analyst head → succeeded

    again = rdm_service.sync_detail(rdm_id=rdm_id, actor_id=iteration2_db.user_b)
    assert again == first  # the SAME row revived, not a duplicate
    heads = _analyst_heads(rdm_id)
    assert len(heads) == 1
    assert heads[0]["status_code"] == "pending"
    assert heads[0]["attempt_count"] == 1
    assert str(heads[0]["updated_by"]) == str(iteration2_db.user_b)


def test_sync_skips_while_backfill_in_flight_either_key(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[e1], actor_id=a)
    package_jobs.run_pending()
    _finish_all(fake_irp, "import_rdm")
    poller.poll_once()  # the poller pair head is PENDING (undrained)
    # Sync must not stack a second concurrent run on the poller-keyed head.
    assert rdm_service.sync_detail(rdm_id=res.entity_id, actor_id=a) is None
    assert _analyst_heads(res.entity_id) == []

    package_jobs.run_pending()
    assert rdm_service.sync_detail(rdm_id=res.entity_id, actor_id=a) is not None
    # ... and an in-flight analyst head blocks a re-click the same way.
    assert rdm_service.sync_detail(rdm_id=res.entity_id, actor_id=a) is None
    assert len(_analyst_heads(res.entity_id)) == 1


def test_sync_noop_when_rdm_importing_or_missing(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[e1], actor_id=a)  # pending_import
    assert rdm_service.sync_detail(rdm_id=res.entity_id, actor_id=a) is None
    assert _analyst_heads(res.entity_id) == []
    assert rdm_service.sync_detail(rdm_id=str(uuid.uuid4()), actor_id=a) is None


# ── worker: the analyst head re-captures EVERY applied pair ───────────────────────

def test_sync_recaptures_settings_across_all_pairs(iteration2_db, fake_irp, drive):
    """The user-facing gap this exists for: rows captured before spec-004 hold
    NULL settings_metadata / exposure_resource_id. A manual sync (no edm_id in
    the input) derives both applied pairs and overwrites the detail in place."""
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    e2 = _edm(drive, a, "E2", "edm2.bak")
    fake_irp.add_analysis(source_rdm_name="R", exposure_name="E1",
                          analysis_id="900", name="AEP",
                          metadata={"engineType": "DLM"},
                          exposure_resource_id="501",
                          exposure_resource_type="PORTFOLIO")
    fake_irp.add_analysis(source_rdm_name="R", exposure_name="E2",
                          analysis_id="901", name="NT",
                          metadata={"engineType": "HD"})
    rdm_id = _rdm_ready(iteration2_db, fake_irp, drive, edm_ids=[e1, e2])

    # Simulate the pre-capability vintage: what spec-003 captured (names only).
    execute_command(
        "UPDATE irp_analysis SET settings_metadata=NULL, exposure_resource_id=NULL",
        {}, connection="WORKBENCH")
    execute_command("UPDATE irp_rdm SET as_of=NULL WHERE id=:r",
                    {"r": rdm_id}, connection="WORKBENCH")

    assert rdm_service.sync_detail(rdm_id=rdm_id, actor_id=a) is not None
    package_jobs.run_pending()

    rows = {str(r["irp_id"]): r for r in execute(
        "SELECT irp_id, edm_id, settings_metadata, exposure_resource_id "
        "FROM irp_analysis WHERE rdm_id=:r", {"r": rdm_id},
        connection="WORKBENCH")}
    assert set(rows) == {"900", "901"}  # BOTH pairs re-captured, no dupes
    assert json.loads(rows["900"]["settings_metadata"])["engineType"] == "DLM"
    assert rows["900"]["exposure_resource_id"] == "501"   # pointer re-promoted
    assert json.loads(rows["901"]["settings_metadata"])["engineType"] == "HD"
    assert rdm_service.get_rdm(rdm_id).as_of is not None  # trust stamp refreshed
    assert _analyst_heads(rdm_id)[0]["status_code"] == "succeeded"


def test_sync_captures_analyses_added_since_import(iteration2_db, fake_irp, drive):
    # An analysis that appeared in RM after the import (or was missed) is
    # captured by the sync — the insert-if-absent path, not just the overwrite.
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    rdm_id = _rdm_ready(iteration2_db, fake_irp, drive, edm_ids=[e1])
    assert execute_scalar("SELECT COUNT(*) FROM irp_analysis WHERE rdm_id=:r",
                          {"r": rdm_id}, connection="WORKBENCH") == 0
    fake_irp.add_analysis(source_rdm_name="R", exposure_name="E1",
                          analysis_id="910", name="Late",
                          metadata={"engineType": "DLM"})
    rdm_service.sync_detail(rdm_id=rdm_id, actor_id=a)
    package_jobs.run_pending()
    rows = execute("SELECT irp_id, settings_metadata FROM irp_analysis "
                   "WHERE rdm_id=:r", {"r": rdm_id}, connection="WORKBENCH")
    assert [str(r["irp_id"]) for r in rows] == ["910"]
    assert rows[0]["settings_metadata"] is not None


# ── the EDM page syncs both: per-RDM fan-out + sync_running visibility ────────────

def test_sync_for_edm_enqueues_one_head_per_paired_rdm(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    r1 = _rdm_ready(iteration2_db, fake_irp, drive, name="R1", src="rdm1.mdf",
                    edm_ids=[e1])
    r2 = _rdm_ready(iteration2_db, fake_irp, drive, name="R2", src="rdm2.mdf",
                    edm_ids=[e1])

    jobs = rdm_service.sync_analyses_for_edm(edm_id=e1, actor_id=a)
    assert len(jobs) == 2
    heads = execute(
        "SELECT requestor_id, status_code FROM rwb_job "
        "WHERE requestor_type='analyst_request' "
        "AND rwb_job_type='backfill_rdm_analyses'", {}, connection="WORKBENCH")
    assert {str(h["requestor_id"]) for h in heads} == {r1, r2}
    assert all(h["status_code"] == "pending" for h in heads)
    # per-RDM in-flight guard: a re-click stacks nothing on top
    assert rdm_service.sync_analyses_for_edm(edm_id=e1, actor_id=a) == []


def test_edm_detail_sync_running_covers_analysis_backfill(
        iteration2_db, fake_irp, drive):
    # The EDM page's live body must keep polling while the analyses backfill is
    # in flight — under EITHER key — even when the EDM's own detail head is idle.
    a = iteration2_db.user_a
    e1 = _edm(drive, a, "E1", "edm1.bak")
    res = rdm_service.import_rdm(name="R", source_file_path=str(drive / "rdm1.mdf"),
                                 applied_edm_ids=[e1], actor_id=a)
    package_jobs.run_pending()
    _finish_all(fake_irp, "import_rdm")
    poller.poll_once()  # poller-keyed pair head now PENDING
    assert edm_service.get_edm_detail(e1).sync_running is True
    package_jobs.run_pending()
    assert edm_service.get_edm_detail(e1).sync_running is False

    assert rdm_service.sync_detail(rdm_id=res.entity_id, actor_id=a) is not None
    assert edm_service.get_edm_detail(e1).sync_running is True  # analyst-keyed
    package_jobs.run_pending()
    assert edm_service.get_edm_detail(e1).sync_running is False


# ── routes: POST /rdms/{rdm_id}/sync + GET /rdms/{rdm_id}/body (live UX) ──────────
#
# Route contract tested with the services monkeypatched (the fixture SQLite engine
# is thread-local and TestClient dispatches on a worker thread; service behavior is
# covered above). Mirrors the EDM Sync route tests one-for-one.

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
    from app.routers import rdms

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(rdms.router)
    return TestClient(app, follow_redirects=False)


def _rdm_obj(**over) -> rdm_service.RdmRow:
    base = dict(
        id="rdm-1", name="legacy_rdm", status="ready",
        source_file_path="/share/legacy.mdf", irp_id=88001, package_id=None,
        inserted_at="2026-01-01", updated_at="2026-01-01")
    base.update(over)
    return rdm_service.RdmRow(**base)


def _stub_reads(monkeypatch, *, rdm=..., sync_status=None, analyses=None):
    if rdm is ...:
        rdm = _rdm_obj()
    monkeypatch.setattr(rdm_service, "get_rdm", lambda rdm_id: rdm)
    monkeypatch.setattr(rdm_service, "latest_backfill_status",
                        lambda rdm_id: sync_status)
    monkeypatch.setattr(analysis_service, "list_broker_analyses",
                        lambda *, rdm_id: analyses or [])


def test_sync_route_bad_csrf_redirects_without_service_call(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(rdm_service, "sync_detail",
                        lambda **kw: calls.append(kw))
    r = _client().post("/rdms/rdm-1/sync", data={"csrf_token": "garbage"})
    assert r.status_code == 303
    assert r.headers["location"] == "/rdms/rdm-1"
    assert calls == []


def test_sync_route_nonhtmx_post_redirects_prg(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(rdm_service, "sync_detail",
                        lambda **kw: calls.append(kw) or "job-1")
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/rdms/rdm-1/sync",
                       data={"csrf_token": generate_csrf_token()})
    assert r.status_code == 303
    assert r.headers["location"] == "/rdms/rdm-1"
    assert calls == [{"rdm_id": "rdm-1", "actor_id": "analyst-1"}]


def test_sync_route_htmx_returns_live_body_partial(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(rdm_service, "sync_detail",
                        lambda **kw: calls.append(kw) or "job-1")
    _stub_reads(monkeypatch, sync_status="pending")
    from app.auth.csrf import generate_csrf_token
    r = _client().post("/rdms/rdm-1/sync",
                       data={"csrf_token": generate_csrf_token()},
                       headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert calls == [{"rdm_id": "rdm-1", "actor_id": "analyst-1"}]
    assert 'id="rdm-detail"' in r.text
    assert 'hx-get="/rdms/rdm-1/body"' in r.text and "every 3s" in r.text
    assert "Syncing" in r.text and "disabled" in r.text
    assert "</html>" not in r.text  # a partial — no shell around it


def test_sync_route_htmx_bad_csrf_forces_full_refresh(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(rdm_service, "sync_detail",
                        lambda **kw: calls.append(kw))
    r = _client().post("/rdms/rdm-1/sync", data={"csrf_token": "garbage"},
                       headers={"HX-Request": "true"})
    assert r.status_code == 204
    assert r.headers["hx-refresh"] == "true"
    assert calls == []


def test_body_poll_partial_polls_while_running_then_stops(monkeypatch):
    _stub_reads(monkeypatch, sync_status="running")
    html = _client().get("/rdms/rdm-1/body").text
    assert 'hx-get="/rdms/rdm-1/body"' in html and "every 3s" in html

    _stub_reads(monkeypatch, rdm=_rdm_obj(as_of="2026-07-24 10:00:00"),
                sync_status="succeeded")
    html = _client().get("/rdms/rdm-1/body").text
    assert "every 3s" not in html
    assert "synced" in html
    assert '<time data-utc="2026-07-24 10:00:00"' in html
    assert ">Sync</button>" in html  # the button is offered again, enabled


def test_body_poll_populated_mid_sync_returns_204_no_swap(monkeypatch):
    # Re-syncing an already-populated page: a 3s outerHTML swap would collapse
    # every open <details> (analysis drills), so the poll target answers 204
    # until the sync lands — then the fresh body renders exactly once.
    grp = analysis_service.BrokerAnalysisGroup(
        rdm_id="rdm-1", rdm_name="R", rdm_irp_id=88,
        analyses=[analysis_service.BrokerAnalysis(
            id="a1", irp_id="5521", name="AEP", rdm_id="rdm-1", rdm_name="R",
            edm_name="E1")])
    _stub_reads(monkeypatch, sync_status="running", analyses=[grp])
    r = _client().get("/rdms/rdm-1/body")
    assert r.status_code == 204


def test_body_poll_partial_live_while_importing(monkeypatch):
    _stub_reads(monkeypatch, rdm=_rdm_obj(status="pending_import"))
    html = _client().get("/rdms/rdm-1/body").text
    assert "every 3s" in html


def test_body_poll_partial_when_rdm_gone(monkeypatch):
    monkeypatch.setattr(rdm_service, "get_rdm", lambda rdm_id: None)
    r = _client().get("/rdms/rdm-1/body")
    assert r.status_code == 200
    assert "no longer exists" in r.text
    assert "every 3s" not in r.text


def test_sync_button_rendered_by_state(monkeypatch):
    # ready + never-captured → the Sync form is offered (header + state box)
    _stub_reads(monkeypatch)
    html = _client().get("/rdms/rdm-1").text
    assert 'hx-post="/rdms/rdm-1/sync"' in html
    assert 'hx-target="#rdm-detail"' in html
    assert "Sync now" in html  # the settings-unavailable box offers the action
    # importing → no Sync form (the import is still in flight)
    _stub_reads(monkeypatch, rdm=_rdm_obj(status="pending_import"))
    html = _client().get("/rdms/rdm-1").text
    assert "/rdms/rdm-1/sync" not in html


def test_sync_failed_state_shows_warn_and_recovery(monkeypatch):
    _stub_reads(monkeypatch, sync_status="failed")
    html = _client().get("/rdms/rdm-1/body").text
    assert "last sync failed" in html
    assert "Sync now" in html
    assert "every 3s" not in html  # terminal — no poll
