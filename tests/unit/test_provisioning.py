"""Unit tests for JIT OIDC provisioning."""

from __future__ import annotations


class TestJitProvisionOidcUser:
    def test_returns_existing_id_when_oid_found(self, monkeypatch):
        import app.auth.provisioning as prov
        from app.services import auth_service

        monkeypatch.setattr(auth_service, "get_user_by_oid",
                            lambda oid: {"id": "existing-uuid"})

        result = prov.jit_provision_oidc_user("oid-123", "a@b.com", "A B")
        assert result == "existing-uuid"

    def test_does_not_insert_when_oid_exists(self, monkeypatch):
        import app.auth.provisioning as prov
        from app.services import auth_service

        monkeypatch.setattr(auth_service, "get_user_by_oid",
                            lambda oid: {"id": "existing-uuid"})
        inserts = []
        monkeypatch.setattr(prov, "execute_command",
                            lambda sql, params, connection=None: inserts.append(params))

        prov.jit_provision_oidc_user("oid-123", "a@b.com", "A B")
        assert inserts == []

    def test_inserts_new_user_when_oid_not_found(self, monkeypatch):
        import app.auth.provisioning as prov
        from app.services import auth_service

        monkeypatch.setattr(auth_service, "get_user_by_oid", lambda oid: None)
        inserts = []
        monkeypatch.setattr(prov, "execute_command",
                            lambda sql, params, connection=None: inserts.append(params))
        monkeypatch.setattr(prov, "execute_one",
                            lambda sql, params, connection=None: {"id": "new-uuid"})

        result = prov.jit_provision_oidc_user("oid-new", "new@b.com", "New User")
        assert result == "new-uuid"
        assert len(inserts) == 1
        assert inserts[0]["oid"] == "oid-new"
        assert inserts[0]["email"] == "new@b.com"
        assert inserts[0]["display_name"] == "New User"

    def test_insert_params_use_workbench_connection(self, monkeypatch):
        import app.auth.provisioning as prov
        from app.services import auth_service

        monkeypatch.setattr(auth_service, "get_user_by_oid", lambda oid: None)
        connections = []
        monkeypatch.setattr(prov, "execute_command",
                            lambda sql, params, connection=None: connections.append(connection))
        monkeypatch.setattr(prov, "execute_one",
                            lambda sql, params, connection=None: {"id": "x"})

        prov.jit_provision_oidc_user("oid-x", "x@x.com", "X")
        assert connections[0] == "WORKBENCH"
