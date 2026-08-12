"""Route tests for attaching/detaching whole packages to/from a submission
(issue #22, FR "add existing package").

Owns only the HTTP surface — the service behavior lives in
``test_package_service.py``. What matters here:

  • both writes respond with the re-rendered ``#package-list``, so the DOM
    matches a page reload (ordering, empty state, detach controls);
  • the detach control is NOT part of ``package_card.html`` — the card re-renders
    itself (poll + card POSTs) from contexts with no submission id, so an in-card
    control would vanish on the first swap. The poll response must never carry it;
  • a partially-skipped attach is a 200 with a banner, never a 422 — htmx drops
    non-2xx bodies;
  • the closed-submission gate returns 409 for modal, attach, and detach.

Harness: TestClient + monkeypatched services (``test_package_attach_routes.py``
pattern).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import package_service
from app.services import package_sync_service as psync
from app.services.package_service import Package, SubmissionRef


class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.services.auth_service import CurrentUser
        request.state.user = CurrentUser(
            id="analyst-1", email="analyst@example.com", display_name="Analyst",
            session_id="s", role_codes=["analyst"], is_admin=False,
            must_change_password=False, entra_oid=None, is_active=True)
        return await call_next(request)


def _csrf() -> str:
    from app.auth.csrf import generate_csrf_token
    return generate_csrf_token()


def _package(n, member_count=1):
    return Package(id=f"p{n}", name=f"PKG{n}", deleted_at=None,
                   member_count=member_count, inserted_at="2026-01-01")


def _client(monkeypatch, *, candidates=None, owners=None, cards=None,
            active=True, calls=None) -> TestClient:
    """Packages router with the DB-touching gates and reads stubbed. ``cards`` is
    what ``get_package_cards`` returns AFTER the attach loop ran — omitting a posted
    id from it is how a test simulates a candidate soft-deleted mid-flight.
    ``calls`` (a dict of lists) records the service writes."""
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import packages

    calls = calls if calls is not None else {"attach": [], "detach": []}

    monkeypatch.setattr(packages, "_submission_active", lambda sid: active)
    monkeypatch.setattr(packages, "_package_actionable", lambda pid: True)
    monkeypatch.setattr(package_service, "get_attachable_packages",
                        lambda sid: list(candidates or []))
    monkeypatch.setattr(package_service, "submission_refs_for_packages",
                        lambda ids: dict(owners or {}))
    monkeypatch.setattr(
        package_service, "attach_to_submission",
        lambda **kw: calls["attach"].append((kw["submission_id"], kw["package_id"])))
    monkeypatch.setattr(
        package_service, "detach_from_submission",
        lambda **kw: calls["detach"].append((kw["submission_id"], kw["package_id"])))
    monkeypatch.setattr(
        psync, "get_package_cards",
        lambda sid: [psync.PackageCard(id=c.id, name=c.name) for c in (cards or [])])
    monkeypatch.setattr(
        psync, "get_package_card",
        lambda pid, with_counts=False: psync.PackageCard(id=str(pid), name="Pkg"))

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(packages.router)
    return TestClient(app, follow_redirects=False)


# ── the modal ─────────────────────────────────────────────────────────────────────

def test_attach_modal_lists_candidates_with_owners(monkeypatch):
    owners = {"p1": [SubmissionRef(id="s2", name="Deal A"),
                     SubmissionRef(id="s3", name="Deal B")]}
    r = _client(monkeypatch, candidates=[_package(1, member_count=2), _package(2)],
                owners=owners).get("/submissions/s1/packages/attach")
    assert r.status_code == 200
    assert r.text.count('name="package_ids"') == 2
    assert "PKG1" in r.text and "PKG2" in r.text
    assert "2 members" in r.text
    assert "shared with Deal A, Deal B" in r.text     # attached elsewhere
    assert "in no deal" in r.text                     # the library package
    assert "disabled" in r.text                       # submit is off at zero picks


def test_attach_modal_with_no_candidates_offers_only_close(monkeypatch):
    r = _client(monkeypatch, candidates=[]).get("/submissions/s1/packages/attach")
    assert r.status_code == 200
    assert "already attached to this deal" in r.text
    assert 'name="package_ids"' not in r.text
    assert 'type="submit"' not in r.text


def test_attach_modal_on_a_closed_submission_is_409(monkeypatch):
    r = _client(monkeypatch, active=False).get("/submissions/s1/packages/attach")
    assert r.status_code == 409
    assert "closed" in r.text


def test_attach_modal_closes_only_on_its_own_submit(monkeypatch):
    """Same guard as the members modal: htmx events bubble, so only the form's own
    request may remove the modal."""
    r = _client(monkeypatch, candidates=[_package(1)]).get(
        "/submissions/s1/packages/attach")
    close = next(ln for ln in r.text.splitlines() if "$root.remove()" in ln
                 and "after-request" in ln)
    assert "$event.target === $el" in close


# ── attach ────────────────────────────────────────────────────────────────────────

def test_attach_dedupes_ids_and_returns_the_list_with_a_notice(monkeypatch):
    calls = {"attach": [], "detach": []}
    client = _client(monkeypatch, cards=[_package(1), _package(2)], calls=calls)
    r = client.post("/submissions/s1/packages/attach",
                    data={"package_ids": ["p1", "p1", "p2"], "csrf_token": _csrf()})
    assert r.status_code == 200
    assert calls["attach"] == [("s1", "p1"), ("s1", "p2")]
    assert 'id="package-list"' in r.text
    assert "Attached 2 package(s)" in r.text
    assert "nothing was submitted to Risk Modeler" in r.text
    assert "form-banner--error" not in r.text


def test_attach_reports_a_skipped_pick_from_the_reread_list(monkeypatch):
    """p2 vanished between render and submit (soft-deleted mid-flight): the INSERT
    predicate skipped it silently, so the router detects it by its absence from the
    re-read list. 200 + banner, never 422."""
    calls = {"attach": [], "detach": []}
    client = _client(monkeypatch, cards=[_package(1)], calls=calls)
    r = client.post("/submissions/s1/packages/attach",
                    data={"package_ids": ["p1", "p2"], "csrf_token": _csrf()})
    assert r.status_code == 200
    assert calls["attach"] == [("s1", "p1"), ("s1", "p2")]
    assert "Attached 1 package(s)" in r.text
    assert "Skipped 1" in r.text
    assert "form-banner--error" in r.text


def test_attach_with_no_ids_returns_the_list_without_writing(monkeypatch):
    calls = {"attach": [], "detach": []}
    client = _client(monkeypatch, cards=[], calls=calls)
    r = client.post("/submissions/s1/packages/attach",
                    data={"csrf_token": _csrf()})
    assert r.status_code == 200
    assert calls["attach"] == []
    assert 'id="package-list"' in r.text


def test_attach_on_a_closed_submission_is_409(monkeypatch):
    calls = {"attach": [], "detach": []}
    client = _client(monkeypatch, active=False, calls=calls)
    r = client.post("/submissions/s1/packages/attach",
                    data={"package_ids": ["p1"], "csrf_token": _csrf()})
    assert r.status_code == 409
    assert calls["attach"] == []


def test_attach_with_a_bad_csrf_token_redirects(monkeypatch):
    r = _client(monkeypatch).post("/submissions/s1/packages/attach",
                                  data={"package_ids": ["p1"], "csrf_token": "nope"})
    assert r.status_code == 303


# ── detach ────────────────────────────────────────────────────────────────────────

def test_detach_passes_the_pair_and_returns_the_list(monkeypatch):
    calls = {"attach": [], "detach": []}
    client = _client(monkeypatch, cards=[], calls=calls)
    r = client.post("/submissions/s1/packages/p1/detach",
                    data={"csrf_token": _csrf()})
    assert r.status_code == 200
    assert calls["detach"] == [("s1", "p1")]
    assert 'id="package-list"' in r.text
    assert "No packages yet." in r.text      # last package detached → empty state


def test_detach_on_a_closed_submission_is_409(monkeypatch):
    calls = {"attach": [], "detach": []}
    client = _client(monkeypatch, active=False, calls=calls)
    r = client.post("/submissions/s1/packages/p1/detach",
                    data={"csrf_token": _csrf()})
    assert r.status_code == 409
    assert calls["detach"] == []


def test_detach_with_a_bad_csrf_token_redirects(monkeypatch):
    r = _client(monkeypatch).post("/submissions/s1/packages/p1/detach",
                                  data={"csrf_token": "nope"})
    assert r.status_code == 303


# ── the list partial ──────────────────────────────────────────────────────────────

def test_list_carries_the_detach_form_but_the_card_poll_does_not(monkeypatch):
    """The poll-survival pin: detach lives in the list's wrapper, not in
    package_card.html, so the card's every-3s self-swap (which has no submission id)
    can never remove it — because it was never inside the swapped element."""
    client = _client(monkeypatch, cards=[_package(1)])
    listing = client.post("/submissions/s1/packages/p1/detach",
                          data={"csrf_token": _csrf()})
    assert 'id="package-item-p1"' in listing.text
    assert "/submissions/s1/packages/p1/detach" in listing.text
    assert "Detach from this deal" in listing.text

    polled = client.get("/packages/p1/card")
    assert 'id="package-card-p1"' in polled.text
    assert "Detach from this deal" not in polled.text
    assert "/detach" not in polled.text


def test_list_omits_the_detach_form_for_a_deleted_package(monkeypatch):
    client = _client(monkeypatch)
    # _client's get_package_cards stub copies id/name only — re-stub with deleted_at.
    monkeypatch.setattr(
        psync, "get_package_cards",
        lambda sid: [psync.PackageCard(id="p9", name="Gone",
                                       deleted_at="2026-01-01")])
    r = client.post("/submissions/s1/packages/p9/detach",
                    data={"csrf_token": _csrf()})
    assert 'id="package-item-p9"' in r.text
    assert "Detach from this deal" not in r.text
