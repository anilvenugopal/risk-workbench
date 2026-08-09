"""Package-card template polling (US5).

Renders ``partials/package_card.html`` in isolation and pins the
self-terminating HTMX poll trigger: poll while a member is in flight or a job
is active, stop once everything is terminal.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from jinja2 import Environment, FileSystemLoader


def _render_card(*, edm_status, active=0, deleted=False):
    """Render package_card.html in isolation and report whether it emits the HTMX
    polling trigger. Guards the self-terminating condition — the card must poll while
    a member is in flight (or a job is active) and stop once everything is terminal."""
    env = Environment(loader=FileSystemLoader("app/templates"))
    env.globals["generate_csrf_token"] = lambda: "tok"
    card = NS(id="c1", name="Pkg", deleted_at="2026-01-01" if deleted else None,
              edms=[NS(id="m1", kind="edm", name="E1", status=edm_status,
                       source_file_path="/x/E1.bak")],
              rdms=[], job_counts=NS(all=1, active=active, failed=0))
    html = env.get_template("partials/package_card.html").render(card=card, is_active=True)
    return 'hx-trigger="every 3s"' in html


def test_card_polls_while_member_in_flight():
    assert _render_card(edm_status="pending_import", active=1) is True
    assert _render_card(edm_status="importing", active=1) is True


def test_card_polls_while_job_active_even_if_member_ready():
    # RDM analyses backfill keeps a job active after the member reaches ready — the
    # job-count pills must stay live, so the card keeps polling.
    assert _render_card(edm_status="ready", active=1) is True


def test_card_stops_polling_when_terminal():
    assert _render_card(edm_status="ready", active=0) is False
    assert _render_card(edm_status="error", active=0) is False


def test_deleted_card_never_polls():
    assert _render_card(edm_status="importing", active=1, deleted=True) is False
