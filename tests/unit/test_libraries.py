"""Unit tests for the global EDM/RDM libraries (US7, T057).

Exercises the T058 service extensions on BOTH ``edm_service.list_edms`` and
``rdm_service.list_rdms`` (the two library pages are the same shape over a
different entity type):

* **No scoping** (SC-009 / FR-037): with no filter, every non-deleted entity is
  returned regardless of owning submission or analyst; soft-deleted rows excluded.
* **Filters**: ``name=`` narrows by case-insensitive substring; ``status=`` narrows
  to the exact import status; the two combine with AND; blank / ``None`` filters are
  no-ops (return all).
* **Owning-submission attach** over the direct association tables: a standalone
  entity carries an empty list; an entity attached to one submission carries one
  ``SubmissionRef``; an entity attached to two submissions carries both refs in
  ``submission.inserted_at`` order.

Runs on the SQLite unit mirror (``iteration2_db``).
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace as NS
from urllib.parse import urlencode

import pytest
from fastapi.templating import Jinja2Templates

from app.services import edm_service, rdm_service
from app.services import submission_service as svc
from app.templating import TEMPLATE_DIRS
from db import execute_command
from tests.unit.test_name_check_routes import _client
from tests.unit.conftest import cedant_id

# (module, child table) for the two sibling libraries.
LIBS = [(edm_service, "irp_edm"), (rdm_service, "irp_rdm")]


def _list(mod, **kwargs):
    """Dispatch to the module's library list function."""
    return mod.list_edms(**kwargs) if mod is edm_service else mod.list_rdms(**kwargs)


def _entity(table, *, name, status="ready", deleted=False,
            inserted_at="2026-01-01 00:00:00") -> str:
    eid = str(uuid.uuid4())
    execute_command(
        f"INSERT INTO {table} (id, source_file_path, name, status, "
        f"deleted_at, inserted_at, updated_at) "
        f"VALUES (:id, :src, :name, :status, :del, :now, :now)",
        {"id": eid, "src": r"\\share\intake\x.bak", "name": name,
         "status": status, "del": ("2026-02-02 00:00:00" if deleted else None),
         "now": inserted_at},
        connection="WORKBENCH",
    )
    return eid



def _submission(*, name, inserted_at) -> str:
    sid = str(uuid.uuid4())
    execute_command(
        "INSERT INTO submission (id, assigned_analyst_id, name, cedant_id, "
        "status_code, inserted_at, updated_at) "
        "SELECT :id, id, :name, :ced, 'ACTIVE', :now, :now FROM app_user LIMIT 1",
        {"id": sid, "name": name, "ced": cedant_id(), "now": inserted_at},
        connection="WORKBENCH")
    return sid


