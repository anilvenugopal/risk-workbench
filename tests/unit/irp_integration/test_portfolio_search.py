"""
Tests for the portfolio selection reads.

Pinned here: ``search_accounts_by_portfolio`` omits ``limit``/``offset``
entirely when they are None (the Risk Data API does not declare them for that
operation) and sends ``offset=0`` when explicitly asked; ``search_accounts``
sends ``allowDeepFilters`` as the lowercase string; and the ``_paginated``
variants really do drive ``paginate_search`` by record offset. The other reads
(policies, locations) and their paginated companions are the same two lines of
parameter assembly and the same ``paginate_search`` call, so they are not
retested here.

A failed request keeps its HTTP status and its cause through the manager's
``IRPAPIError`` wrapper.
"""

import pytest

from irp_integration.exceptions import IRPAPIError


def accounts(count, start=0):
    """Build a page of account dicts, so pages differ from one another."""
    return [{'accountId': start + n} for n in range(count)]


def test_limit_and_offset_are_omitted_unless_asked_for(make_portfolio_manager, response):
    manager, client, _ = make_portfolio_manager([
        response(200, json_body=accounts(3)),
        response(200, json_body=accounts(3)),
    ])

    manager.search_accounts_by_portfolio(42, 7)
    manager.search_accounts_by_portfolio(42, 7, offset=0)

    assert client.calls[0]['path'] == '/platform/riskdata/v1/exposures/42/portfolios/7/accounts'
    assert client.calls[0]['params'] == {}, (
        "the API does not declare limit/offset for getPortfolioAccounts, so the "
        "no-arg call must send neither"
    )
    assert client.calls[1]['params'] == {'offset': 0}, (
        "offset=0 is a request to send it, not the same as leaving it out"
    )


def test_search_accounts_sends_deep_filters_as_a_lowercase_string(make_portfolio_manager, response):
    manager, client, _ = make_portfolio_manager([response(200, json_body=accounts(2))])

    manager.search_accounts(42)

    assert client.calls[0]['params']['allowDeepFilters'] == 'false', (
        "requests renders a Python bool as 'False', which the API rejects"
    )


def test_paginated_read_walks_pages_by_record_offset(make_portfolio_manager, response):
    manager, client, _ = make_portfolio_manager([
        response(200, json_body=accounts(100, 0)),
        response(200, json_body=accounts(100, 100)),
        response(200, json_body=accounts(84, 200)),
    ])

    results = manager.search_accounts_by_portfolio_paginated(42, 7)

    assert len(results) == 284
    assert [call['params']['offset'] for call in client.calls] == [0, 100, 200]


def test_a_failed_read_keeps_the_status_and_cause(make_portfolio_manager):
    rejected = IRPAPIError("HTTP request failed (status 404)", status_code=404)
    manager, _, _ = make_portfolio_manager([rejected])

    with pytest.raises(IRPAPIError) as excinfo:
        manager.get_portfolio_by_id(42, 7)

    assert excinfo.value.status_code == 404
    assert excinfo.value.__cause__ is rejected
    assert "exposure ID '42' and portfolio ID '7'" in str(excinfo.value)
