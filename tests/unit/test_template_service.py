"""Template and suite service tests for spec 009 user story 2."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import template_service
from app.services.template_service import (
    SuiteItemValues,
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
        "currency_code": "USD",
        "currency_scheme_code": "RMS",
        "currency_vintage": "RL25",
        "min_loss_threshold": Decimal("1.00"),
        "num_max_loss_event": 1,
        "franchise_deductible": False,
        "treat_construction_occupancy_as_unknown": True,
    }
    values.update(changes)
    return TemplateValues(**values)


def _sync(iteration2_db, fake_irp):
    # Populates irp_currency_scheme/irp_currency_scheme_vintage too (T011-T014):
    # the fake's defaults give "RMS" the vintages RL25 (latest)/RL23, and "DT"
    # the vintage RL24 — a code RMS doesn't share, so tests can exercise a
    # vintage that resolves under the wrong scheme without seeding anything.
    metadata_jobs._sync_irp_metadata_body()


def test_dlm_requires_event_rate_scheme(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)

    with pytest.raises(TemplateValidationError) as exc:
        template_service.create_template(
            _values(event_rate_scheme_name=None), actor_id=iteration2_db.user_a
        )

    assert "Event rate scheme is required for DLM analyses" in exc.value.errors


def test_hd_and_accumulation_can_save_without_scheme(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_model_profile
                (id, irp_id, name, is_accumulation, inserted_at, updated_at)
            VALUES
                ('accumulation', 99, 'Global Accumulation', 1,
                 '2026-08-18', '2026-08-18')
        """)

    hd_id = template_service.create_template(
        _values(
            name="US Wind HD",
            analysis_profile_name="RMS Default HD",
            event_rate_scheme_name=None,
        )
    )
    accumulation_id = template_service.create_template(
        _values(
            name="Global Accumulation",
            analysis_profile_name="Global Accumulation",
            event_rate_scheme_name=None,
        )
    )

    assert template_service.get_template(hd_id)["profile_family"] == "HD"
    assert template_service.get_template(accumulation_id)["profile_family"] == "Accumulation"


def test_hd_can_save_with_matching_scheme(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)

    template_id = template_service.create_template(_values(
        name="US Wind HD with scheme",
        analysis_profile_name="RMS Default HD",
    ))

    assert template_service.get_template(template_id)["event_rate_scheme_name"] == "RMS WS"


def test_mismatched_scheme_is_rejected_when_both_cache_rows_resolve(
    iteration2_db, fake_irp,
):
    _sync(iteration2_db, fake_irp)
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
        template_service.create_template(_values(event_rate_scheme_name="RMS EQ"))

    assert any("does not match model profile peril/region" in error
               for error in exc.value.errors)


def test_pairing_check_skips_when_scheme_or_profile_is_absent(
    iteration2_db, fake_irp,
):
    _sync(iteration2_db, fake_irp)

    absent_scheme_id = template_service.create_template(_values(
        name="Unresolved Scheme",
        event_rate_scheme_name="Removed Scheme",
    ))
    absent_profile_id = template_service.create_template(_values(
        name="Unresolved Profile",
        analysis_profile_name="Removed Profile",
        event_rate_scheme_name=None,
    ))

    assert template_service.get_template(absent_scheme_id)["unresolved"] is True
    assert template_service.get_template(absent_profile_id)["unresolved"] is True


def test_live_template_and_suite_names_are_unique(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)
    template_id = template_service.create_template(_values())
    template_service.create_suite("US", [SuiteItemValues(template_id)])

    with pytest.raises(TemplateValidationError, match="already exists"):
        template_service.create_template(_values(name="us wind dlm"))
    with pytest.raises(TemplateValidationError, match="already exists"):
        template_service.create_suite("us", [])


def test_template_delete_guard_names_live_suites(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)
    template_id = template_service.create_template(_values())
    template_service.create_suite("US", [SuiteItemValues(template_id)])
    template_service.create_suite("Global", [SuiteItemValues(template_id)])

    with pytest.raises(TemplateInUseError) as exc:
        template_service.delete_template(template_id)

    assert exc.value.suite_names == ("Global", "US")


