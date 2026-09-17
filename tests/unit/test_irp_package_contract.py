"""Offline contract between _RealGateway and the pinned irp-integration wheel."""

from __future__ import annotations

import inspect
from importlib.metadata import version

from irp_integration import IRPClient
from irp_integration.analysis import AnalysisManager
from irp_integration.databridge import DataBridgeManager
from irp_integration.edm import EDMManager
from irp_integration.grouping import GroupingManager
from irp_integration.import_job import ImportJobManager
from irp_integration.portfolio import PortfolioManager
from irp_integration.rdm import RDMManager
from irp_integration.reference_data import ReferenceDataManager
from irp_integration.treaty import TreatyManager


def test_production_package_version_and_client_constructor():
    assert version("irp-integration") == "0.8.0"
    assert not [
        parameter
        for parameter in inspect.signature(IRPClient).parameters.values()
        if parameter.default is inspect.Parameter.empty
    ]


def test_real_gateway_manager_methods_exist():
    expected = {
        AnalysisManager: {
            "delete_analysis", "get_analysis_by_id", "get_analysis_job", "get_ep",
            "get_stats", "search_analyses_paginated", "submit_portfolio_analysis_job",
        },
        DataBridgeManager: {"execute_query_from_file"},
        EDMManager: {"search_edms", "search_edms_paginated", "submit_edm_import_job"},
        GroupingManager: {"get_job", "inspect", "submit"},
        ImportJobManager: {"get_import_job"},
        PortfolioManager: {
            "create_portfolio", "get_geohaz_job", "get_portfolio_metadata",
            "search_portfolios_paginated", "submit_geohaz_job",
        },
        RDMManager: {"search_imported_rdms", "submit_rdm_import_job"},
        ReferenceDataManager: {
            "get_event_rate_schemes", "get_model_profiles", "get_output_profiles",
            "search_currencies", "search_currency_scheme_vintages",
            "search_currency_schemes",
        },
        TreatyManager: {"search_treaties_paginated"},
    }

    for manager, methods in expected.items():
        for method in methods:
            assert callable(getattr(manager, method, None)), f"{manager.__name__}.{method}"


def test_grouping_signatures_used_by_gateway():
    inspect_params = inspect.signature(GroupingManager.inspect).parameters
    submit_params = inspect.signature(GroupingManager.submit).parameters
    get_params = inspect.signature(GroupingManager.get_job).parameters

    assert "analysis_ids" in inspect_params
    assert {
        "analysis_ids", "settings", "event_rate_selections",
        "simulation_set_selections", "simulation_periods_selections",
        "expected_inspection_fingerprint",
    } <= set(submit_params)
    assert "job_id" in get_params
