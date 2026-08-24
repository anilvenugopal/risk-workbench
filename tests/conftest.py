"""Root conftest — tier flags and fixtures shared across tiers.

Three test tiers (Constitution Article 12):
  tests/unit/      — database-free: pure functions, validation, and route
                     behavior against mocked service boundaries. Default CI.
                     No test here executes SQL.
  tests/sqlserver/ — every test that executes application SQL, run against a
                     dedicated SQL Server test database (see
                     tests/sqlserver/conftest.py). Mark: @pytest.mark.sqlserver
  tests/irp/       — requires sandbox IRP. Mark: @pytest.mark.irp

The sqlserver and irp suites are opt-in: they skip unless the corresponding
flag is passed. Tests under tests/sqlserver and tests/irp are marked
automatically by directory.

    pytest tests/unit                          # unit only (default)
    pytest tests/sqlserver --run-sqlserver     # SQL Server suite
    pytest tests/irp --run-irp                 # IRP suite
"""

from __future__ import annotations

import os

import pytest

# Must be set before any app.* import triggers Settings() at module level.
os.environ.setdefault("SESSION_SECRET_KEY", "test-secret-key-not-for-production")


def pytest_addoption(parser):
    parser.addoption("--run-sqlserver", action="store_true", default=False,
                     help="Run SQL Server integration tests")
    parser.addoption("--run-irp", action="store_true", default=False,
                     help="Run IRP integration tests (sandbox)")


def pytest_configure(config):
    config.addinivalue_line("markers",
        "sqlserver: requires a live SQL Server connection")
    config.addinivalue_line("markers",
        "irp: requires a sandbox IRP environment")


def pytest_collection_modifyitems(config, items):
    skip_sql = pytest.mark.skip(reason="Pass --run-sqlserver to run")
    skip_irp = pytest.mark.skip(reason="Pass --run-irp to run")
    for item in items:
        if item.nodeid.startswith("tests/sqlserver/"):
            item.add_marker(pytest.mark.sqlserver)
        if item.nodeid.startswith("tests/irp/"):
            item.add_marker(pytest.mark.irp)
        if "sqlserver" in item.keywords and not config.getoption("--run-sqlserver"):
            item.add_marker(skip_sql)
        if "irp" in item.keywords and not config.getoption("--run-irp"):
            item.add_marker(skip_irp)


# ── Shared fixtures (unit and sqlserver tiers) ────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_name_check_cache():
    """The name-collision TTL cache (issue #11) is module-level state — clear it
    around every test so a seeded collision can't leak into the next test."""
    from app.services import name_check

    name_check.clear_cache()
    yield
    name_check.clear_cache()


@pytest.fixture()
def fake_irp():
    """Inject an in-memory fake Risk Modeler as the active irp_gateway (Article 12).

    The fake implements the ``IRPGateway`` protocol; the poller/worker code under
    test reaches it through the gateway free functions. Reset after each test so no
    implementation leaks across tests."""
    from app.services import irp_gateway
    from tests.fakes.fake_irp import FakeIRP

    fake = FakeIRP()
    irp_gateway.configure(fake)
    yield fake
    irp_gateway.reset()


@pytest.fixture()
def drive(tmp_path, monkeypatch):
    """A real on-disk shared-drive root with a few exposure files, wired into
    ``settings.shared_drive_root`` so ``shared_drive.validate_selection`` (and thus
    ``import_edm``/``import_rdm``) accept selections within it. Returns the root
    ``Path``; build a source path with ``str(drive / 'edm1.bak')``."""
    from app.config import settings

    root = tmp_path / "share"
    root.mkdir()
    for fname in ("edm1.bak", "edm2.bak", "rdm1.mdf", "rdm2.mdf"):
        (root / fname).write_text("x")
    (root / "deals" / "zephyr").mkdir(parents=True)
    monkeypatch.setattr(settings, "shared_drive_root", str(root))
    return root
