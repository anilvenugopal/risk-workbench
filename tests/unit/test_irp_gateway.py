"""Unit tests for ``_RealGateway.get_analysis_metadata`` — the group marker.

Live payload confirmation (2026-07-24, first real sync against the RM tenant):
``GET /analyses/{analysisId}`` carries a first-class ``isGroup`` boolean, and a
plain analysis comes back with ``groupType='ANLS'`` — NOT a ``'GROUP'`` literal
— so the defensive literal-equality derivation alone would misread a real group
whose marker fields never spell "GROUP". ``isGroup`` is now the authoritative
marker; the literal spellings stay as fallback for payloads that omit it.
``exposureResourceId``/``exposureResourceType`` are confirmed RESPONSE
properties (IRP_INTEGRATION_FOLLOWUPS.md §8 resolved).

``_RealGateway`` is constructed directly with a stub client injected into
``_irp`` — no wheel import, no env, no HTTP.
"""

from __future__ import annotations

from app.services import irp_gateway


class _StubClient:
    class _Analysis:
        def __init__(self, payload):
            self._payload = payload

        def get_analysis_by_id(self, analysis_id):
            return self._payload

    def __init__(self, payload):
        self.analysis = self._Analysis(payload)


def _meta(payload) -> irp_gateway.AnalysisMetadata:
    gw = irp_gateway._RealGateway()
    gw._irp = _StubClient(payload)
    return gw.get_analysis_metadata(analysis_id=1)


def test_first_class_isgroup_boolean_is_authoritative():
    m = _meta({"isGroup": True, "groupType": "GRP"})
    assert m.is_group is True


def test_plain_live_analysis_is_not_group_and_pointer_promotable():
    # The confirmed live shape: isGroup=False, groupType='ANLS', pointer present.
    m = _meta({"isGroup": False, "groupType": "ANLS",
               "exposureResourceId": 3, "exposureResourceType": "PORTFOLIO"})
    assert m.is_group is False
    assert m.exposure_resource_id == "3"
    assert m.exposure_resource_type == "PORTFOLIO"


def test_group_literal_fallback_when_isgroup_absent():
    # Older/other payload shapes without isGroup: the defensive literals hold.
    assert _meta({"analysisType": "GROUP"}).is_group is True
    assert _meta({"analysisType": "EP"}).is_group is False
