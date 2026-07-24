"""Unit tests for ``TreatyRow.attribute_items`` display shaping (Treaties
section polish, 2026-07-24).

The expanded attribute grid renders ``attribute_items()``: RM's camelCase keys
humanized and values collapsed for DISPLAY — a sub-object to its human label
(cedant ``{cedantId, cedantName}`` → the NAME, never the "id, name" values
join; currency ``{code}`` → the code), a list of sub-objects to a comma-joined
label list (lobs → ``Lend, Prop``, never raw JSON), and RM's internal ``uri``
dropped entirely. Scalars pass through untouched — the template still owns
number/boolean formatting. The Excel export stays verbatim (full fidelity).
"""

from __future__ import annotations

from app.services.treaty_service import TreatyRow

# The live RM lob sub-object shape (user-reported 2026-07-24).
LOBS = [
    {"lobId": 5955, "lobName": "Lend",
     "uri": "/platform/riskdata/v1/exposures/5331056/treaties/2/lobs/5955"},
    {"lobId": 8837, "lobName": "Prop",
     "uri": "/platform/riskdata/v1/exposures/5331056/treaties/2/lobs/8837"},
]


def _items(attrs: dict) -> dict:
    row = TreatyRow(id="t1", edm_id="e1", name="Cat XoL", irp_id="1042",
                    attributes=attrs, as_of="2026-07-24")
    return dict(row.attribute_items())


def test_uri_attribute_is_dropped_from_the_grid():
    items = _items({"treatyType": "CATA",
                    "uri": "/platform/riskdata/v1/exposures/1/treaties/2"})
    assert "Treaty Type" in items
    assert "Uri" not in items


def test_cedant_object_shows_the_name_only():
    items = _items({"cedant": {"cedantId": "ASST", "cedantName": "Asset Re"}})
    assert items["Cedant"] == "Asset Re"


def test_lobs_list_shows_lob_names_not_raw_json():
    assert _items({"lobs": LOBS})["Lobs"] == "Lend, Prop"


def test_currency_object_still_collapses_to_its_code():
    assert _items({"currency": {"code": "USD"}})["Currency"] == "USD"


def test_scalars_pass_through_untouched():
    items = _items({"attachmentPoint": 25000000.0, "perRisk": True,
                    "treatyNumber": "TR-9"})
    assert items["Attachment Point"] == 25000000.0
    assert items["Per Risk"] is True
    assert items["Treaty Number"] == "TR-9"


def test_empty_list_and_unlabeled_object_degrade_gracefully():
    items = _items({"lobs": [], "odd": {"a": 1, "b": 2}})
    assert items["Lobs"] is None        # renders the em-dash, never []
    assert items["Odd"] == "1, 2"       # no label/code → scalar-values join
