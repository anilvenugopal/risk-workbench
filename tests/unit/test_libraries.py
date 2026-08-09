"""Database-free tests for the global EDM/RDM library pages (US7, T057):
the self-terminating live-poll trigger in ``partials/library_table.html``,
the ``/edms/table`` / ``/rdms/table`` fragment routes, and the
``TRANSIENT_STATUSES`` contract on both services. The list-query tests
(scoping, filters, owning-submission attach) live in
``tests/sqlserver/test_libraries.py``.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from fastapi.templating import Jinja2Templates

from app.services import edm_service, package_service, rdm_service
from tests.unit.test_name_check_routes import _client

# (module, child table) for the two sibling libraries.
LIBS = [(edm_service, "irp_edm"), (rdm_service, "irp_rdm")]


def test_submission_refs_for_packages_empty_input():
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
