"""Development database scripts reject non-development targets before I/O."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DEV_SCRIPTS = _ROOT / "infra" / "scripts" / "dev"


def _load_script(name: str):
    path = _DEV_SCRIPTS / name
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unexpected_io():
    raise AssertionError("script performed I/O before refusing the target")


def test_bootstrap_db_refuses_production_before_database_access(monkeypatch):
    script = _load_script("bootstrap_db.py")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(script, "_database_names", _unexpected_io)

    assert script.main([]) == 1


def test_seed_dev_fixtures_refuses_production_before_database_access(monkeypatch):
    script = _load_script("seed_dev_fixtures.py")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(script, "_workbench_engine", _unexpected_io)

    assert script.main() == 1


def test_bootstrap_loss_refuses_the_cic_database_before_database_access(
        monkeypatch):
    script = _load_script("bootstrap_loss.py")
    monkeypatch.setenv("MSSQL_LOSS_DATABASE", "CRE_Trial_ELT_Repository")

    assert script.main() == 1


def test_each_runnable_dev_script_has_a_refusal_test():
    assert {path.name for path in _DEV_SCRIPTS.glob("*.py")} == {
        "bootstrap_db.py",
        "bootstrap_loss.py",
        "seed_dev_fixtures.py",
    }
