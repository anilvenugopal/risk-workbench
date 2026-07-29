"""Unit tests for the global EDM/RDM libraries (US7, T057).

Exercises the T058 service extensions on BOTH ``edm_service.list_edms`` and
``rdm_service.list_rdms`` (the two library pages are the same shape over a
different entity type):

* **No scoping** (SC-009 / FR-037): with no filter, every non-deleted entity is
  returned regardless of owning submission or analyst; soft-deleted rows excluded.
* **Filters**: ``name=`` narrows by case-insensitive substring; ``status=`` narrows
  to the exact import status; the two combine with AND; blank / ``None`` filters are
  no-ops (return all).
* **Owning-submission attach** over the M:N ``submission_package`` join: a standalone
  entity (``package_id IS NULL``) carries an empty list; a package attached to one
  submission carries a single ``SubmissionRef``; a package attached to ≥2 submissions
  carries all refs **oldest-first** (``submission.inserted_at``).

Runs on the SQLite unit mirror (``iteration2_db``).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace as NS

import pytest
from fastapi.templating import Jinja2Templates

from app.services import edm_service, package_service, rdm_service
from db import execute_command
from tests.unit.test_name_check_routes import _client

# (module, child table) for the two sibling libraries.
LIBS = [(edm_service, "irp_edm"), (rdm_service, "irp_rdm")]


def _list(mod, **kwargs):
    """Dispatch to the module's library list function."""
    return mod.list_edms(**kwargs) if mod is edm_service else mod.list_rdms(**kwargs)


def _entity(table, *, name, status="ready", package_id=None, deleted=False,
            inserted_at="2026-01-01 00:00:00") -> str:
    eid = str(uuid.uuid4())
    execute_command(
        f"INSERT INTO {table} (id, package_id, source_file_path, name, status, "
        f"deleted_at, inserted_at, updated_at) "
        f"VALUES (:id, :pkg, :src, :name, :status, :del, :now, :now)",
        {"id": eid, "pkg": package_id, "src": r"\\share\intake\x.bak", "name": name,
         "status": status, "del": ("2026-02-02 00:00:00" if deleted else None),
         "now": inserted_at},
        connection="WORKBENCH",
    )
    return eid


def _package(inserted_at="2026-01-01 00:00:00") -> str:
    pid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO package (id, name, inserted_at, updated_at) "
        "VALUES (:id, :name, :now, :now)",
        {"id": pid, "name": "pkg", "now": inserted_at}, connection="WORKBENCH")
    return pid


def _submission(*, name, inserted_at) -> str:
    sid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission (id, name, status_code, inserted_at, updated_at) "
        "VALUES (:id, :name, 'ACTIVE', :now, :now)",
        {"id": sid, "name": name, "now": inserted_at}, connection="WORKBENCH")
    return sid


def _attach(submission_id, package_id) -> None:
    execute_command(
        "INSERT INTO submission_package (submission_id, package_id, inserted_at) "
        "VALUES (:s, :p, :now)",
        {"s": submission_id, "p": package_id, "now": "2026-01-01 00:00:00"},
        connection="WORKBENCH")


def _by_name(mod, name, **kwargs):
    return next((r for r in _list(mod, **kwargs) if r.name == name), None)


# ── No row scoping (SC-009 / FR-037) ─────────────────────────────────────────────

@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_lists_all_non_deleted_no_scoping(iteration2_db, mod, table):
    _entity(table, name="Alpha")
    _entity(table, name="Beta")
    _entity(table, name="Gone", deleted=True)
    names = {r.name for r in _list(mod)}
    assert {"Alpha", "Beta"} <= names   # every entity, any owner (no scoping)
    assert "Gone" not in names          # soft-deleted excluded


# ── Filters: name (substring, case-insensitive), status (exact), AND, blanks ─────

@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_name_filter_case_insensitive_substring(iteration2_db, mod, table):
    _entity(table, name="Meridian Property 2026")
    _entity(table, name="Coastal Re")
    assert {r.name for r in _list(mod, name="meridian")} == {"Meridian Property 2026"}
    assert {r.name for r in _list(mod, name="RE")} == {"Coastal Re"}


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_status_filter_exact(iteration2_db, mod, table):
    _entity(table, name="ReadyOne", status="ready")
    _entity(table, name="ErrOne", status="error")
    assert {r.name for r in _list(mod, status="error")} == {"ErrOne"}
    assert {r.name for r in _list(mod, status="ready")} == {"ReadyOne"}


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_filters_combine_with_and(iteration2_db, mod, table):
    _entity(table, name="Meridian ready", status="ready")
    _entity(table, name="Meridian error", status="error")
    _entity(table, name="Other ready", status="ready")
    assert {r.name for r in _list(mod, name="meridian", status="ready")} \
        == {"Meridian ready"}


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_blank_filters_are_noops(iteration2_db, mod, table):
    _entity(table, name="A")
    _entity(table, name="B")
    assert len(_list(mod, name=None, status=None)) == 2
    assert len(_list(mod, name="", status="")) == 2


