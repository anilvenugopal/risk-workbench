"""
Tests for the two portfolio account writes.

``add_filtered_accounts`` (PUT ``manageFilteredAccounts``) and
``manage_portfolio_accounts`` (PATCH ``managePortfolioAccounts``) are the "add"
step of a sub-portfolio breakout. Pinned here: the exact request body, method and
path; that a non-200 raises instead of being polled as a workflow; and that an
empty selection is refused before a request goes out, because an empty filter
with ``selectAll`` would otherwise add every account in the EDM.
"""

import pytest

from irp_integration.exceptions import IRPAPIError, IRPValidationError


class TestAddFilteredAccounts:
    def test_sends_the_documented_body(self, make_portfolio_manager, response):
        manager, client, _ = make_portfolio_manager([response(200, has_body=False)])

        result = manager.add_filtered_accounts(42, 7, marked_accounts=[101, 102])

        assert client.calls == [{
            'method': 'PUT',
            'path': '/platform/riskdata/v1/exposures/42/portfolios/7/filtered-accounts',
            'params': None,
            'json': {
                'selectAll': False,
                'queryFilter': '',
                'markedAccounts': [101, 102],
                'manageExistingAccounts': False,
            },
        }]
        assert result == {}, "the operation declares no response body"

    def test_empty_selection_raises_before_any_request(self, make_portfolio_manager):
        manager, client, _ = make_portfolio_manager([])

        with pytest.raises(IRPValidationError, match="No accounts selected"):
            manager.add_filtered_accounts(42, 7)

        assert client.calls == [], "an empty selection must cost no request"

    def test_manage_existing_accounts_with_a_selection_is_refused(self, make_portfolio_manager):
        manager, client, _ = make_portfolio_manager([])

        with pytest.raises(IRPValidationError, match="would add nothing"):
            manager.add_filtered_accounts(
                42, 7, marked_accounts=[101], manage_existing_accounts=True
            )

        assert client.calls == [], (
            "the API answers 200 and adds nothing, so this must not be sent"
        )

    def test_select_all_sends_the_flag(self, make_portfolio_manager, response):
        manager, client, _ = make_portfolio_manager([response(200, has_body=False)])

        manager.add_filtered_accounts(42, 7, select_all=True)

        assert client.calls[0]['json'] == {
            'selectAll': True,
            'queryFilter': '',
            'markedAccounts': [],
            'manageExistingAccounts': False,
        }

    def test_202_raises_rather_than_being_polled(self, make_portfolio_manager, response):
        manager, _, _ = make_portfolio_manager([
            response(202, headers={'location': 'https://api.example.invalid/workflows/9'})
        ])

        with pytest.raises(IRPAPIError) as caught:
            manager.add_filtered_accounts(42, 7, marked_accounts=[101])

        message = str(caught.value)
        assert "Unexpected status 202" in message
        assert "workflows/9" in message, "the location header belongs in the message"


class TestManagePortfolioAccounts:
    def test_sends_both_lists_even_when_one_is_empty(self, make_portfolio_manager, response):
        counts = {
            'addAccounts': {'completed': 2, 'total': 2},
            'removeAccounts': {'completed': 0, 'total': 0},
        }
        manager, client, _ = make_portfolio_manager([response(200, json_body=counts)])

        result = manager.manage_portfolio_accounts(42, 7, accounts_to_add=[101, 102])

        assert client.calls == [{
            'method': 'PATCH',
            'path': '/platform/riskdata/v1/exposures/42/portfolios/7/accounts',
            'params': None,
            'json': {'accountsToAdd': [101, 102], 'accountsToRemove': []},
        }]
        assert result == counts

    def test_no_accounts_raises_before_any_request(self, make_portfolio_manager):
        manager, client, _ = make_portfolio_manager([])

        with pytest.raises(IRPValidationError, match="No accounts specified"):
            manager.manage_portfolio_accounts(42, 7)

        assert client.calls == []

    def test_201_raises_rather_than_being_polled(self, make_portfolio_manager, response):
        manager, _, _ = make_portfolio_manager([response(201)])

        with pytest.raises(IRPAPIError, match="Unexpected status 201"):
            manager.manage_portfolio_accounts(42, 7, accounts_to_add=[101])
