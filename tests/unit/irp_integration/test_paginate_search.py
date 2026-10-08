"""
Tests for ``utils.paginate_search``.

Two things are pinned. First, ``offset`` counts records, not pages: the Risk Data
API documents this both ways, and an earlier version of the helper guessed page
semantics, which truncated a read to its first 100 records without any error.
Second, the helper either returns a result it can show is complete or raises
``IRPAPIError`` — it never returns a short list as though the walk had finished,
because callers create sub-portfolios out of these results and a missing account
looks like success.
"""

from typing import Any, Dict, List

import pytest

from irp_integration.exceptions import IRPAPIError
from irp_integration.utils import paginate_search


class Recorder:
    """Returns the configured pages in order and records the offsets asked for."""

    def __init__(self, pages: List[List[Any]]) -> None:
        self.pages = pages
        self.offsets: List[int] = []

    def __call__(self, limit: int, offset: int) -> List[Any]:
        self.offsets.append(offset)
        return self.pages.pop(0) if self.pages else []


def page(size: int, start: int = 0) -> List[Dict[str, int]]:
    """Build a page of distinct records, so pages differ from one another."""
    return [{'accountId': start + n} for n in range(size)]


def test_offset_counts_records_and_the_walk_ends_on_a_short_page():
    fetch = Recorder([page(10, 0), page(10, 10), page(4, 20)])

    results = paginate_search(fetch, "account search", limit=10)

    assert results == page(10, 0) + page(10, 10) + page(4, 20)
    assert fetch.offsets == [0, 10, 20], (
        "page N must start at N * limit; page-number offsets would be 0, 1, 2"
    )


def test_repeated_page_raises_rather_than_returning_a_partial_result():
    repeated = page(10, 0)
    fetch = Recorder([repeated, list(repeated)])

    with pytest.raises(IRPAPIError) as caught:
        paginate_search(fetch, "account search", limit=10)

    message = str(caught.value)
    assert "account search" in message
    assert "offset 10" in message
    assert "10 records" in message, "the message should say how much was read"


def test_page_ceiling_raises_and_stops_at_max_pages():
    fetch = Recorder([page(10, n * 10) for n in range(50)])

    with pytest.raises(IRPAPIError, match="3-page ceiling"):
        paginate_search(fetch, "account search", limit=10, max_pages=3)

    assert fetch.offsets == [0, 10, 20], "max_pages caps total requests, not extra ones"


def test_oversized_first_page_is_treated_as_complete():
    fetch = Recorder([page(150)])

    results = paginate_search(fetch, "portfolio account search", limit=100)

    assert results == page(150), (
        "more rows than the requested limit means the operation ignored "
        "pagination, so the first response was already the whole result"
    )
    assert fetch.offsets == [0]
