"""
Tests for the treaties-by-analysis read.

Pinned here: ``search_analysis_treaties`` sends the analysis ID in the path and
``limit``/``offset`` as query parameters, the paginated companion drives
``paginate_search`` by record offset, and a failed request is reported as an
``IRPAPIError`` naming the analysis.
"""

import pytest

from irp_integration.exceptions import IRPAPIError


def treaties(count, start=0):
    """Build a page of treaty dicts, so pages differ from one another."""
    return [{'treatyId': start + n} for n in range(count)]


def test_search_sends_the_analysis_id_in_the_path_with_limit_and_offset(
    make_analysis_manager, response
):
    manager, client, _ = make_analysis_manager([response(200, json_body=treaties(3))])

    results = manager.search_analysis_treaties(42)

    assert len(results) == 3
    assert client.calls[0]['method'] == 'GET'
    assert client.calls[0]['path'] == '/platform/riskdata/v1/analyses/42/treaties'
    assert client.calls[0]['params'] == {'limit': 100, 'offset': 0}


def test_paginated_read_walks_pages_by_record_offset(make_analysis_manager, response):
    manager, client, _ = make_analysis_manager([
        response(200, json_body=treaties(100, 0)),
        response(200, json_body=treaties(100, 100)),
        response(200, json_body=treaties(17, 200)),
    ])

    results = manager.search_analysis_treaties_paginated(42)

    assert len(results) == 217
    assert [call['params']['offset'] for call in client.calls] == [0, 100, 200]


def test_a_failed_request_names_the_analysis(make_analysis_manager, response):
    manager, _, _ = make_analysis_manager([RuntimeError("connection reset")])

    with pytest.raises(IRPAPIError, match="analysis 42"):
        manager.search_analysis_treaties(42)
