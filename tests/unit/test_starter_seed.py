"""Starter template-suite seed tests."""

from __future__ import annotations

from sqlalchemy import text

from infra.scripts.seed_db import _seed_starter_suites


def test_fresh_database_seeds_four_suites_and_forty_templates(iteration2_db):
    with iteration2_db.engine.begin() as conn:
        seeded = _seed_starter_suites(conn, actor_id=iteration2_db.user_a)
        suite_names = [row[0] for row in conn.execute(text(
            "SELECT name FROM template_suite WHERE deleted_at IS NULL ORDER BY name"
        ))]
        template_count = conn.execute(text(
            "SELECT COUNT(*) FROM analysis_template WHERE deleted_at IS NULL"
        )).scalar()
        item_count = conn.execute(text(
            "SELECT COUNT(*) FROM template_suite_item"
        )).scalar()

    assert seeded is True
    assert suite_names == ["Canada", "Global", "US", "US+Canada"]
    assert template_count == 40
    assert item_count == 40


def test_reseed_skips_when_a_live_suite_exists_and_preserves_edit(iteration2_db):
    with iteration2_db.engine.begin() as conn:
        _seed_starter_suites(conn, actor_id=iteration2_db.user_a)
        conn.execute(text(
            "UPDATE template_suite SET name = 'US Edited' WHERE name = 'US'"
        ))

    with iteration2_db.engine.begin() as conn:
        seeded = _seed_starter_suites(conn, actor_id=iteration2_db.user_a)
        names = {row[0] for row in conn.execute(text(
            "SELECT name FROM template_suite WHERE deleted_at IS NULL"
        ))}

    assert seeded is False
    assert "US Edited" in names
    assert "US" not in names
    assert len(names) == 4
