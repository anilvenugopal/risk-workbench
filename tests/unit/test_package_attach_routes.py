"""Route tests for attaching/detaching package members (issue #22).

Owns only the HTTP surface — the service behavior lives in ``test_package_attach.py``.
What matters here is the picker *mechanism*, because it is the part that can silently
lose an analyst's work:

  • the selected tray round-trips through the query string, so a pick survives a search
    that hides its row, a page turn, and a cleared search;
  • a chip emits its hidden input ONLY when its row is off-page, which is what makes
    un-ticking an on-page box actually drop it (a hidden input would resubmit the id and
    the tick would spring back) and what stops an id being submitted twice;
  • a partial attach is a 200 with a banner naming the skips, never a 422 — htmx drops
    non-2xx bodies, so a 422 would leave the modal open over a stale list;
  • the read-only gate returns 409 for both attach and detach.

Harness: TestClient + monkeypatched services (``test_name_check_routes.py`` pattern).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services import package_service
from app.services import package_sync_service as psync
from app.services.errors import MemberNotAttachable

MC = psync.MemberCandidate


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


def _candidate(n, kind="edm"):
    return MC(id=f"id{n}", kind=kind, name=f"NAME{n}", status="ready",
              source_file_path=f"\\\\share\\{n}.bak")


def _page(rows, *, total=None, page=1, page_size=20):
    total = len(rows) if total is None else total
    pages = max(1, -(-total // page_size))
    start = (page - 1) * page_size
    return psync.CandidatePage(
        rows=rows, total=total, page=page, pages=pages, page_size=page_size,
        first=start + 1 if total else 0, last=min(start + page_size, total))


def _client(monkeypatch, *, candidates=None, actionable=True) -> TestClient:
    """Packages router with the DB-touching gates and reads stubbed. ``candidates`` is
    the full candidate universe; the stub slices and filters it the way the real service
    does, so the tests exercise the ROUTER's paging/selection wiring."""
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import packages

    universe = list(candidates if candidates is not None else [])

    def fake_list(*, name=None, page=1, page_size=20):
        rows = [c for c in universe if not name or name.lower() in c.name.lower()]
        start = (page - 1) * page_size
        return _page(rows[start:start + page_size], total=len(rows), page=page,
                     page_size=page_size)

    def fake_resolve(*, edm_ids=(), rdm_ids=()):
        wanted = {("edm", str(i)) for i in edm_ids} | {("rdm", str(i)) for i in rdm_ids}
        return [c for c in universe if (c.kind, c.id) in wanted]

    monkeypatch.setattr(packages, "_submission_active", lambda sid: True)
    monkeypatch.setattr(packages, "_package_actionable", lambda pid: actionable)
    monkeypatch.setattr(package_service, "attach_to_submission", lambda **kw: None)
    monkeypatch.setattr(psync, "list_unattached_members", fake_list)
    monkeypatch.setattr(psync, "resolve_picks", fake_resolve)
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

def test_add_modal_renders_the_picker_with_nothing_picked(monkeypatch):
    r = _client(monkeypatch, candidates=[_candidate(1)]).get("/packages/p1/members/add")
    assert r.status_code == 200
    assert 'id="member-picker"' in r.text
    assert 'id="picker-tray"' in r.text
    assert "Nothing picked yet" in r.text
    assert 'id="picker-q"' in r.text                 # the search is offered
    assert "NAME1" in r.text
    assert "disabled" in r.text                      # submit is off at zero picks


def test_add_modal_read_only_package_is_409(monkeypatch):
    r = _client(monkeypatch, actionable=False).get("/packages/p1/members/add")
    assert r.status_code == 409
    assert "read-only" in r.text
    assert 'id="member-picker"' not in r.text        # no picker behind the banner


def test_add_modal_with_nothing_to_attach_hides_the_search(monkeypatch):
    """"Nothing to attach" and "your search matched nothing" must not share a message,
    and an empty library offers no search box at all (there is nothing to search)."""
    r = _client(monkeypatch, candidates=[]).get("/packages/p1/members/add")
    assert "every imported entity already belongs to a package" in r.text
    assert 'id="picker-q"' not in r.text
    assert 'id="picker-tray"' not in r.text


# ── the tray survives search and paging ───────────────────────────────────────────

def test_tray_keeps_picks_whose_rows_the_search_hides(monkeypatch):
    """The scenario that drove the design: pick two, then search for something neither
    matches. Both stay in the tray, counted, and still submitted — as hidden inputs,
    because their rows are not on this page."""
    universe = [_candidate(1), _candidate(2), _candidate(3, kind="rdm")]
    r = _client(monkeypatch, candidates=universe).get(
        "/packages/p1/members/candidates",
        params={"q": "NAME3", "existing_edm_ids": ["id1", "id2"]})

    assert r.status_code == 200
    assert '<span class="picker-tray__n">2</span>' in r.text
    assert "NAME1" in r.text and "NAME2" in r.text          # chips are labelled
    # off-page picks ride along as hidden inputs, so the next request still knows them
    assert '<input type="hidden" name="existing_edm_ids" value="id1">' in r.text
    assert '<input type="hidden" name="existing_edm_ids" value="id2">' in r.text
    assert 'value="NAME3"' not in r.text                    # only the filtered row shows


