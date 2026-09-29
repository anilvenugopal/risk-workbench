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
        "name": "US_Wind_DLM",
        "model_profile_irp_id": 1,
        "output_profile_irp_id": 10,
        "event_rate_scheme_irp_id": 20,
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
            _values(event_rate_scheme_irp_id=None), actor_id=iteration2_db.user_a
        )

    assert "Event rate scheme is required for DLM analyses" in exc.value.errors


def test_template_name_outside_the_character_rule_is_rejected(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    with pytest.raises(TemplateValidationError) as exc:
        template_service.save_template(_values(name="Alpha EDM"))

    assert ("Template names may use only letters, numbers, underscores, and hyphens."
            in exc.value.errors)


def test_template_tag_outside_the_character_rule_is_rejected(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    with pytest.raises(TemplateValidationError) as exc:
        template_service.save_template(_values(), tags=["US", "Wind (EU)"])

    assert ("Tag names may use only letters, numbers, underscores, and hyphens."
            in exc.value.errors)


def test_suite_name_outside_the_character_rule_is_rejected(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())

    with pytest.raises(TemplateValidationError) as exc:
        template_service.save_suite("Alpha EDM", [template_id])

    assert ("Suite names may use only letters, numbers, underscores, and hyphens."
            in exc.value.errors)


def test_accumulation_profile_is_rejected(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_model_profile
                (id, irp_id, name, is_accumulation, inserted_at, updated_at)
            VALUES
                ('accumulation', 99, 'Global Accumulation', 1,
                 '2026-08-18', '2026-08-18')
        """)

    with pytest.raises(TemplateValidationError) as exc:
        template_service.save_template(_values(
            name="Global_Accumulation", model_profile_irp_id=99,
            event_rate_scheme_irp_id=None,
        ))

    assert exc.value.errors == ("Accumulation model profiles are not supported",)


def test_profile_without_software_version_is_rejected(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_model_profile
                (id, irp_id, name, is_accumulation, software_version_code,
                 inserted_at, updated_at)
            VALUES
                ('no-version', 98, 'No Version', 0, NULL,
                 '2026-08-18', '2026-08-18')
        """)

    with pytest.raises(TemplateValidationError) as exc:
        template_service.save_template(_values(
            name="No_Version", model_profile_irp_id=98,
            event_rate_scheme_irp_id=None,
        ))

    assert exc.value.errors == (
        "Model profile has no software version in Risk Modeler",)


def test_hd_can_save_with_matching_scheme(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    template_id = template_service.save_template(_values(
        name="US_Wind_HD_with_scheme",
        model_profile_irp_id=2,
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
        template_service.save_template(_values(event_rate_scheme_irp_id=21))

    assert any("does not match model profile peril/region" in error
               for error in exc.value.errors)


def test_ids_absent_from_the_cache_are_rejected(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    with pytest.raises(TemplateValidationError) as absent_scheme:
        template_service.save_template(_values(event_rate_scheme_irp_id=999))
    with pytest.raises(TemplateValidationError) as absent_profile:
        template_service.save_template(_values(
            model_profile_irp_id=999, event_rate_scheme_irp_id=None,
        ))

    assert absent_scheme.value.errors == (
        "Event rate scheme not found in Risk Modeler",)
    assert absent_profile.value.errors == (
        "Model profile not found in Risk Modeler",)
    assert template_service.list_templates() == []


def test_save_stores_display_names(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    dlm_id = template_service.save_template(_values())
    hd_id = template_service.save_template(_values(
        name="US_Wind_HD", model_profile_irp_id=2, event_rate_scheme_irp_id=None,
    ))

    dlm = template_service.get_template(dlm_id)
    assert dlm["analysis_profile_name"] == "RMS Default RL25"
    assert dlm["output_profile_name"] == "RMS Default Output"
    assert dlm["event_rate_scheme_name"] == "RMS WS"
    hd = template_service.get_template(hd_id)
    assert hd["analysis_profile_name"] == "RMS Default HD"
    assert hd["event_rate_scheme_name"] is None


def test_duplicate_scheme_names_resolve_to_one_row(iteration2_db, fake_irp):
    """Issue 68: Risk Modeler reuses event rate scheme names across
    peril/region. Joining by irp_id keeps one template to one row."""
    metadata_jobs._sync_irp_metadata_body()
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_event_rate_scheme
                (id, irp_id, name, peril_code, model_region_code, is_hd,
                 inserted_at, updated_at)
            VALUES
                ('eq-ws', 21, 'RMS WS', 'EQ', 'NAEQ', 0,
                 '2026-08-18', '2026-08-18')
        """)

    template_id = template_service.save_template(_values())
    template_service.save_suite("US", [template_id])

    templates = template_service.list_templates()
    assert [(t["id"], t["unresolved"]) for t in templates] == [(template_id, False)]
    assert template_service.list_suites()[0]["item_count"] == 1


def test_live_template_and_suite_names_are_unique(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())
    template_service.save_suite("US", [template_id])

    with pytest.raises(TemplateValidationError, match="already exists"):
        template_service.save_template(_values(name="us_wind_dlm"))
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
    one = template_service.scheme_options(1)
    zero = template_service.scheme_options(3)

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
    multiple = template_service.scheme_options(1)
    assert len(multiple) == 2
    assert not any(row["selected"] for row in multiple)


def test_scheme_prefill_skips_non_dlm_profiles(iteration2_db, fake_irp):
    """A scheme is required for DLM only, so the lone-match pre-fill stays off
    an HD profile — it offers the same options with nothing chosen."""
    metadata_jobs._sync_irp_metadata_body()

    options = template_service.scheme_options(2)

    assert [row["name"] for row in options] == ["RMS WS"]
    assert not any(row["selected"] for row in options)


def test_scheme_options_exclude_workbench_inactive_schemes(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_event_rate_scheme
                (id, irp_id, name, peril_code, model_region_code, is_hd,
                 inserted_at, updated_at)
            VALUES
                ('second-ws', 22, 'RMS WS Alternate', 'WS', 'NAWS', 0,
                 '2026-08-18', '2026-08-18')
        """)
    assert len(template_service.scheme_options(1)) == 2

    template_service.set_scheme_visibility(22, False)

    # Hiding one leaves a single active match, so the auto-prefill applies.
    options = template_service.scheme_options(1)
    assert [(row["name"], row["selected"]) for row in options] == [
        ("RMS WS", True)]

    template_service.set_scheme_visibility(22, True)
    assert len(template_service.scheme_options(1)) == 2


def test_set_scheme_visibility_rejects_unknown_scheme(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()

    with pytest.raises(template_service.TemplateServiceError):
        template_service.set_scheme_visibility(999, False)


def test_hidden_scheme_keeps_existing_template_saveable(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())

    template_service.set_scheme_visibility(20, False)

    # Pairing validation ignores the flag — re-saving the template still works.
    template_service.save_template(
        _values(name="US_Wind_DLM_renamed"), template_id=template_id)


def test_suite_items_are_unordered_and_display_sorts_by_template_name(
    iteration2_db, fake_irp,
):
    metadata_jobs._sync_irp_metadata_body()
    first = template_service.save_template(_values(name="Zebra_Template"))
    second = template_service.save_template(_values(
        name="Alpha_Template",
        model_profile_irp_id=2,
        event_rate_scheme_irp_id=None,
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


def test_list_suites_includes_empty_suites_with_zero_counts(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())
    template_service.save_suite("Full", [template_id])
    template_service.save_suite("Empty", [])

    suites = {suite["name"]: suite for suite in template_service.list_suites()}

    assert suites["Empty"]["item_count"] == 0
    assert suites["Empty"]["unresolved"] is False
    assert suites["Empty"]["items"] == []
    assert [item["template_id"] for item in suites["Full"]["items"]] == [template_id]


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
    assert copy["name"] == "US_Wind_DLM_copy"
    assert copy["analysis_profile_name"] == original["analysis_profile_name"]
    assert copy["output_profile_name"] == original["output_profile_name"]
    assert copy["event_rate_scheme_name"] == original["event_rate_scheme_name"]
    assert copy["model_profile_irp_id"] == 1
    assert copy["output_profile_irp_id"] == 10
    assert copy["event_rate_scheme_irp_id"] == 20
    assert copy["tags"] == ["US", "Wind"]


def test_duplicate_template_name_collision_gets_a_counter(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())

    first_copy = template_service.duplicate_template(template_id)
    second_copy = template_service.duplicate_template(template_id)

    assert template_service.get_template(first_copy)["name"] == "US_Wind_DLM_copy"
    assert template_service.get_template(second_copy)["name"] == "US_Wind_DLM_copy_2"


def test_duplicate_template_truncates_base_to_fit_name_column(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    long_name = "A" * 200
    template_id = template_service.save_template(_values(name=long_name))

    copy_id = template_service.duplicate_template(template_id)

    copy_name = template_service.get_template(copy_id)["name"]
    assert copy_name == "A" * 195 + "_copy"
    assert len(copy_name) == 200


def test_duplicate_suite_copies_membership_not_templates(iteration2_db, fake_irp):
    metadata_jobs._sync_irp_metadata_body()
    template_id = template_service.save_template(_values())
    suite_id = template_service.save_suite("US", [template_id])

    copy_id = template_service.duplicate_suite(suite_id)

    copy = template_service.get_suite(copy_id)
    assert copy_id != suite_id
    assert copy["name"] == "US_copy"
    assert [item["template_id"] for item in copy["items"]] == [template_id]
