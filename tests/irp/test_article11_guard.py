"""Article-11 guard for the IRP tier (spec 004 T052 / quickstart §4).

Asserts — as part of the opt-in ``--run-irp`` sandbox pass — that the blocking
``poll_*_to_completion`` helpers AND the wheel's poll-inside convenience
methods (``edm.delete_edm()``, ``rdm.export_analyses_to_rdm()``,
``import_job.submit_job()`` — each calls a poll-to-completion internally, see
docs/IRP_INTEGRATION_FOLLOWUPS.md §4) appear nowhere in the worker / poller /
gateway code that performs the spec-004 detail reads. The unit tier carries the
same poll scan (``tests/unit/test_architecture_guards.py``); this file makes
the assertion part of every sandbox verification run, next to the real
round-trips it certifies.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.irp

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_PATHS = (
    _REPO_ROOT / "app" / "services" / "irp_gateway.py",
    *(_REPO_ROOT / "app" / "workers").rglob("*.py"),
    *(_REPO_ROOT / "app" / "poller").rglob("*.py"),
)

# A real poll-to-completion identifier (the doc form ``poll_*_to_completion``
# in prose cannot match — ``*`` is not a word char).
_POLL_TO_COMPLETION = re.compile(r"poll\w*_to_completion")
# The wheel's poll-INSIDE convenience methods (block for minutes when called).
_POLL_INSIDE = re.compile(
    r"\.delete_edm\(|\.export_analyses_to_rdm\(|\.submit_job\(")


def _offenders(pattern: re.Pattern) -> list[str]:
    hits = []
    for path in _SCAN_PATHS:
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{path.relative_to(_REPO_ROOT)}:{line}: {match.group(0)}")
    return hits


def test_no_poll_to_completion_in_detail_read_code():
    offenders = _offenders(_POLL_TO_COMPLETION)
    assert offenders == [], (
        f"poll_*_to_completion is forbidden (Article 11): {offenders}")


def test_no_poll_inside_convenience_methods():
    offenders = _offenders(_POLL_INSIDE)
    assert offenders == [], (
        "the wheel's poll-inside convenience methods are forbidden "
        f"(Article 11 / FOLLOWUPS §4): {offenders}")


def test_breakout_request_path_reads_only_fetch_portfolio_stamp():
    """Spec 005 (T042): the breakout confirm path's ONE Risk Modeler read is
    ``fetch_portfolio_stamp`` (FR-002a — the Article 2 submit-time pattern);
    routers touch no gateway function at all. Mirrors the unit-tier guard in
    tests/unit/test_architecture_guards.py as part of every sandbox run."""
    service = _REPO_ROOT / "app" / "services" / "breakout_service.py"
    text = "\n".join(line.split("#", 1)[0] for line in
                     service.read_text(encoding="utf-8").splitlines())
    calls = set(re.findall(r"irp_gateway\.(\w+)", text))
    assert calls <= {"fetch_portfolio_stamp"}, sorted(calls)
    router_hits = []
    for path in (_REPO_ROOT / "app" / "routers").rglob("*.py"):
        if "irp_gateway" in path.read_text(encoding="utf-8"):
            router_hits.append(str(path.relative_to(_REPO_ROOT)))
    assert router_hits == [], router_hits
