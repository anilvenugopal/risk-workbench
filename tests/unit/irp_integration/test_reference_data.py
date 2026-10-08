"""
Tests for ``search_currency_schemes`` and the currency search methods'
shared limit/offset/sort/sortOrder pagination parameters.

Pinned here: the ``where`` param is only sent when a filter is given, a
failed request is re-raised as ``IRPAPIError``, and each of ``search_currencies``,
``search_currency_schemes``, and ``search_currency_scheme_vintages`` wires
limit/offset/sort/sortOrder into the request params the same way, rejecting
invalid values before any request is sent.
"""

import pytest

from irp_integration.constants import SEARCH_CURRENCY_SCHEMES
from irp_integration.exceptions import IRPAPIError, IRPValidationError

CURRENCY_SEARCH_METHODS = [
    "search_currencies",
    "search_currency_schemes",
    "search_currency_scheme_vintages",
]


def test_where_clause_is_sent_when_given(make_reference_data_manager, response):
    manager, client = make_reference_data_manager([response(200, json_body={'items': []})])

    manager.search_currency_schemes('currencySchemeCode="RMS"')

    assert client.calls[0]['path'] == SEARCH_CURRENCY_SCHEMES
    assert client.calls[0]['params'] == {'where': 'currencySchemeCode="RMS"'}


def test_where_clause_is_omitted_when_not_given(make_reference_data_manager, response):
    manager, client = make_reference_data_manager([response(200, json_body={'items': []})])

    manager.search_currency_schemes()

    assert client.calls[0]['params'] == {}


def test_request_failure_raises_irp_api_error(make_reference_data_manager):
    manager, client = make_reference_data_manager([RuntimeError("boom")])

    with pytest.raises(IRPAPIError):
        manager.search_currency_schemes()


@pytest.mark.parametrize("method_name", CURRENCY_SEARCH_METHODS)
def test_pagination_and_sort_params_are_sent_when_given(method_name, make_reference_data_manager, response):
    manager, client = make_reference_data_manager([response(200, json_body={'items': []})])

    getattr(manager, method_name)(
        where_clause='code="USD"',
        limit=50,
        offset=100,
        sort="code",
        sort_order=-1
    )

    assert client.calls[0]['params'] == {
        'where': 'code="USD"',
        'limit': 50,
        'offset': 100,
        'sort': 'code',
        'sortOrder': -1,
    }


@pytest.mark.parametrize("method_name", CURRENCY_SEARCH_METHODS)
@pytest.mark.parametrize("kwargs", [{'limit': 0}, {'offset': -1}, {'sort_order': 0}])
def test_invalid_pagination_params_raise_before_request(method_name, kwargs, make_reference_data_manager):
    manager, client = make_reference_data_manager([])

    with pytest.raises(IRPValidationError):
        getattr(manager, method_name)(**kwargs)

    assert client.calls == []


def test_exact_simulation_set_requires_one_atomic_match(make_reference_data_manager, response):
    """Match scheme, model region, and model version on the same row."""
    rows = [
        {
            'id': 10,
            'eventRateSchemeId': 7,
            'modelRegionCode': 'NAWS',
            'modelVersionCode': '11.0',
            'defaultPeriods': 100000,
        },
        {
            'id': 11,
            'eventRateSchemeId': 7,
            'modelRegionCode': 'NAWS',
            'modelVersionCode': '12.0',
            'defaultPeriods': 100000,
        },
    ]
    manager, _ = make_reference_data_manager([
        response(200, json_body={'items': rows}),
    ])

    result = manager.get_simulation_set_exact(
        event_rate_scheme_id=7,
        model_region_code='NAWS',
        model_version='11.0',
    )

    assert result['id'] == 10


@pytest.mark.parametrize('rows', [[], [
    {
        'id': 10,
        'eventRateSchemeId': 7,
        'modelRegionCode': 'NAWS',
        'modelVersionCode': '11.0',
    },
    {
        'id': 12,
        'eventRateSchemeId': 7,
        'modelRegionCode': 'NAWS',
        'modelVersionCode': '11.0',
    },
]])
def test_exact_simulation_set_rejects_zero_or_multiple_matches(
    rows, make_reference_data_manager, response
):
    """Never select a fallback simulation set when cardinality is not one."""
    manager, _ = make_reference_data_manager([
        response(200, json_body={'items': rows}),
    ])

    with pytest.raises(IRPAPIError):
        manager.get_simulation_set_exact(
            event_rate_scheme_id=7,
            model_region_code='NAWS',
            model_version='11.0',
        )


def test_pet_metadata_reads_every_page(make_reference_data_manager, response):
    """Find a PET beyond the former fixed first page of 500 rows."""
    first_page = [{'id': value} for value in range(1, 501)]
    manager, client = make_reference_data_manager([
        response(200, json_body={'items': first_page}),
        response(200, json_body={'items': [
            {'id': 501, 'modelRegionCode': 'NAWS', 'modelVersionCode': '11.0'}
        ]}),
    ])

    result = manager.get_pet_metadata_by_id(501)

    assert result['id'] == 501
    assert [call['params']['offset'] for call in client.calls] == [0, 500]


def test_exact_pet_metadata_uses_version_and_optional_region(
    make_reference_data_manager, response
):
    """Select a PET row only when every supplied qualifier matches."""
    rows = [
        {'id': 15, 'modelRegionCode': 'JPWS', 'modelVersionCode': '2.0'},
        {'id': 16, 'modelRegionCode': 'JPWS', 'modelVersionCode': '2.0'},
        {'id': 15, 'modelRegionCode': 'JPWS', 'modelVersionCode': '2.1'},
        {'id': 16, 'modelRegionCode': 'JPWS', 'modelVersionCode': '2.1'},
        {'id': 15, 'modelRegionCode': 'NAWS', 'modelVersionCode': '2.1'},
    ]
    manager, _ = make_reference_data_manager([
        response(200, json_body={'items': rows}),
        response(200, json_body={'items': rows}),
    ])

    region_result = manager.get_pet_metadata_exact(
        pet_id=15,
        model_version='2.1',
        model_region_code='JPWS',
    )
    version_result = manager.get_pet_metadata_exact(
        pet_id=16,
        model_version='2.1',
    )

    assert region_result == rows[2]
    assert version_result == rows[3]


@pytest.mark.parametrize('rows', [
    [],
    [
        {'id': 15, 'modelRegionCode': 'JPWS', 'modelVersionCode': '2.1'},
        {'id': 15, 'modelRegionCode': 'JPWS', 'modelVersionCode': '2.1'},
    ],
])
def test_exact_pet_metadata_rejects_zero_or_multiple_qualified_matches(
    rows, make_reference_data_manager, response
):
    """Reject zero or multiple matches after applying every qualifier."""
    manager, _ = make_reference_data_manager([
        response(200, json_body={'items': rows}),
    ])

    with pytest.raises(IRPAPIError):
        manager.get_pet_metadata_exact(
            pet_id=15,
            model_version='2.1',
            model_region_code='JPWS',
        )


def test_pet_metadata_by_id_remains_ambiguous_across_versions(
    make_reference_data_manager, response
):
    """Preserve strict PET-ID lookup behavior when an ID spans versions."""
    manager, _ = make_reference_data_manager([
        response(200, json_body={'items': [
            {'id': 15, 'modelRegionCode': 'JPWS', 'modelVersionCode': '2.0'},
            {'id': 15, 'modelRegionCode': 'JPWS', 'modelVersionCode': '2.1'},
        ]}),
    ])

    with pytest.raises(IRPAPIError, match="Multiple PET metadata rows"):
        manager.get_pet_metadata_by_id(15)