def test_on_page_pick_is_carried_by_its_checkbox_not_a_hidden_input(monkeypatch):
    """The load-bearing detail: an on-page pick must NOT also get a hidden input, or
    un-ticking its box would resubmit the id and the tick would spring back. It is
    rendered checked (and tinted) instead, and that is the only carrier."""
    client = _client(monkeypatch, candidates=[_candidate(1)])
    picked = client.get("/packages/p1/members/candidates",
                        params={"existing_edm_ids": ["id1"]})
    unpicked = client.get("/packages/p1/members/candidates")

    # the checkbox is the carrier: checked when picked, and nothing else changes
    assert picked.text.count("checked") == 1
    assert "checked" not in unpicked.text
    assert '<input type="hidden" name="existing_edm_ids" value="id1">' not in picked.text
    assert "member-picker__row--picked" in picked.text        # list agrees with the tray
    assert "member-picker__row--picked" not in unpicked.text
    assert '<span class="picker-tray__n">1</span>' in picked.text


def test_pager_appears_only_past_one_page_and_states_the_filtered_count(monkeypatch):
    universe = [_candidate(n) for n in range(25)]
    client = _client(monkeypatch, candidates=universe)

    one_page = client.get("/packages/p1/members/candidates", params={"q": "NAME1"})
    assert "pager__range" not in one_page.text        # NAME1, NAME10..NAME19 → 11 rows

    paged = client.get("/packages/p1/members/candidates")
    assert "1–20 of 25" in paged.text
    assert "Page 1 / 2" in paged.text

    page2 = client.get("/packages/p1/members/candidates", params={"page": 2})
    assert "21–25 of 25" in page2.text


def test_chip_removal_drops_the_id_whichever_input_carried_it(monkeypatch):
    """The ✕ sends ``drop=kind:id`` and the server excludes it, so removal works for an
    off-page chip and an on-page checked row alike."""
    r = _client(monkeypatch, candidates=[_candidate(1), _candidate(2)]).get(
        "/packages/p1/members/candidates",
        params={"existing_edm_ids": ["id1", "id2"], "drop": "edm:id1"})

    assert '<span class="picker-tray__n">1</span>' in r.text
    assert "NAME2" in r.text


def test_out_of_range_page_does_not_error(monkeypatch):
    """A stale ?page= must not 404 or render blank — the candidate set shrinks under the
    analyst whenever anyone else attaches something."""
    r = _client(monkeypatch, candidates=[_candidate(1)]).get(
        "/packages/p1/members/candidates", params={"page": 0})
    assert r.status_code == 200


def test_new_package_candidates_uses_the_package_less_url(monkeypatch):
    """The create modal has no package id yet; the literal route must not be shadowed by
    /packages/{package_id}/members/candidates."""
    r = _client(monkeypatch, candidates=[_candidate(1)]).get(
        "/packages/members/candidates")
    assert r.status_code == 200
    assert "/packages/members/candidates" in r.text     # self-referencing hx-get URLs
    assert "NAME1" in r.text


# ── attach ────────────────────────────────────────────────────────────────────────

def test_attach_posts_picks_and_returns_the_card_with_a_neutral_notice(monkeypatch):
    seen = {}

    def fake_attach(*, package_id, picks, actor_id):
        seen["picks"] = [(p.kind, p.id) for p in picks]
        return psync.AttachResult(attached=len(picks), skipped=[])
    monkeypatch.setattr(psync, "attach_existing_members", fake_attach)

    r = _client(monkeypatch, candidates=[_candidate(1)]).post(
        "/packages/p1/members",
        data={"existing_edm_ids": ["id1"], "existing_rdm_ids": ["id9"],
              "csrf_token": _csrf()})

    assert r.status_code == 200
    assert seen["picks"] == [("edm", "id1"), ("rdm", "id9")]
    assert 'id="package-card-p1"' in r.text
    assert "Nothing was submitted to Risk Modeler" in r.text
    assert "form-banner--error" not in r.text      # a success must not read as a failure


