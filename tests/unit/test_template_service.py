"""Template and suite service tests for spec 009 user story 2."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import template_service
from app.services.template_service import (
    TemplateInUseError,
    TemplateValidationError,
    TemplateValues,
)
from app.workers import metadata_jobs


def _values(**changes) -> TemplateValues:
    values = {
        "name": "US Wind DLM",
        "analysis_profile_name": "RMS Default RL25",
        "output_profile_name": "RMS Default Output",
        "event_rate_scheme_name": "RMS WS",
        "min_loss_threshold": Decimal("1.00"),
        "num_max_loss_event": 1,
        "franchise_deductible": False,
        "treat_construction_occupancy_as_unknown": True,
    }
    values.update(changes)
    return TemplateValues(**values)


def test_dlm_requires_event_rate_scheme(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    with pytest.raises(TemplateValidationError) as exc:
        template_service.save_template(
            _values(event_rate_scheme_name=None), actor_id=iteration2_db.user_a
        )

    assert "Event rate scheme is required for DLM analyses" in exc.value.errors


def test_hd_and_accumulation_can_save_without_scheme(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_model_profile
                (id, irp_id, name, is_accumulation, inserted_at, updated_at)
            VALUES
                ('accumulation', 99, 'Global Accumulation', 1,
                 '2026-08-18', '2026-08-18')
        """)

    hd_id = template_service.save_template(
        _values(
            name="US Wind HD",
            analysis_profile_name="RMS Default HD",
            event_rate_scheme_name=None,
        )
    )
    accumulation_id = template_service.save_template(
        _values(
            name="Global Accumulation",
            analysis_profile_name="Global Accumulation",
            event_rate_scheme_name=None,
        )
    )

    assert template_service.get_template(hd_id)["profile_family"] == "HD"
    assert template_service.get_template(accumulation_id)["profile_family"] == "Accumulation"


