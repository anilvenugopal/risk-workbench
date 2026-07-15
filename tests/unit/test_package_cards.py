"""Unit tests for package-card data + the read-only gate (US5, T043).

``package_job_counts`` returns all/active/failed scoped to a package's members;
``get_package_cards`` exposes both member status chips + source paths, sets job counts,
leaves portfolio/analysis empty, and carries **no rolled-up package status** (FR-018).
Create/sync/delete are blocked on a COMPLETED/CANCELLED submission (SC-011).
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

from jinja2 import Environment, FileSystemLoader

from app.poller import run as poller
from app.services import job_query, package_service, submission_service
from app.services import package_sync_service as sync
from app.workers import package_jobs
from db import execute_command, execute_one

MS = sync.MemberSpec


def _submission(actor):
    return submission_service.create_submission(
        name="Deal", cedant_name="Cedant X", treaty_type_code="cat_xol",
        inception_date=dt.date(2026, 1, 1), actor_id=actor).submission_id


def _package(drive, actor, submission_id):
    pid = sync.save_package(
        package_id=None, name="Pkg",
        members=[MS(kind="edm", name="E1", source_file_path=str(drive / "edm1.bak")),
                 MS(kind="rdm", name="R1", source_file_path=str(drive / "rdm1.mdf"))],
        actor_id=actor).package_id
    package_service.attach_to_submission(submission_id=submission_id, package_id=pid,
                                         actor_id=actor)
    return pid


# ── job counts scoped to members ─────────────────────────────────────────────────

def test_package_job_counts_scoped_to_members(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    sid = _submission(a)
    pid = _package(drive, a, sid)
    sync.save_and_sync(package_id=pid, actor_id=a)     # 1 upload_edm rwb_job
    package_jobs.run_pending()                          # → 1 import_edm irp_job (QUEUED)
    counts = job_query.package_job_counts(pid)
    assert counts.all == 2          # 1 rwb_job (succeeded) + 1 irp_job (active)
    assert counts.active == 1       # the QUEUED import job
    assert counts.failed == 0

    row = execute_one(  # find the import job's irp_id
        "SELECT irp_id FROM irp_job WHERE package_id=:p", {"p": pid},
        connection="WORKBENCH")
    fake_irp.fail(str(row["irp_id"]))
    poller.poll_once()
    after = job_query.package_job_counts(pid)
    assert after.active == 0 and after.failed == 1  # terminal failure counted


# ── card shape: chips, source paths, no rolled-up status ─────────────────────────

def test_get_package_cards_exposes_members_and_no_rolled_up_status(
        iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    sid = _submission(a)
    _package(drive, a, sid)
    cards = sync.get_package_cards(sid)
    assert len(cards) == 1
    card = cards[0]
    assert len(card.edms) == 1 and len(card.rdms) == 1
    assert card.edms[0].source_file_path.endswith("edm1.bak")
    assert card.edms[0].status == "pending_import"       # member carries its own chip
    assert card.job_counts is not None                    # counts populated
    assert not hasattr(card, "status")                    # FR-018 — no package rollup


def test_get_package_cards_excludes_deleted_package(iteration2_db, fake_irp, drive):
    a = iteration2_db.user_a
    sid = _submission(a)
    pid = _package(drive, a, sid)
    execute_command("UPDATE package SET deleted_at=:n WHERE id=:p",
                    {"n": "2026-01-01 00:00:00", "p": pid}, connection="WORKBENCH")
    assert sync.get_package_cards(sid) == []


# ── read-only gate (SC-011) ──────────────────────────────────────────────────────

def test_actionable_gate_blocks_closed_submission(iteration2_db, fake_irp, drive):
    from app.routers.packages import _package_actionable
    a = iteration2_db.user_a
    sid = _submission(a)
    pid = _package(drive, a, sid)
    assert _package_actionable(pid) is True             # ACTIVE submission
    execute_command("UPDATE submission SET status_code='COMPLETED' WHERE id=:s",
                    {"s": sid}, connection="WORKBENCH")
    assert _package_actionable(pid) is False            # closed → read-only (SC-011)


# ── live status: self-terminating poll trigger ──────────────────────────────────

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
