"""Unit tests for app.config computed fields."""

from __future__ import annotations


def _make_settings(**kwargs):
    """Instantiate a fresh Settings object without reading .env or env vars.

    All fields are passed as keyword arguments so no eviction of sys.modules
    is needed — the module singleton stays intact for other test modules.
    """
    from app.config import Settings
    defaults = dict(
        session_secret_key="test-secret-key-for-testing-only",
        app_env="development",
        auth_mode="both",
    )
    defaults.update(kwargs)
    return Settings(_env_file=None, **defaults)


class TestIsProduction:
    def test_false_when_development(self):
        s = _make_settings(app_env="development")
        assert s.is_production is False

    def test_true_when_production(self):
        s = _make_settings(app_env="production")
        assert s.is_production is True


class TestPasswordAuthEnabled:
    def test_true_for_password_mode(self):
        s = _make_settings(auth_mode="password")
        assert s.password_auth_enabled is True

    def test_true_for_both_mode(self):
        s = _make_settings(auth_mode="both")
        assert s.password_auth_enabled is True

    def test_false_for_oidc_mode(self):
        s = _make_settings(auth_mode="oidc")
        assert s.password_auth_enabled is False


class TestOidcAuthEnabled:
    def test_true_for_oidc_mode(self):
        s = _make_settings(auth_mode="oidc")
        assert s.oidc_auth_enabled is True

    def test_true_for_both_mode(self):
        s = _make_settings(auth_mode="both")
        assert s.oidc_auth_enabled is True

    def test_false_for_password_mode(self):
        s = _make_settings(auth_mode="password")
        assert s.oidc_auth_enabled is False
