"""Tests for public analysis classification and validation helpers."""

import pytest

from irp_integration.analysis_validation import (
    analysis_type_for_software_version,
    validate_event_rate_scheme_settings,
)


@pytest.mark.parametrize(
    ("software_version_code", "expected"),
    [
        *[(f"RL{version}", "DLM") for version in range(18, 26)],
        ("HDv1.0", "HD"),
        ("HDv2.0", "HD"),
        ("HDv3.0", "HD"),
        ("Open", "DLM"),
    ],
)
def test_analysis_type_for_software_version(software_version_code, expected):
    """Classify the observed Risk Modeler software version codes."""
    assert analysis_type_for_software_version(software_version_code) == expected


@pytest.mark.parametrize("software_version_code", ["RL18", "RL25", "Open"])
def test_dlm_requires_event_rate_scheme(software_version_code):
    """Require an event rate scheme for every DLM software version."""
    assert validate_event_rate_scheme_settings(
        software_version_code,
        scheme_provided=False,
        profile_peril_code="WS",
        profile_model_region_code="NAWS",
    ) == "Event rate scheme is required for DLM analyses"


@pytest.mark.parametrize("scheme_provided", [False, True])
def test_event_rate_scheme_is_optional_for_hd(scheme_provided):
    """Allow an HD analysis with or without an event rate scheme."""
    assert validate_event_rate_scheme_settings(
        "HDv3.0",
        scheme_provided=scheme_provided,
        profile_peril_code="WS",
        profile_model_region_code="NAWS",
    ) is None


def test_matching_event_rate_scheme_pair_is_valid():
    """Accept matching model profile and event rate scheme pairs."""
    assert validate_event_rate_scheme_settings(
        "RL25",
        scheme_provided=True,
        profile_peril_code="WS",
        profile_model_region_code="NAWS",
        scheme_peril_code="WS",
        scheme_model_region_code="NAWS",
    ) is None


def test_mismatched_event_rate_scheme_pair_returns_error():
    """Name both pairs when an event rate scheme does not match."""
    assert validate_event_rate_scheme_settings(
        "RL25",
        scheme_provided=True,
        profile_peril_code="WS",
        profile_model_region_code="NAWS",
        scheme_peril_code="EQ",
        scheme_model_region_code="USEQ",
    ) == (
        "Event rate scheme peril/region (EQ, USEQ) does not match "
        "model profile peril/region (WS, NAWS)"
    )


@pytest.mark.parametrize(
    (
        "profile_peril_code",
        "profile_model_region_code",
        "scheme_peril_code",
        "scheme_model_region_code",
    ),
    [
        # An event rate scheme with either code missing.
        ("WS", "NAWS", None, "NAWS"),
        ("WS", "NAWS", "WS", None),
        ("WS", "NAWS", None, None),
        # A model profile with either code missing. Risk Modeler omits both
        # fields from some model profiles, and the caller reads them with
        # ``.get()``, so ``None`` reaches this function.
        (None, "NAWS", "EQ", "USEQ"),
        ("WS", None, "EQ", "USEQ"),
        (None, None, "EQ", "USEQ"),
    ],
)
def test_unknown_peril_region_pair_skips_pair_validation(
    profile_peril_code,
    profile_model_region_code,
    scheme_peril_code,
    scheme_model_region_code,
):
    """Skip pair validation when any of the four codes is unknown."""
    assert validate_event_rate_scheme_settings(
        "RL25",
        scheme_provided=True,
        profile_peril_code=profile_peril_code,
        profile_model_region_code=profile_model_region_code,
        scheme_peril_code=scheme_peril_code,
        scheme_model_region_code=scheme_model_region_code,
    ) is None