def test_unresolved_flag_tracks_cache_removal_and_return(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)
    template_id = template_service.create_template(_values())
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
    _sync(iteration2_db, fake_irp)
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
    _sync(iteration2_db, fake_irp)
    first = template_service.create_template(_values(name="Zebra Template"))
    second = template_service.create_template(_values(
        name="Alpha Template",
        analysis_profile_name="RMS Default HD",
        event_rate_scheme_name=None,
    ))
    suite_id = template_service.create_suite(
        "US", [SuiteItemValues(first), SuiteItemValues(second)]
    )

    # Rewriting the suite in a different order has no effect on display order —
    # suites are an unordered set (P-08); no position/portfolio_name_override.
    template_service.update_suite(
        suite_id, "US", [SuiteItemValues(second), SuiteItemValues(first)],
    )
    suite = template_service.get_suite(suite_id)

    assert [item["template_id"] for item in suite["items"]] == [second, first]
    assert all("position" not in item for item in suite["items"])
    assert all("portfolio_name_override" not in item for item in suite["items"])


def test_suite_rejects_same_template_twice(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)
    template_id = template_service.create_template(_values())

    with pytest.raises(TemplateValidationError, match="only once"):
        template_service.create_suite(
            "US", [SuiteItemValues(template_id), SuiteItemValues(template_id)]
        )


def test_currency_scheme_is_required(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)

    with pytest.raises(TemplateValidationError) as exc:
        template_service.create_template(_values(currency_scheme_code=""))

    assert "Currency scheme is required" in exc.value.errors


def test_currency_vintage_is_required(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)

    with pytest.raises(TemplateValidationError) as exc:
        template_service.create_template(_values(currency_vintage=""))

    assert "Currency vintage is required" in exc.value.errors


def test_currency_scheme_with_no_cached_vintages_blocks_save(iteration2_db, fake_irp):
    _sync(iteration2_db, fake_irp)
    with iteration2_db.engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO irp_currency_scheme
                (id, irp_id, name, code, inserted_at, updated_at)
            VALUES
                ('scheme-empty', 2, 'Empty Scheme', 'EMPTY', '2026-08-19', '2026-08-19')
        """)

    with pytest.raises(TemplateValidationError) as exc:
        template_service.create_template(_values(
            currency_scheme_code="EMPTY", currency_vintage="RL25",
        ))

    assert any('scheme "EMPTY" has no cached vintages' in error
               for error in exc.value.errors)


def test_currency_vintage_not_in_scheme_is_rejected_when_both_resolve(
    iteration2_db, fake_irp,
):
    # The synced defaults give "DT" the vintage "RL24" — it resolves in the
    # cache, but not under the "RMS" scheme the template names here.
    _sync(iteration2_db, fake_irp)

    with pytest.raises(TemplateValidationError) as exc:
        template_service.create_template(_values(
            currency_scheme_code="RMS", currency_vintage="RL24",
        ))

    assert any(
        'does not belong to currency scheme "RMS"' in error
        for error in exc.value.errors
    )


def test_currency_unresolved_when_scheme_or_vintage_absent_from_cache(
    iteration2_db, fake_irp,
):
    _sync(iteration2_db, fake_irp)

    unresolved_scheme_id = template_service.create_template(_values(
        name="Unresolved Currency Scheme",
        currency_scheme_code="MISSING", currency_vintage="RL25",
    ))
    unresolved_vintage_id = template_service.create_template(_values(
        name="Unresolved Currency Vintage",
        currency_vintage="MISSING",
    ))

    scheme_template = template_service.get_template(unresolved_scheme_id)
    assert scheme_template["currency_scheme_unresolved"] is True
    assert scheme_template["unresolved"] is True

    vintage_template = template_service.get_template(unresolved_vintage_id)
    assert vintage_template["currency_vintage_unresolved"] is True
    assert vintage_template["unresolved"] is True
