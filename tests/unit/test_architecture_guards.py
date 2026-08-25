"""Architecture guards asserted as source scans, so a regression trips the unit
tier rather than production:

1. **Article 11 — single-status-check only.** ``poll_*_to_completion`` (and any
   poll-inside convenience wrapper) blocks for minutes and is forbidden. The
   poller uses single-status ``get_*`` checks; the ``irp_gateway`` — the sole
   module wrapping ``irp-integration`` (T007) — never wraps a poll-to-completion.
2. **Article 6 / FR-041 — no row-level security on the async entities.** No
   ``customer_id`` column, no ``apply_scope`` / ``scoped_execute`` helper, no
   ``user_customer_access`` gate anywhere the EDM/RDM/job modules live
   (services, routers, worker, poller, and the single Alembic revision). Every
   authenticated analyst sees every entity; ownership reaches a submission only
   through submission association tables. (Complements ``test_no_scope.py``, which
   guards the wider ``app/`` + ``db/`` trees; this adds the Alembic schema.)
3. **Spec 005 — the breakout request path.** Routers never touch
   ``irp_gateway``, the request path opens no DataBridge connection, and every
   seeded breakout dimension carries its full vocabulary.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"
_POLLER = _APP / "poller"
_GATEWAY = _APP / "services" / "irp_gateway.py"
_MIGRATION = _REPO_ROOT / "alembic" / "versions" / "0001_initial.py"

# Matches a real poll-to-completion identifier (e.g. ``poll_edm_import_to_completion``)
# but NOT the doc form ``poll_*_to_completion`` the poller/gateway use in prose — the
# literal ``*`` is not a word char, so ``poll\w*_to_completion`` cannot bridge it.
_POLL_TO_COMPLETION = re.compile(r"poll\w*_to_completion")

# CR-003 / FR-041 row-level-security tokens that must never reach the async entities.
_FORBIDDEN_SCOPE = ("customer_id", "apply_scope", "scoped_execute", "user_customer_access")


def _offenders(paths, pattern):
    hits = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{path.relative_to(_REPO_ROOT)}:{line}: {match.group(0)}")
    return hits


def _strip_line_comments(text):
    """Drop ``#`` line comments so affirmative-compliance notes (e.g. the migration's
    "no customer_id/scope column (Article 6)") aren't scanned as if they were code."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_poller_never_polls_to_completion():
    """Article 11: no ``poll_*_to_completion`` anywhere under app/poller/."""
    offenders = _offenders(_POLLER.rglob("*.py"), _POLL_TO_COMPLETION)
    assert offenders == [], f"poll_*_to_completion is forbidden in the poller: {offenders}"


def test_irp_gateway_never_wraps_poll_to_completion():
    """Article 11 / T007: the sole irp-integration wrapper exposes no poll-to-completion."""
    offenders = _offenders([_GATEWAY], _POLL_TO_COMPLETION)
    assert offenders == [], f"irp_gateway must not wrap poll_*_to_completion: {offenders}"


def test_workers_never_poll_to_completion():
    """Article 11 (spec 004 T052): the Dramatiq worker tier — where every Risk
    Modeler detail read now runs — never blocks on a poll-to-completion."""
    offenders = _offenders((_APP / "workers").rglob("*.py"), _POLL_TO_COMPLETION)
    assert offenders == [], f"poll_*_to_completion is forbidden in workers: {offenders}"


def test_no_scope_construct_on_async_entities():
    """Article 6 / FR-041: no customer/scope construct on EDM/RDM/job sources."""
    paths = list(_APP.rglob("*.py"))
    if _MIGRATION.exists():
        paths.append(_MIGRATION)
    offenders = []
    for path in paths:
        text = _strip_line_comments(path.read_text(encoding="utf-8"))
        for token in _FORBIDDEN_SCOPE:
            if token in text:
                offenders.append(f"{path.relative_to(_REPO_ROOT)}: {token}")
    assert offenders == [], f"row-level-security constructs present (Article 6/FR-041): {offenders}"


# ── Spec 005 (T042): the breakout request path ────────────────────────────────────
# Article 11 as applied to the confirm flow (contracts/http-routes.md): the web
# layer performs no IRP call itself — routers never touch irp_gateway, and the
# request path opens no DataBridge connection of its own.

