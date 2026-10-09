"""
Tests for ``create_portfolio``'s name rules.

The server caps ``portfolioName`` at 40 characters and ``portfolioNumber`` at 20,
both boundaries confirmed against a live tenant. Neither field is truncated: a
shortened value can collide two distinct inputs into one identifier, which is
harder to notice than a rejected call. The trap pinned below is that
``portfolio_number`` defaults to ``portfolio_name``, so a name between 21 and 40
characters passes its own cap and then fails on the number's.
"""

import pytest

from irp_integration.exceptions import IRPValidationError

# Invented names. Nothing here may name a real EDM, portfolio or tenant: this
# file ships in the sdist, so a name used here is a name published to PyPI.
EDM_NAME = "example_edm"
SHORT_NAME = "example_edm - TX"          # 16 characters, so portfolio_number may default
LONG_NAME = "example_edm - Puerto Rico"  # 25 characters, over the 20-character number cap


def make_manager(make_portfolio_manager, response, existing=None):
    """Build a manager wired for one create_portfolio call against exposure 42."""
    return make_portfolio_manager(
        responses=[
            response(200, json_body=existing or []),
            response(201, headers={
                'location': '/platform/riskdata/v1/exposures/42/portfolios/9'
            }),
        ],
        edms=[{'exposureId': 42}],
    )


def test_creates_at_the_name_cap_and_defaults_the_number(make_portfolio_manager, response):
    manager, client, _ = make_manager(make_portfolio_manager, response)

    portfolio_id, body = manager.create_portfolio(
        edm_name=EDM_NAME,
        portfolio_name="x" * 40,
        portfolio_number="n" * 20,
    )

    assert portfolio_id == 9, "the ID comes from the location header"
    assert body['portfolioName'] == "x" * 40
    assert client.calls[-1]['method'] == 'POST'

    manager, _, _ = make_manager(make_portfolio_manager, response)
    _, body = manager.create_portfolio(edm_name=EDM_NAME, portfolio_name=SHORT_NAME)
    assert body['portfolioNumber'] == SHORT_NAME


def test_rejects_a_41_character_name_before_any_request(make_portfolio_manager, response):
    manager, client, edm_manager = make_manager(make_portfolio_manager, response)

    with pytest.raises(IRPValidationError, match="at most 40 characters, got 41"):
        manager.create_portfolio(
            edm_name=EDM_NAME,
            portfolio_name="x" * 41,
            portfolio_number="n" * 20,
        )

    assert client.calls == []
    assert edm_manager.filters == [], "the name check must precede the EDM lookup"


def test_a_name_over_the_number_cap_requires_an_explicit_number(make_portfolio_manager, response):
    manager, client, _ = make_manager(make_portfolio_manager, response)

    with pytest.raises(IRPValidationError) as caught:
        manager.create_portfolio(edm_name=EDM_NAME, portfolio_name=LONG_NAME)

    message = str(caught.value)
    assert "defaults to portfolio_name" in message
    assert "Pass portfolio_number explicitly" in message
    assert client.calls == []


def test_a_duplicate_name_raises_before_the_post(make_portfolio_manager, response):
    manager, client, _ = make_manager(
        make_portfolio_manager, response, existing=[{'portfolioId': 3}]
    )

    with pytest.raises(IRPValidationError, match="please use a unique name"):
        manager.create_portfolio(edm_name=EDM_NAME, portfolio_name=SHORT_NAME)

    assert [call['method'] for call in client.calls] == ['GET'], (
        "the collision check must run before the POST, not after it"
    )