def _attach(submission_id, table, entity_id) -> None:
    association = "submission_edm" if table == "irp_edm" else "submission_rdm"
    column = "edm_id" if table == "irp_edm" else "rdm_id"
    execute_command(
        f"INSERT INTO {association} (submission_id, {column}, inserted_at) "
        f"VALUES (:s, :e, :now)",
        {"s": submission_id, "e": entity_id, "now": "2026-01-01 00:00:00"},
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
def test_match_words_finds_every_word_in_any_order(iteration2_db, mod, table):
    """FR-015 (note 35 D14): the library pages match per word like the
    submissions list; the default stays the global search's substring."""
    _entity(table, name="TY 2607 USFL")
    _entity(table, name="TY 2607 FL")
    words = {r.name for r in _list(mod, name="ty usfl 2607", match_words=True)}
    assert words == {"TY 2607 USFL"}
    assert _list(mod, name="ty usfl 2608", match_words=True) == []
    assert _list(mod, name="ty usfl 2607") == []


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_unattached_lists_only_entities_in_no_submission(iteration2_db, mod, table):
    """FR-015 / FR-016 (note 35 D16): "Not in a submission" is an entity-level
    filter; with a submission-attribute filter beside it nothing matches."""
    _attach(_deal(name="Linked", owner=iteration2_db.user_a), table,
            _entity(table, name="Attached"))
    _entity(table, name="Loose")
    assert [r.name for r in _list(mod, unattached=True)] == ["Loose"]
    assert _list(mod, unattached=True,
                 submission_filters={"owner_ids": [iteration2_db.user_a]}) == []
    assert {r.name for r in _list(mod)} == {"Attached", "Loose"}


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


# ── Submission association reads (M:N) ──────────────────────────────────────

@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_standalone_entity_has_no_submissions(iteration2_db, mod, table):
    _entity(table, name="Solo")
    row = _by_name(mod, "Solo")
    assert row is not None
    assert row.submissions == []


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_single_submission_attach(iteration2_db, mod, table):
    entity_id = _entity(table, name="One")
    sid = _submission(name="Deal One", inserted_at="2026-03-01 00:00:00")
    _attach(sid, table, entity_id)
    row = _by_name(mod, "One")
    assert [(s.id, s.name) for s in row.submissions] == [(sid, "Deal One")]


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_multi_submission_attach_oldest_first(iteration2_db, mod, table):
    entity_id = _entity(table, name="Shared")
    newer = _submission(name="Newer", inserted_at="2026-05-01 00:00:00")
    older = _submission(name="Older", inserted_at="2026-02-01 00:00:00")
    _attach(newer, table, entity_id)
    _attach(older, table, entity_id)
    row = _by_name(mod, "Shared")
    assert [submission.name for submission in row.submissions] == ["Older", "Newer"]




# ── Live list: self-terminating poll trigger ─────────────────────────────────────

@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_detail_carries_the_linked_submissions_oldest_first(iteration2_db, mod, table):
    """FR-015 (note 35 D16): the detail header links every owning submission."""
    eid = _entity(table, name="Detailed")
    _attach(_submission(name="Newer", inserted_at="2026-03-01 00:00:00"), table, eid)
    _attach(_submission(name="Older", inserted_at="2026-02-01 00:00:00"), table, eid)
    detail = (mod.get_edm_detail(eid) if mod is edm_service
              else mod.get_rdm_detail(eid)["rdm"])
    assert [s.name for s in detail.submissions] == ["Older", "Newer"]


def _import_job(table, entity_id, *, status, progress, inserted_at) -> None:
    kind = table.removeprefix("irp_")
    execute_command(
        f"INSERT INTO irp_job (id, irp_{kind}_id, irp_job_type, status, progress, "
        "inserted_at, updated_at) VALUES (:id, :e, :type, :status, :progress, :now, :now)",
        {"id": str(uuid.uuid4()), "e": entity_id, "type": f"import_{kind}",
         "status": status, "progress": progress, "now": inserted_at},
        connection="WORKBENCH")


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_import_label_reaches_every_entity_read(iteration2_db, mod, table):
    running = _entity(table, name="Running", status="importing")
    _import_job(table, running, status="FAILED", progress=80,
                inserted_at="2026-01-01 00:00:00")
    _import_job(table, running, status="RUNNING", progress=45,
                inserted_at="2026-01-02 00:00:00")
    queued = _entity(table, name="Queued", status="importing")
    _import_job(table, queued, status="QUEUED", progress=None,
                inserted_at="2026-01-02 00:00:00")
    finished = _entity(table, name="Finished", status="importing")
    _import_job(table, finished, status="FINISHED", progress=100,
                inserted_at="2026-01-02 00:00:00")
    ready = _entity(table, name="Ready", status="ready")
    _import_job(table, ready, status="FINISHED", progress=100,
                inserted_at="2026-01-02 00:00:00")
    sid = _submission(name="Deal", inserted_at="2026-01-01 00:00:00")
    for entity in (running, queued, finished, ready):
        _attach(sid, table, entity)
    empty_sid = _submission(name="Empty", inserted_at="2026-01-01 00:00:00")

    if mod is edm_service:
        entity_table = svc.list_submission_edms(sid)
        candidates = svc.list_edm_candidates(empty_sid).rows
        detail = mod.get_edm_detail(running)
    else:
        entity_table = svc.list_submission_rdms(sid)
        candidates = svc.list_rdm_candidates(empty_sid).rows
        detail = mod.get_rdm_detail(running)["rdm"]

    expected = {"Running": "Running 45%", "Queued": "Queued",
                "Finished": "Finished", "Ready": None}
    assert {r.name: r.import_label for r in _list(mod)} == expected
    assert {r.name: r.import_label for r in entity_table} == expected
    assert {r.name: r.import_label for r in candidates} == expected
    assert detail.import_label == "Running 45%"


def _render_table(*, statuses, filters=None, import_label=None):
    """Render library_table.html in isolation and report (polls?, html). Guards the
    self-terminating condition — the list must poll while any row is still moving
    under a worker and stop once every row is terminal."""
    # The app's own env (autoescape on) — that is what turns the poll URL's query
    # separator into `&amp;` (valid HTML, htmx reads it back as `&`).
    env = Jinja2Templates(directory=TEMPLATE_DIRS).env
    env.globals["ui_poll_interval_secs"] = 3
    filter_values = {"q": "", "status": "", **(filters or {})}
    live = any(s in edm_service.TRANSIENT_STATUSES for s in statuses)
    html = env.get_template("partials/library_table.html").render(
        rows=[NS(id=f"e{i}", name=f"E{i}", status=s, source_file_path="/x/E.bak",
                 inserted_at="2026-01-01", submissions=[], import_label=import_label)
              for i, s in enumerate(statuses)],
        filter_values=filter_values, live=live, validation_error=None,
        is_filtered=any(filter_values.values()),
        query_string=urlencode(list(filter_values.items()), doseq=True),
        list_route="/edms", detail_prefix="/edms", entity_label="EDM")
    return 'hx-trigger="every 3s"' in html, html


def test_list_polls_while_a_row_is_in_flight():
    assert _render_table(statuses=["pending_import"])[0] is True
    assert _render_table(statuses=["importing"])[0] is True
    assert _render_table(statuses=["ready", "importing"])[0] is True  # one is enough


def test_status_chip_shows_import_label():
    assert ">Running 45%<" in _render_table(statuses=["importing"],
                                            import_label="Running 45%")[1]
    assert ">importing<" in _render_table(statuses=["importing"])[1]


def test_list_stops_polling_when_every_row_is_terminal():
    assert _render_table(statuses=["ready", "error"])[0] is False
    assert _render_table(statuses=[])[0] is False  # empty list never polls


def test_poll_url_carries_the_active_filters():
    """The poll URL is the request's own query string, so every filter param —
    not only name and status — keeps applying while the list polls (spec 017)."""
    _, html = _render_table(statuses=["importing"],
                            filters={"q": "meridian re", "status": "importing",
                                     "crm_id": ["T-1", "T-2"], "contract_status": ["WON"]})
    assert ("/edms/table?q=meridian+re&amp;status=importing&amp;crm_id=T-1"
            "&amp;crm_id=T-2&amp;contract_status=WON" in html)


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_transient_statuses_exclude_terminal_ones(mod, table):
    assert mod.READY not in mod.TRANSIENT_STATUSES
    assert mod.ERROR not in mod.TRANSIENT_STATUSES
    assert set(mod.TRANSIENT_STATUSES) <= set(mod.STATUSES)


@pytest.mark.parametrize("mod, list_fn, prefix", [
    (edm_service, "list_edms", "/edms"),
    (rdm_service, "list_rdms", "/rdms"),
], ids=["edm", "rdm"])
def test_table_route_renders_the_swap_unit_alone(iteration2_db, mod, list_fn, prefix,
                                                 monkeypatch):
    """The poll/filter target is a fragment, and the literal path must stay declared
    ahead of ``/{id}`` — if that order ever flips this 404s instead."""
    monkeypatch.setattr(mod, list_fn, lambda **kwargs: [])
    r = _client().get(f"{prefix}/table")
    assert r.status_code == 200
    assert 'id="lib-live"' in r.text
    assert "<html" not in r.text  # no page shell


@pytest.mark.parametrize("prefix", ["/edms", "/rdms"])
def test_table_route_does_not_load_the_picker_options(iteration2_db, prefix, monkeypatch):
    """The polled fragment renders no filter bar, so it must not read the
    repository's client list on every tick."""
    from app.services import client_service

    def _refuse():
        raise AssertionError("list_clients called by the table fragment")

    monkeypatch.setattr(client_service, "list_clients", _refuse)
    assert _client().get(f"{prefix}/table").status_code == 200



# ── spec 017 US3: submission-attribute filters (FR-015, FR-016) ──────────────

def _deal(*, name, owner, contract_status="OPEN", crm_ids=("C-1",),
          expiration=None):
    """A deal whose contracts (one per CRM ID) all incept 2026-01-01 and carry
    ``contract_status``; ``expiration`` defaults to 2026-12-31."""
    from app.services import submission_service

    return submission_service.create_submission(
        name=name, cedant_id=cedant_id(f"{name} Re"),
        contracts=[submission_service.ContractInput(
            crm_id=crm_id, treaty_type_code="per_risk_xol",
            inception_date=date(2026, 1, 1), expiration_date=expiration,
            contract_status_code=contract_status) for crm_id in crm_ids],
        data_vintage="2026-06-30", actor_id=owner)


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_entity_matches_when_one_linked_submission_satisfies_every_filter(
        iteration2_db, mod, table):
    cheryl, ben = iteration2_db.user_a, iteration2_db.user_b
    shared = _entity(table, name="Shared")
    _attach(_deal(name="Won by Cheryl", owner=cheryl, contract_status="WON",
                  crm_ids=["T-100", "T-200"]), table, shared)
    _attach(_deal(name="Open by Ben", owner=ben, crm_ids=["T-300"]), table, shared)
    solo = _entity(table, name="Solo")

    def names(**filters):
        return [r.name for r in _list(mod, submission_filters=filters)]

    assert names(contract_status_codes=["WON"], owner_ids=[cheryl]) == ["Shared"]
    assert names(contract_status_codes=["WON"], owner_ids=[ben]) == []
    assert names(crm_ids=["T-100", "T-300"]) == ["Shared"]        # listed once
    assert names(crm_ids=["T-10"]) == []                            # whole id only
    assert set(names()) == {"Shared", "Solo"}                       # no filter: all
    assert set(names(owner_ids=[], crm_ids=[])) == {"Shared", "Solo"}
    assert names(owner_ids=[ben]) == ["Shared"] and solo            # Solo drops out


@pytest.mark.parametrize("mod, table", LIBS, ids=["edm", "rdm"])
def test_name_and_status_stay_on_the_entity_row(iteration2_db, mod, table):
    ready = _entity(table, name="Meridian ready", status="ready")
    _entity(table, name="Meridian error", status="error")
    _attach(_deal(name="Won", owner=iteration2_db.user_a, contract_status="WON"),
            table, ready)
    assert [r.name for r in _list(mod, name="meridian", status="ready",
                                  submission_filters={"contract_status_codes": ["WON"]})] == [
        "Meridian ready"]


@pytest.mark.parametrize("prefix", ["/edms", "/rdms"])
def test_library_filters_reach_the_query_and_the_cap_message(iteration2_db, prefix):
    body = _client().get(f"{prefix}?" + "&".join(f"crm_id=T-{i}" for i in range(21))).text
    assert "CRM ID accepts 20 values or fewer." in body
    assert "<tbody" not in body.split('id="lib-live"')[1].split("</div>")[0]
    page = _client().get(f"{prefix}?contract_status=WON&in_force=1&as_of=2026-06-01").text
    assert 'id="contract_status-label">Contract status</span>' in page
    assert 'id="crm_id-label">CRM ID</span>' in page
    assert 'name="as_of" aria-label="As of date"\n           value="2026-06-01"' in page
    assert "Modeling status" not in page                            # P-13
    assert 'id="owner-label">Owner</span>' in page and "Any owner" in page
    table = _client().get(f"{prefix}/table?contract_status=WON&crm_id=T-1").text
    assert 'id="lib-live"' in table


def test_edm_library_lists_the_edm_the_filters_keep(iteration2_db):
    cheryl = iteration2_db.user_a
    shared = _entity("irp_edm", name="Kept EDM")
    _attach(_deal(name="Won by Cheryl", owner=cheryl, contract_status="WON",
                  crm_ids=["T-100"], expiration=date(2099, 1, 1)), "irp_edm", shared)
    _entity("irp_edm", name="Dropped EDM")
    body = _client().get(f"/edms?crm_id=t-100&contract_status=WON&owner={cheryl}").text
    assert "Kept EDM" in body and "Dropped EDM" not in body
    in_force = _client().get("/edms?in_force=1&as_of=2027-01-01").text
    assert "Kept EDM" in in_force and "Dropped EDM" not in in_force
    assert "Kept EDM" in _client().get("/edms").text and "Dropped EDM" in _client().get("/edms").text


@pytest.mark.parametrize("prefix, table, label",
                         [("/edms", "irp_edm", "EDM"), ("/rdms", "irp_rdm", "RDM")])
def test_library_page_searches_by_word_and_filters_the_unattached(
        iteration2_db, prefix, table, label):
    cheryl = iteration2_db.user_a
    _attach(_deal(name="Cheryl's deal", owner=cheryl), table,
            _entity(table, name="TY 2607 USFL"))
    _entity(table, name="TY USFL 2607 spare", status="importing")
    body = _client().get(f"{prefix}?q=usfl+2607+ty&unattached=1").text
    assert "TY USFL 2607 spare" in body and "TY 2607 USFL<" not in body
    assert f'hx-get="{prefix}/table?q=usfl+2607+ty&amp;unattached=1"' in body
    assert 'name="unattached" value="1" checked' in body
    assert "Risk Modeler ID" not in body
    empty = _client().get(f"{prefix}?unattached=1&owner={cheryl}").text
    assert f"No {label}s match" in empty and "TY" not in empty.split('id="lib-live"')[1]