def test_hd_can_save_with_matching_scheme(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    template_id = template_service.save_template(_values(
        name="US Wind HD with scheme",
        analysis_profile_name="RMS Default HD",
    ))

    assert template_service.get_template(template_id)["event_rate_scheme_name"] == "RMS WS"


def test_mismatched_scheme_is_rejected_when_both_cache_rows_resolve(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_event_rate_scheme
                (id, irp_id, name, peril_code, model_region_code, is_hd,
                 inserted_at, updated_at)
            VALUES
                ('eq-scheme', 21, 'RMS EQ', 'EQ', 'NAEQ', 0,
                 '2026-08-18', '2026-08-18')
        """)

    with pytest.raises(TemplateValidationError) as exc:
        template_service.save_template(_values(event_rate_scheme_name="RMS EQ"))

    assert any("does not match model profile peril/region" in error
               for error in exc.value.errors)


def test_pairing_check_skips_when_scheme_or_profile_is_absent(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()

    absent_scheme_id = template_service.save_template(_values(
        name="Unresolved Scheme",
        event_rate_scheme_name="Removed Scheme",
    ))
    absent_profile_id = template_service.save_template(_values(
        name="Unresolved Profile",
        analysis_profile_name="Removed Profile",
        event_rate_scheme_name=None,
    ))

    assert template_service.get_template(absent_scheme_id)["unresolved"] is True
    assert template_service.get_template(absent_profile_id)["unresolved"] is True


def test_live_template_and_suite_names_are_unique(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())
    template_service.save_suite("US", [template_id])

    with pytest.raises(TemplateValidationError, match="already exists"):
        template_service.save_template(_values(name="us wind dlm"))
    with pytest.raises(TemplateValidationError, match="already exists"):
        template_service.save_suite("us", [])


def test_template_delete_guard_names_live_suites(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())
    template_service.save_suite("US", [template_id])
    template_service.save_suite("Global", [template_id])

    with pytest.raises(TemplateInUseError) as exc:
        template_service.delete_template(template_id)

    assert exc.value.suite_names == ("Global", "US")


def test_unresolved_flag_tracks_cache_removal_and_return(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())
    assert template_service.get_template(template_id)["unresolved"] is False

    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM irp_model_profile WHERE name = 'RMS Default RL25'"
        )
    assert template_service.get_template(template_id)["model_profile_unresolved"] is True

    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_model_profile
                (id, irp_id, name, is_accumulation, software_version_code,
                 peril_code, model_region_code, inserted_at, updated_at)
            VALUES
                ('returned', 1, 'RMS Default RL25', 0, 'RL25', 'WS', 'NAWS',
                 '2026-08-18', '2026-08-18')
        """)
    assert template_service.get_template(template_id)["unresolved"] is False


def test_scheme_prefill_requires_exactly_one_match(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    one = template_service.scheme_options("RMS Default RL25")
    zero = template_service.scheme_options("Open profile")

    assert [(row["name"], row["selected"]) for row in one] == [("RMS WS", True)]
    assert zero == []

    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_event_rate_scheme
                (id, irp_id, name, peril_code, model_region_code, is_hd,
                 inserted_at, updated_at)
            VALUES
                ('second-ws', 22, 'RMS WS Alternate', 'WS', 'NAWS', 0,
                 '2026-08-18', '2026-08-18')
        """)
    multiple = template_service.scheme_options("RMS Default RL25")
    assert len(multiple) == 2
    assert not any(row["selected"] for row in multiple)


def test_suite_items_are_unordered_and_display_sorts_by_template_name(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    first = template_service.save_template(_values(name="Zebra Template"))
    second = template_service.save_template(_values(
        name="Alpha Template",
        analysis_profile_name="RMS Default HD",
        event_rate_scheme_name=None,
    ))
    suite_id = template_service.save_suite("US", [first, second])

    # Rewriting the suite in a different order has no effect on display order —
    # suites are an unordered set (P-08); no position/portfolio_name_override.
    template_service.save_suite("US", [second, first], suite_id=suite_id)
    suite = template_service.get_suite(suite_id)

    assert [item["template_id"] for item in suite["items"]] == [second, first]
    assert all("position" not in item for item in suite["items"])
    assert all("portfolio_name_override" not in item for item in suite["items"])


def test_suite_rejects_same_template_twice(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())

    with pytest.raises(TemplateValidationError, match="only once"):
        template_service.save_suite("US", [template_id, template_id])


def test_duplicate_template_copies_fields_and_tags(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(
        _values(), tags=["US", "Wind"], actor_id=iteration2_db.user_a,
    )

    copy_id = template_service.duplicate_template(
        template_id, actor_id=iteration2_db.user_a,
    )

    original = template_service.get_template(template_id)
    copy = template_service.get_template(copy_id)
    assert copy_id != template_id
    assert copy["name"] == "US Wind DLM (copy)"
    assert copy["analysis_profile_name"] == original["analysis_profile_name"]
    assert copy["output_profile_name"] == original["output_profile_name"]
    assert copy["event_rate_scheme_name"] == original["event_rate_scheme_name"]
    assert copy["tags"] == ["US", "Wind"]


def test_duplicate_template_name_collision_gets_a_counter(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())

    first_copy = template_service.duplicate_template(template_id)
    second_copy = template_service.duplicate_template(template_id)

    assert template_service.get_template(first_copy)["name"] == "US Wind DLM (copy)"
    assert template_service.get_template(second_copy)["name"] == "US Wind DLM (copy 2)"


def test_duplicate_template_truncates_base_to_fit_name_column(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    long_name = "A" * 200
    template_id = template_service.save_template(_values(name=long_name))

    copy_id = template_service.duplicate_template(template_id)

    copy_name = template_service.get_template(copy_id)["name"]
    assert copy_name == "A" * 193 + " (copy)"
    assert len(copy_name) == 200


def test_duplicate_suite_copies_membership_not_templates(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())
    suite_id = template_service.save_suite("US", [template_id])

    copy_id = template_service.duplicate_suite(suite_id)

    copy = template_service.get_suite(copy_id)
    assert copy_id != suite_id
    assert copy["name"] == "US (copy)"
    assert [item["template_id"] for item in copy["items"]] == [template_id]