def test_partial_attach_is_200_with_an_error_banner_naming_the_skips(monkeypatch):
    """Not 422: htmx drops non-2xx bodies, so a 422 would leave the modal open over a
    stale candidate list inviting a re-submit."""
    monkeypatch.setattr(
        psync, "attach_existing_members",
        lambda **kw: psync.AttachResult(attached=1, skipped=["TOWNSEND_MARINE"]))

    r = _client(monkeypatch).post(
        "/packages/p1/members",
        data={"existing_edm_ids": ["id1", "id2"], "csrf_token": _csrf()})

    assert r.status_code == 200
    assert "form-banner--error" in r.text          # the toast scraper's hook
    assert "TOWNSEND_MARINE" in r.text
    assert "Attached 1 member(s)" in r.text


def test_attach_deduplicates_ids_arriving_twice(monkeypatch):
    """The tick request includes the whole picker, so an id could arrive from both a chip
    and a checked row. A duplicate must not report attaching two members."""
    seen = {}

    def fake_attach(*, package_id, picks, actor_id):
        seen["picks"] = [(p.kind, p.id) for p in picks]
        return psync.AttachResult(attached=len(picks), skipped=[])
    monkeypatch.setattr(psync, "attach_existing_members", fake_attach)

    _client(monkeypatch).post(
        "/packages/p1/members",
        data={"existing_edm_ids": ["id1", "id1", " id1 ", ""], "csrf_token": _csrf()})
    assert seen["picks"] == [("edm", "id1")]


def test_attach_with_no_picks_returns_the_card_without_calling_the_service(monkeypatch):
    def _boom(**kw):
        raise AssertionError("attach must not be called with no picks")
    monkeypatch.setattr(psync, "attach_existing_members", _boom)

    r = _client(monkeypatch).post("/packages/p1/members", data={"csrf_token": _csrf()})
    assert r.status_code == 200
    assert 'id="package-card-p1"' in r.text


def test_attach_read_only_package_is_409(monkeypatch):
    r = _client(monkeypatch, actionable=False).post(
        "/packages/p1/members",
        data={"existing_edm_ids": ["id1"], "csrf_token": _csrf()})
    assert r.status_code == 409


def test_attach_without_csrf_redirects(monkeypatch):
    r = _client(monkeypatch).post(
        "/packages/p1/members",
        data={"existing_edm_ids": ["id1"], "csrf_token": "bogus"})
    assert r.status_code == 303


# ── detach ────────────────────────────────────────────────────────────────────────

def test_remove_member_passes_the_kind_and_returns_the_card(monkeypatch):
    seen = {}
    monkeypatch.setattr(package_service, "remove_member",
                        lambda **kw: seen.update(kw))

    r = _client(monkeypatch).post(
        "/packages/p1/members/m9/remove",
        data={"member_kind": "rdm", "csrf_token": _csrf()})

    assert r.status_code == 200
    assert (seen["package_id"], seen["member_id"], seen["member_kind"]) == \
        ("p1", "m9", "rdm")
    assert 'id="package-card-p1"' in r.text


def test_remove_member_unattachable_returns_the_card_with_the_reason(monkeypatch):
    """200 with the banner, not 422 — same htmx reasoning as the partial attach. The
    card is re-read, so an emptied package comes back in its deleted state."""
    def _raise(**kw):
        raise MemberNotAttachable("That RDM is no longer a member of this package.")
    monkeypatch.setattr(package_service, "remove_member", _raise)

    r = _client(monkeypatch).post(
        "/packages/p1/members/m9/remove",
        data={"member_kind": "rdm", "csrf_token": _csrf()})

    assert r.status_code == 200
    assert "form-banner--error" in r.text
    assert "no longer a member" in r.text


def test_remove_member_read_only_package_is_409(monkeypatch):
    r = _client(monkeypatch, actionable=False).post(
        "/packages/p1/members/m9/remove",
        data={"member_kind": "edm", "csrf_token": _csrf()})
    assert r.status_code == 409


def test_remove_member_without_csrf_redirects(monkeypatch):
    r = _client(monkeypatch).post(
        "/packages/p1/members/m9/remove",
        data={"member_kind": "edm", "csrf_token": "bogus"})
    assert r.status_code == 303


# ── the card: where the two controls are, and when they are suppressed ────────────

def _card_with(monkeypatch, members, *, deleted_at=None):
    # The card patch has to come AFTER _client(), which installs its own empty-card stub.
    client = _client(monkeypatch)
    card = psync.PackageCard(
        id="p1", name="Pkg", deleted_at=deleted_at,
        edms=[m for m in members if m.kind == "edm"],
        rdms=[m for m in members if m.kind == "rdm"])
    monkeypatch.setattr(psync, "get_package_card",
                        lambda pid, with_counts=False: card)
    return client.get("/packages/p1/card")


def _member(kind="edm", status="ready", name="M1"):
    return psync.MemberCard(id=f"{kind}-1", kind=kind, name=name, status=status,
                            source_file_path="\\\\share\\m.bak")