# ── Owning-submission attach over submission_package (M:N) ───────────────────────

@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_standalone_entity_has_no_submissions(iteration2_db, mod, table):
    _entity(table, name="Solo", package_id=None)
    row = _by_name(mod, "Solo")
    assert row is not None
    assert row.submissions == []


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_single_submission_attach(iteration2_db, mod, table):
    pid = _package()
    _entity(table, name="One", package_id=pid)
    sid = _submission(name="Deal One", inserted_at="2026-03-01 00:00:00")
    _attach(sid, pid)
    row = _by_name(mod, "One")
    assert [(s.id, s.name) for s in row.submissions] == [(sid, "Deal One")]


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_multi_submission_attach_oldest_first(iteration2_db, mod, table):
    pid = _package()
    _entity(table, name="Shared", package_id=pid)
    newer = _submission(name="Newer", inserted_at="2026-05-01 00:00:00")
    older = _submission(name="Older", inserted_at="2026-02-01 00:00:00")
    _attach(newer, pid)
    _attach(older, pid)
    row = _by_name(mod, "Shared")
    # ordered oldest-first by submission.inserted_at, independent of attach order
    assert [s.name for s in row.submissions] == ["Older", "Newer"]


def test_submission_refs_for_packages_empty_input(iteration2_db):
    assert package_service.submission_refs_for_packages([]) == {}


# ── Live list: self-terminating poll trigger ─────────────────────────────────────

def _render_table(*, statuses, filters=None):
    """Render library_table.html in isolation and report (polls?, html). Guards the
    self-terminating condition — the list must poll while any row is still moving
    under a worker and stop once every row is terminal."""
    # The app's own env (autoescape on) — that is what turns the poll URL's query
    # separator into `&amp;` (valid HTML, htmx reads it back as `&`).
    env = Jinja2Templates(directory="app/templates").env
    filter_values = {"q": "", "status": "", **(filters or {})}
    live = any(s in edm_service.TRANSIENT_STATUSES for s in statuses)
    html = env.get_template("partials/library_table.html").render(
        rows=[NS(id=f"e{i}", name=f"E{i}", status=s, source_file_path="/x/E.bak",
                 irp_id=None, inserted_at="2026-01-01", submissions=[])
              for i, s in enumerate(statuses)],
        filter_values=filter_values, live=live,
        list_route="/edms", detail_prefix="/edms", entity_label="EDM")
    return 'hx-trigger="every 3s"' in html, html


def test_list_polls_while_a_row_is_in_flight():
    assert _render_table(statuses=["pending_import"])[0] is True
    assert _render_table(statuses=["importing"])[0] is True
    assert _render_table(statuses=["delete_pending"])[0] is True
    assert _render_table(statuses=["ready", "importing"])[0] is True  # one is enough


def test_list_stops_polling_when_every_row_is_terminal():
    assert _render_table(statuses=["ready", "error", "deleted"])[0] is False
    assert _render_table(statuses=[])[0] is False  # empty list never polls


def test_poll_url_carries_the_active_filters():
    _, html = _render_table(statuses=["importing"],
                            filters={"q": "meridian re", "status": "importing"})
    assert "/edms/table?q=meridian+re&amp;status=importing" in html


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_transient_statuses_exclude_terminal_ones(mod, table):
    assert mod.READY not in mod.TRANSIENT_STATUSES
    assert mod.ERROR not in mod.TRANSIENT_STATUSES
    assert set(mod.TRANSIENT_STATUSES) <= set(mod.STATUSES)


@pytest.mark.parametrize("mod, list_fn, prefix", [
    (edm_service, "list_edms", "/edms"),
    (rdm_service, "list_rdms", "/rdms"),
], ids=["edm", "rdm"])
def test_table_route_renders_the_swap_unit_alone(mod, list_fn, prefix, monkeypatch):
    """The poll/filter target is a fragment, and the literal path must stay declared
    ahead of ``/{id}`` — if that order ever flips this 404s instead."""
    monkeypatch.setattr(mod, list_fn, lambda **kwargs: [])
    r = _client().get(f"{prefix}/table")
    assert r.status_code == 200
    assert 'id="lib-live"' in r.text
    assert "<html" not in r.text  # no page shell
