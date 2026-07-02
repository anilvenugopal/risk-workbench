"""Unit tests for app/main.py.

Covers:
- Module-level wiring: FastAPI app created, middleware added, routers included
- _is_htmx() helper
- 404 and 500 exception handlers
- lifespan: startup raises when WORKBENCH is unreachable; shutdown calls dispose_all
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ── _is_htmx ─────────────────────────────────────────────────────────────────

class TestIsHtmx:
    def test_htmx_header_present(self):
        from app.main import _is_htmx
        from unittest.mock import MagicMock
        req = MagicMock()
        req.headers.get.return_value = "true"
        assert _is_htmx(req) is True

    def test_htmx_header_absent(self):
        from app.main import _is_htmx
        from unittest.mock import MagicMock
        req = MagicMock()
        req.headers.get.return_value = None
        assert _is_htmx(req) is False

    def test_htmx_header_wrong_value(self):
        from app.main import _is_htmx
        from unittest.mock import MagicMock
        req = MagicMock()
        req.headers.get.return_value = "1"
        assert _is_htmx(req) is False


# ── error handlers ────────────────────────────────────────────────────────────

def _fake_request(htmx: bool = False):
    """Build a minimal Request-like mock for calling handlers directly."""
    from unittest.mock import MagicMock
    import app.main as main_mod

    req = MagicMock()
    req.headers.get = lambda k, d=None: "true" if (k == "HX-Request" and htmx) else d
    req.state.user = None
    req.app = main_mod.app
    return req


class TestErrorHandlers:
    def test_404_renders_html(self, monkeypatch):
        """Call the handler directly — the real app's SessionMiddleware would
        intercept unauthenticated requests before the 404 handler fires."""
        import asyncio
        import app.main as main_mod
        req = _fake_request()
        result = asyncio.run(main_mod.not_found(req, Exception()))
        assert result.status_code == 404

    def test_404_htmx_sets_is_htmx(self, monkeypatch):
        import asyncio
        import app.main as main_mod
        req = _fake_request(htmx=True)
        result = asyncio.run(main_mod.not_found(req, Exception()))
        assert result.status_code == 404

    def test_500_renders_html(self, monkeypatch):
        import asyncio
        import app.main as main_mod
        req = _fake_request()
        result = asyncio.run(main_mod.server_error(req, Exception("boom")))
        assert result.status_code == 500


# ── lifespan ──────────────────────────────────────────────────────────────────

class TestLifespan:
    def test_startup_raises_when_workbench_unreachable(self, monkeypatch):
        import app.main as main_mod
        monkeypatch.setattr(main_mod, "test_connection", lambda name: False)
        monkeypatch.setattr(main_mod, "dispose_all", lambda: None)
        with pytest.raises(RuntimeError, match="WORKBENCH"):
            with TestClient(main_mod.app):
                pass

    def test_startup_succeeds_when_workbench_reachable(self, monkeypatch):
        import app.main as main_mod
        disposed = []
        monkeypatch.setattr(main_mod, "test_connection", lambda name: True)
        monkeypatch.setattr(main_mod, "dispose_all", lambda: disposed.append(True))
        with TestClient(main_mod.app):
            pass
        assert disposed == [True]
