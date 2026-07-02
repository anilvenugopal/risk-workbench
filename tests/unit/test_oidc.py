"""Unit tests for OIDC helpers — initiate_flow, complete_flow, build_logout_url."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_app(flow=None, token_result=None):
    """Return a mock ConfidentialClientApplication."""
    app = MagicMock()
    app.initiate_auth_code_flow.return_value = flow or {
        "auth_uri": "https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize?...",
        "state": "abc",
        "code_verifier": "verifier",
    }
    app.acquire_token_by_auth_code_flow.return_value = token_result or {
        "id_token_claims": {
            "email": "user@example.com",
            "name": "Test User",
            "oid": "oid-123",
        }
    }
    return app


class TestInitiateFlow:
    def test_returns_dict_with_auth_uri(self, monkeypatch):
        import app.auth.oidc as oidc

        monkeypatch.setattr(oidc, "_app", lambda: _make_app())
        flow = oidc.initiate_flow("https://app/auth/callback")
        assert "auth_uri" in flow

    def test_calls_initiate_auth_code_flow_with_email_scope(self, monkeypatch):
        import app.auth.oidc as oidc

        mock_app = _make_app()
        monkeypatch.setattr(oidc, "_app", lambda: mock_app)
        oidc.initiate_flow("https://app/auth/callback")
        mock_app.initiate_auth_code_flow.assert_called_once()
        call_kwargs = mock_app.initiate_auth_code_flow.call_args
        assert call_kwargs.kwargs.get("scopes") == ["email"] or \
               call_kwargs.args[0] == ["email"]

    def test_passes_redirect_uri(self, monkeypatch):
        import app.auth.oidc as oidc

        mock_app = _make_app()
        monkeypatch.setattr(oidc, "_app", lambda: mock_app)
        oidc.initiate_flow("https://app/auth/callback")
        call_kwargs = mock_app.initiate_auth_code_flow.call_args
        assert call_kwargs.kwargs.get("redirect_uri") == "https://app/auth/callback"


class TestCompleteFlow:
    def test_returns_id_token_claims_on_success(self, monkeypatch):
        import app.auth.oidc as oidc

        monkeypatch.setattr(oidc, "_app", lambda: _make_app())
        flow = {"state": "abc", "code_verifier": "v"}
        claims = oidc.complete_flow(flow, {"code": "xyz", "state": "abc"})
        assert claims["email"] == "user@example.com"

    def test_raises_on_msal_error(self, monkeypatch):
        import app.auth.oidc as oidc
        import pytest

        error_result = {"error": "invalid_grant", "error_description": "Token expired"}
        monkeypatch.setattr(oidc, "_app", lambda: _make_app(token_result=error_result))

        with pytest.raises(ValueError, match="Token expired"):
            oidc.complete_flow({}, {"code": "bad"})

    def test_raises_when_email_claim_missing(self, monkeypatch):
        import app.auth.oidc as oidc
        import pytest

        no_email_result = {"id_token_claims": {"name": "No Email", "oid": "x"}}
        monkeypatch.setattr(oidc, "_app", lambda: _make_app(token_result=no_email_result))

        with pytest.raises(ValueError, match="email claim missing"):
            oidc.complete_flow({}, {"code": "xyz"})

    def test_accepts_preferred_username_as_email_fallback(self, monkeypatch):
        import app.auth.oidc as oidc

        result_with_upn = {
            "id_token_claims": {"preferred_username": "user@corp.com", "oid": "x"}
        }
        monkeypatch.setattr(oidc, "_app", lambda: _make_app(token_result=result_with_upn))

        claims = oidc.complete_flow({}, {"code": "xyz"})
        assert claims["preferred_username"] == "user@corp.com"

    def test_raises_on_error_without_description(self, monkeypatch):
        import app.auth.oidc as oidc
        import pytest

        error_result = {"error": "access_denied"}
        monkeypatch.setattr(oidc, "_app", lambda: _make_app(token_result=error_result))

        with pytest.raises(ValueError, match="access_denied"):
            oidc.complete_flow({}, {"code": "xyz"})


class TestBuildLogoutUrl:
    def test_contains_tenant_id(self, monkeypatch):
        import app.auth.oidc as oidc
        import app.config as cfg

        monkeypatch.setattr(cfg.settings, "entra_tenant_id", "my-tenant-id")
        monkeypatch.setattr(cfg.settings, "entra_redirect_uri",
                            "https://app.example.com/auth/callback")

        url = oidc.build_logout_url()
        assert "my-tenant-id" in url

    def test_contains_post_logout_redirect(self, monkeypatch):
        import app.auth.oidc as oidc
        import app.config as cfg

        monkeypatch.setattr(cfg.settings, "entra_tenant_id", "tid")
        monkeypatch.setattr(cfg.settings, "entra_redirect_uri",
                            "https://app.example.com/auth/callback")

        url = oidc.build_logout_url()
        assert "post_logout_redirect_uri" in url

    def test_post_logout_points_to_login_page(self, monkeypatch):
        import app.auth.oidc as oidc
        import app.config as cfg
        from urllib.parse import unquote

        monkeypatch.setattr(cfg.settings, "entra_tenant_id", "tid")
        monkeypatch.setattr(cfg.settings, "entra_redirect_uri",
                            "https://app.example.com/auth/callback")

        url = oidc.build_logout_url()
        assert "auth/login" in unquote(url)
