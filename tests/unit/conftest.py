"""Unit-test-level conftest.

Sets the minimum required env vars before any app module is imported,
so that `app.config.Settings()` (which runs at module load) does not fail
with a missing-field validation error in environments without a .env file.
"""

import os

# Must be set before any app.* import triggers Settings() at module level.
os.environ.setdefault("SESSION_SECRET_KEY", "unit-test-secret-key-not-for-production")

import pytest  # noqa: E402


@pytest.fixture()
def fake_irp():
    """Inject an in-memory fake Risk Modeler as the active irp_gateway (Article 12).

    The fake implements the ``IRPGateway`` protocol; the poller/worker code under
    test reaches it through the gateway free functions. Reset after each test so no
    implementation leaks across tests."""
    from app.services import irp_gateway
    from tests.unit.fakes.fake_irp import FakeIRP

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
    monkeypatch.setattr(settings, "shared_drive_root", str(root))
    return root
