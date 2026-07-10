"""CR-003 no-scope regression (SC-010 / FR-032).

Guards that the row-level-security scaffolding removed by CR-003 stays removed:
the `db` package exposes no scope helpers, `db.scope` no longer imports, and no
application source (under `app/` or `db/`) references a `customer_id` column or
the old scope helpers. See specs/002-submission-package-domain (research R8).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import db

# Application source trees whose SQL/queries must never carry the removed
# row-level-security constructs. Tests are intentionally excluded — they may
# name the forbidden tokens as string literals (as this file does).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOTS = (_REPO_ROOT / "app", _REPO_ROOT / "db")
_FORBIDDEN = ("customer_id", "apply_scope", "scoped_execute")


def test_db_exposes_no_scope_helpers():
    assert not hasattr(db, "apply_scope")
    assert not hasattr(db, "scoped_execute")
    assert "apply_scope" not in db.__all__
    assert "scoped_execute" not in db.__all__


def test_db_scope_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("db.scope")


def test_no_source_references_scope_or_customer_id():
    offenders = []
    for root in _SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in _FORBIDDEN:
                if token in text:
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}: {token}")
    assert offenders == [], f"forbidden CR-003 tokens present in source: {offenders}"
