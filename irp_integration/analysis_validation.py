"""
Analysis job type classification and event rate scheme validation.

The functions in this module operate only on values already retrieved from
Risk Modeler. They do not make API requests or require an ``IRPClient``.
"""

from typing import Literal, Optional


def analysis_type_for_software_version(
    software_version_code: str,
) -> Literal["DLM", "HD"]:
    """
    Return the analysis job type for a model profile software version code.

    The result is posted as the analysis job's ``type`` field.

    Args:
        software_version_code: Model profile ``softwareVersionCode``.

    Returns:
        ``"HD"`` when ``software_version_code`` contains ``"HD"``;
        otherwise, ``"DLM"``.
    """
    if "HD" in software_version_code:
        return "HD"
    return "DLM"


def validate_event_rate_scheme_settings(
    software_version_code: str,
    scheme_provided: bool,
    profile_peril_code: Optional[str] = None,
    profile_model_region_code: Optional[str] = None,
    scheme_peril_code: Optional[str] = None,
    scheme_model_region_code: Optional[str] = None,
) -> Optional[str]:
    """
    Validate event rate scheme settings against a model profile.

    Two rules apply, and they cannot both fail: a DLM model profile requires an
    event rate scheme, and an event rate scheme that was supplied must carry the
    model profile's peril and model region. The first rule needs
    ``scheme_provided`` to be ``False``, the second needs it to be ``True``.

    Pair validation is skipped unless all four peril and model region codes are
    known. Risk Modeler omits ``perilCode`` and ``modelRegionCode`` from some
    model profiles and event rate schemes.

    Args:
        software_version_code: Model profile ``softwareVersionCode``.
        scheme_provided: Whether an event rate scheme name was supplied.
        profile_peril_code: Model profile ``perilCode``, if known.
        profile_model_region_code: Model profile ``modelRegionCode``, if known.
        scheme_peril_code: Event rate scheme ``perilCode``, if known.
        scheme_model_region_code: Event rate scheme ``modelRegionCode``, if known.

    Returns:
        The validation error message, or ``None`` when the settings are valid.
    """
    if (
        analysis_type_for_software_version(software_version_code) == "DLM"
        and not scheme_provided
    ):
        return "Event rate scheme is required for DLM analyses"

    pair_known = None not in (
        profile_peril_code,
        profile_model_region_code,
        scheme_peril_code,
        scheme_model_region_code,
    )
    if (
        scheme_provided
        and pair_known
        and (
            scheme_peril_code != profile_peril_code
            or scheme_model_region_code != profile_model_region_code
        )
    ):
        return (
            "Event rate scheme peril/region "
            f"({scheme_peril_code}, {scheme_model_region_code}) does not match "
            "model profile peril/region "
            f"({profile_peril_code}, {profile_model_region_code})"
        )

    return None
