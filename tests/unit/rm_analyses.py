"""Seed an analysis on the fake IRP gateway so ``resolve_app_analysis_id`` and
``get_analysis_metadata`` find it (#101 import by Risk Modeler id)."""

from __future__ import annotations

APP_ID, PLATFORM_ID = "35774", "90001"
NAME = "CRE_WS_JP_COM_HD_JPWS_Stochastic"


def seed_rm_analysis(fake_irp, *, app_id=APP_ID, platform_id=PLATFORM_ID,
                     name=NAME, is_group=False, engine="HD",
                     currency="JPY") -> None:
    fake_irp.add_analysis(
        source_rdm_name="RDM", exposure_name="EDM", analysis_id=platform_id,
        name=name, app_analysis_id=app_id, is_group=is_group,
        exposure_resource_id="5",
        exposure_resource_type=("GROUP" if is_group else "PORTFOLIO"),
        metadata={"appAnalysisId": int(app_id), "analysisName": name,
                  "engineType": engine, "engineVersion": "23.0",
                  "currencyCode": currency})