def test_card_puts_the_add_action_top_right_not_in_the_actions_row(monkeypatch):
    r = _card_with(monkeypatch, [_member()])
    assert "Add existing EDM/RDM" in r.text
    # top-right means inside the head, before the members — not down in the footer row
    head, _, rest = r.text.partition("package-card__group-title")
    assert "Add existing EDM/RDM" in head
    assert "Add existing EDM/RDM" not in rest


def test_card_member_row_offers_remove_inline(monkeypatch):
    r = _card_with(monkeypatch, [_member()])
    assert "/packages/p1/members/edm-1/remove" in r.text
    assert "member-row__x" in r.text
    # inline == inside the collapsed row's <summary>, not the expanded <dl>
    summary, _, detail = r.text.partition("</summary>")
    assert "member-row__x" in summary
    assert "member-row__x" not in detail


def test_card_confirm_warns_when_removing_the_only_member(monkeypatch):
    """Emptying a package soft-deletes it (R5/FR-027), so the confirm has to say so —
    and must still make clear nothing leaves Risk Modeler."""
    only = _card_with(monkeypatch, [_member()])
    assert "also deletes this package" in only.text
    assert "Nothing is removed from Risk Modeler" in only.text

    pair = _card_with(monkeypatch, [_member(), _member(kind="rdm")])
    assert "also deletes this package" not in pair.text
    assert "returns to the standalone library" in pair.text


def test_card_suppresses_remove_for_an_outgoing_member(monkeypatch):
    """remove_member's UPDATE predicate would refuse a delete_pending/deleted member, so
    rendering a control guaranteed to fail is worse than rendering none."""
    for status in ("delete_pending", "deleted"):
        r = _card_with(monkeypatch, [_member(status=status)])
        assert "member-row__x" not in r.text, status


def test_deleted_package_card_offers_neither_control(monkeypatch):
    r = _card_with(monkeypatch, [_member()], deleted_at="2026-01-01 00:00:00")
    assert "Add existing EDM/RDM" not in r.text
    assert "member-row__x" not in r.text


# ── the create modal's disclosure ─────────────────────────────────────────────────

def test_new_modal_wires_the_disclosure_without_reading_candidates(monkeypatch):
    """The disclosure is wired with a URL, not data: no candidate read on modal render,
    so the 422 error re-renders stay free of a DB dependency they do not otherwise have.
    The picker loads when the analyst opens it."""
    def _boom(**kw):
        raise AssertionError("the create modal must not read candidates")
    monkeypatch.setattr(psync, "list_unattached_members", _boom)

    r = _client(monkeypatch).get("/submissions/s1/packages/new")
    assert r.status_code == 200
    assert "picker-disclosure" in r.text
    assert "/packages/members/candidates" in r.text
    assert 'id="member-picker"' not in r.text        # not loaded yet


def test_create_with_only_picks_makes_an_attach_only_package(monkeypatch):
    """An attach-only package is legal — the picks make it non-empty, and they carry no
    name to collision-check. save_package attaches them in its own transaction."""
    seen = {}

    def fake_save(**kw):
        seen.update(kw)
        return psync.SaveResult(package_id="p1")
    monkeypatch.setattr(psync, "save_package", fake_save)

    r = _client(monkeypatch).post(
        "/submissions/s1/packages",
        data={"name": "Attach only", "existing_edm_ids": ["id1"],
              "existing_rdm_ids": ["id2"], "action": "save", "csrf_token": _csrf()})

    assert r.status_code == 200
    assert seen["members"] == []                            # no shared-drive rows
    assert [(p.kind, p.id) for p in seen["existing"]] == [("edm", "id1"),
                                                          ("rdm", "id2")]


def test_create_with_an_unattachable_pick_is_a_422_modal(monkeypatch):
    """save_package attaches in-transaction, so the whole create rolls back — nothing was
    saved, and re-showing the modal with the reason is honest."""
    def _raise(**kw):
        raise MemberNotAttachable("That EDM cannot be added — reload and try again.")
    monkeypatch.setattr(psync, "save_package", _raise)

    r = _client(monkeypatch).post(
        "/submissions/s1/packages",
        data={"existing_edm_ids": ["id1"], "action": "save", "csrf_token": _csrf()})

    assert r.status_code == 422
    assert 'id="package-modal"' in r.text
    assert "form-banner--error" in r.text
    assert "reload and try again" in r.text        # the reason reaches the banner


def test_create_with_neither_files_nor_picks_is_still_rejected(monkeypatch):
    from app.services.errors import EmptyPackageError

    def _raise(**kw):
        raise EmptyPackageError("A package must have at least one member.")
    monkeypatch.setattr(psync, "save_package", _raise)

    r = _client(monkeypatch).post(
        "/submissions/s1/packages", data={"action": "save", "csrf_token": _csrf()})
    assert r.status_code == 422
    assert "Add at least one EDM or RDM." in r.text