_BREAKOUT_SERVICE = _APP / "services" / "breakout_service.py"


def test_routers_never_touch_irp_gateway():
    """No router imports or calls irp_gateway — every RM interaction on a
    request path is mediated by a service."""
    offenders = _offenders((_APP / "routers").rglob("*.py"),
                           re.compile(r"irp_gateway"))
    assert offenders == [], f"routers must not touch irp_gateway: {offenders}"


def test_every_seeded_breakout_dimension_has_its_vocabulary():
    """Per-dimension registration lockstep. Every value dimension (lob, state,
    country, peril) needs the ``portfolio_number`` letter, the noun and its
    plural (the chooser tile renders the count line from them), both
    DataBridge scripts (selection + coverage), the ``run_breakout_{code}``
    job-type seed, and the worker body — a missing entry composes a wrong
    number, renders a missing noun, or fails the run."""
    from app.services import breakout_service, irp_gateway
    from app.workers import portfolio_jobs
    from tests.iteration1_mirror import (
        BREAKOUT_DIMENSION_SEED,
        RWB_JOB_TYPE_SEED,
    )

    seeded = {code for code, _label, _order in BREAKOUT_DIMENSION_SEED}
    values = seeded - {"custom"}
    assert values == {"lob", "state", "country", "peril"}

    job_types = {code for code, _label, _order in RWB_JOB_TYPE_SEED}
    for code in values:
        assert code in breakout_service._DIMENSION_NOUN, code
        assert code in breakout_service._DIMENSION_NOUN_PLURAL, code
        assert code in breakout_service._DIMENSION_LETTER, code
        assert code in irp_gateway._SELECTION_SCRIPTS, code
        assert code in irp_gateway._COVERAGE_SCRIPTS, code
        # A value dimension with no clause in breakout_match_count.sql would
        # have its filter dropped, and the Add-time count would then include
        # accounts the breakout excludes (P-29).
        assert code in irp_gateway._MATCH_COUNT_PARAMS, code
        assert f"run_breakout_{code}" in job_types, code
        assert f"run_breakout_{code}" in portfolio_jobs._BODIES, code
    # custom (T-12): the grouping lineage code — the job type and the group
    # worker body, but NO number letter (P-26: a group's number is its name
    # truncated to 20); selections run through the value dimensions' scripts,
    # so it must never gain scripts of its own.
    assert "custom" not in breakout_service._DIMENSION_LETTER
    assert "custom" in breakout_service._DIMENSION_NOUN
    assert "run_breakout_custom" in job_types
    assert "run_breakout_custom" in portfolio_jobs._BODIES
    assert "custom" not in irp_gateway._SELECTION_SCRIPTS
    assert "custom" not in irp_gateway._COVERAGE_SCRIPTS
    assert "custom" not in irp_gateway._MATCH_COUNT_PARAMS

    sql_dir = _REPO_ROOT / "sql" / "databridge"
    absent = [script for scripts in (irp_gateway._SELECTION_SCRIPTS,
                                     irp_gateway._COVERAGE_SCRIPTS,
                                     {"match": irp_gateway._MATCH_COUNT_SCRIPT})
              for script in scripts.values()
              if not (sql_dir / script).is_file()]
    assert absent == [], f"registered script missing from sql/databridge: {absent}"


def test_no_databridge_on_request_path():
    """The web layer never opens a DataBridge connection itself and never runs a
    trusted script: the summary the modal renders is the STORED one, and the one
    permitted request-path read goes through ``irp_gateway`` and a repo-owned SQL
    file (Article 11, request-path exception v3.2.0)."""
    paths = [*(_APP / "routers").rglob("*.py"), _BREAKOUT_SERVICE]
    offenders = []
    for path in paths:
        text = _strip_line_comments(path.read_text(encoding="utf-8"))
        for token in ("DATABRIDGE", "execute_script_file"):
            if token in text:
                offenders.append(f"{path.relative_to(_REPO_ROOT)}: {token}")
    assert offenders == [], (
        f"DataBridge access is worker-side only (Article 11): {offenders}")
