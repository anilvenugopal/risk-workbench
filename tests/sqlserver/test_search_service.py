"""Unit tests for app/services/search_service.py (PRD §19 global search).

Runs on the WORKBENCH test database (``workbench_db``) — every provider goes
through the ordinary ``db.execute`` bound-parameter path, so no provider needs
its own fixture beyond a handful of direct-insert rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text

from app.services import edm_service, search_service, submission_service


def _now() -> datetime:
    return datetime.utcnow()


def _insert_edm(engine, *, name: str) -> str:
    eid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO irp_edm (id, name, status, inserted_at, updated_at) "
            "VALUES (:id, :name, 'ready', :now, :now)"
        ), {"id": eid, "name": name, "now": _now()})
    return eid


def _insert_rdm(engine, *, name: str) -> str:
    rid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO irp_rdm (id, name, status, inserted_at, updated_at) "
            "VALUES (:id, :name, 'ready', :now, :now)"
        ), {"id": rid, "name": name, "now": _now()})
    return rid


def _insert_template(engine, *, name: str) -> str:
    tid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO analysis_template (id, name, analysis_profile_name, "
            "output_profile_name, inserted_at, updated_at) "
            "VALUES (:id, :name, 'Profile', 'Output', :now, :now)"
        ), {"id": tid, "name": name, "now": _now()})
    return tid


def _insert_user(engine, *, display_name: str, email: str) -> str:
    uid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO app_user (id, email, display_name, is_active) "
            "VALUES (:id, :email, :name, 1)"
        ), {"id": uid, "email": email, "name": display_name})
    return uid


class TestGlobalSearchFloor:
    def test_blank_term_returns_no_groups(self, workbench_db):
        assert search_service.global_search("", user_roles=["analyst"]) == []

    def test_below_min_term_returns_no_groups(self, workbench_db):
        assert search_service.global_search("a", user_roles=["analyst"]) == []


class TestPagesProvider:
    def test_matches_by_label(self, workbench_db):
        groups = search_service.global_search("submissio", user_roles=["analyst"])
        pages = next(g for g in groups if g.type == "pages")
        assert any(item.label == "Submissions" for item in pages.items)

    # Every current role-gated manifest node also has searchable=False (the
    # Administration rail root), so these exercise _pages' own role check
    # against a fake node rather than depending on that combination staying
    # true in the real manifest.

    def test_role_gated_node_hidden_from_a_role_without_access(
            self, workbench_db, monkeypatch):
        node = {"key": "x", "label": "Restricted Page", "route": "/x",
                "roles": ["admin"], "searchable": True}
        monkeypatch.setattr(search_service, "searchable_nodes", lambda: [node])
        assert search_service._pages("restricted", ["analyst"]) is None

    def test_role_gated_node_visible_to_a_role_with_access(
            self, workbench_db, monkeypatch):
        node = {"key": "x", "label": "Restricted Page", "route": "/x",
                "roles": ["admin"], "searchable": True}
        monkeypatch.setattr(search_service, "searchable_nodes", lambda: [node])
        pages = search_service._pages("restricted", ["admin"])
        assert pages is not None
        assert pages.items[0].label == "Restricted Page"


class TestSubmissionsProvider:
    def test_matches_by_name(self, workbench_db):
        submission_service.create_submission(
            name="Coastal Re HO 2026", cedant_name="Coastal Re",
            treaty_type_code="cat_xol", inception_date="2026-01-01",
            actor_id=workbench_db.user_a, confirmed=True,
        )
        groups = search_service.global_search("coastal", user_roles=["analyst"])
        submissions = next(g for g in groups if g.type == "submissions")
        assert submissions.items[0].label == "Coastal Re HO 2026"

    def test_matches_by_crm_id(self, workbench_db):
        result = submission_service.create_submission(
            name="Zenith Mutual 2026", cedant_name="Zenith Mutual",
            treaty_type_code="cat_xol", inception_date="2026-01-01",
            actor_id=workbench_db.user_a, confirmed=True,
        )
        submission_service.add_crm_id(
            submission_id=result.submission_id, crm_id="CRM-9912",
            actor_id=workbench_db.user_a,
        )
        groups = search_service.global_search("9912", user_roles=["analyst"])
        submissions = next(g for g in groups if g.type == "submissions")
        assert submissions.items[0].label == "Zenith Mutual 2026"

    def test_no_match_omits_group(self, workbench_db):
        groups = search_service.global_search("nonexistent", user_roles=["analyst"])
        assert not any(g.type == "submissions" for g in groups)


class TestEdmRdmProvider:
    def test_edm_matches_by_name(self, workbench_db):
        _insert_edm(workbench_db.engine, name="Coastal HO 2026")
        groups = search_service.global_search("coastal", user_roles=["analyst"])
        edms = next(g for g in groups if g.type == "edms")
        assert edms.items[0].label == "Coastal HO 2026"

    def test_rdm_matches_by_name(self, workbench_db):
        _insert_rdm(workbench_db.engine, name="Broker RDM Alpha")
        groups = search_service.global_search("broker", user_roles=["analyst"])
        rdms = next(g for g in groups if g.type == "rdms")
        assert rdms.items[0].label == "Broker RDM Alpha"


class TestTemplatesProvider:
    def test_matches_by_name(self, workbench_db):
        _insert_template(workbench_db.engine, name="US Wind DLM")
        groups = search_service.global_search("wind", user_roles=["analyst"])
        templates = next(g for g in groups if g.type == "templates")
        assert templates.items[0].label == "US Wind DLM"

    def test_soft_deleted_excluded(self, workbench_db):
        tid = _insert_template(workbench_db.engine, name="Retired Template")
        with workbench_db.engine.begin() as conn:
            conn.execute(text(
                "UPDATE analysis_template SET deleted_at = :now WHERE id = :id"
            ), {"now": _now(), "id": tid})
        groups = search_service.global_search("retired", user_roles=["analyst"])
        assert not any(g.type == "templates" for g in groups)


class TestUsersProvider:
    def test_matches_by_display_name(self, workbench_db):
        _insert_user(workbench_db.engine, display_name="Priya Sharma",
                    email="priya.sharma@example.com")
        groups = search_service.global_search("priya", user_roles=["analyst"])
        users = next(g for g in groups if g.type == "users")
        assert users.items[0].label == "Priya Sharma"

    def test_matches_by_email(self, workbench_db):
        _insert_user(workbench_db.engine, display_name="Priya Sharma",
                    email="priya.sharma@example.com")
        groups = search_service.global_search("sharma@example",
                                               user_roles=["analyst"])
        users = next(g for g in groups if g.type == "users")
        assert users.items[0].label == "Priya Sharma"

    def test_inactive_user_excluded(self, workbench_db):
        uid = _insert_user(workbench_db.engine, display_name="Departed Analyst",
                          email="departed@example.com")
        with workbench_db.engine.begin() as conn:
            conn.execute(text(
                "UPDATE app_user SET is_active = 0 WHERE id = :id"), {"id": uid})
        groups = search_service.global_search("departed", user_roles=["analyst"])
        assert not any(g.type == "users" for g in groups)


class TestTypeFilter:
    def test_narrows_to_one_provider(self, workbench_db):
        _insert_edm(workbench_db.engine, name="Coastal HO 2026")
        submission_service.create_submission(
            name="Coastal Re HO 2026", cedant_name="Coastal Re",
            treaty_type_code="cat_xol", inception_date="2026-01-01",
            actor_id=workbench_db.user_a, confirmed=True,
        )
        groups = search_service.global_search(
            "coastal", user_roles=["analyst"], type="edms")
        assert [g.type for g in groups] == ["edms"]

    def test_unknown_type_returns_no_groups(self, workbench_db):
        _insert_edm(workbench_db.engine, name="Coastal HO 2026")
        groups = search_service.global_search(
            "coastal", user_roles=["analyst"], type="bogus")
        assert groups == []


class TestGroupCap:
    def test_caps_at_group_limit(self, workbench_db):
        for i in range(search_service.GROUP_LIMIT + 3):
            _insert_edm(workbench_db.engine, name=f"Matching EDM {i}")
        groups = search_service.global_search("matching", user_roles=["analyst"])
        edms = next(g for g in groups if g.type == "edms")
        assert len(edms.items) == search_service.GROUP_LIMIT
