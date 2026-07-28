"""Iteration-2 architecture guards (T064 — Article 11 / Article 6 / FR-041).

Two constitutional invariants of the async spine, asserted as source scans so a
regression trips the unit tier rather than production:

1. **Article 11 — single-status-check only.** ``poll_*_to_completion`` (and any
   poll-inside convenience wrapper) blocks for minutes and is forbidden. The
   poller uses single-status ``get_*`` checks; the ``irp_gateway`` — the sole
   module wrapping ``irp-integration`` (T007) — never wraps a poll-to-completion.
2. **Article 6 / FR-041 — no row-level security on the async entities.** No
   ``customer_id`` column, no ``apply_scope`` / ``scoped_execute`` helper, no
   ``user_customer_access`` gate anywhere the EDM/RDM/package/job families live
   (services, routers, worker, poller, and the single Alembic revision). Every
   authenticated analyst sees every entity; ownership reaches a submission only
   transitively through the package. (Complements ``test_no_scope.py``, which
   guards the wider ``app/`` + ``db/`` trees; this adds the Alembic schema.)
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
    """Article 6 / FR-041: no customer/scope construct on EDM/RDM/package/job sources."""
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
