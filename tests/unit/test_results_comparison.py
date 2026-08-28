"""Route tests for the comparison page and the Compare modal fragments
(spec 013 T-01/T-05, FR-007/FR-012).

Strategy mirrors test_shell_routes.py: a minimal FastAPI app with the shell,
submissions, and edms routers mounted, request.state.user stamped by a
middleware, service reads monkeypatched. Real Jinja2 templates render.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.services.auth_service import CurrentUser

SUB_ID = "11111111-1111-1111-1111-111111111111"
EDM_ID = "22222222-2222-2222-2222-222222222222"
AN_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AN_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
AN_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
AN_D = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def _fake_user():
    return CurrentUser(
        user_id="test-user-id", email="test@example.com",
        display_name="Test User", session_id="sess-abc",
        role_codes=["analyst"], is_admin=False,
        must_change_password=False, entra_oid=None)


class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = _fake_user()
        return await call_next(request)


def _make_app():
    from app.auth.csrf import generate_csrf_token
    from app.config import settings
    from app.routers import edms, shell, submissions
    from app.services import analysis_service

    app = FastAPI()
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_env"] = settings.app_env
    templates.env.globals["password_auth_enabled"] = settings.password_auth_enabled
    templates.env.globals["oidc_auth_enabled"] = settings.oidc_auth_enabled
    templates.env.globals["generate_csrf_token"] = generate_csrf_token
    templates.env.globals["default_perspective"] = (
        analysis_service.DEFAULT_PERSPECTIVE)
    templates.env.globals["default_perspective_label"] = (
        analysis_service.DEFAULT_PERSPECTIVE_LABEL)
    app.state.templates = templates
    app.add_middleware(_InjectUser)
    app.include_router(shell.router)
    app.include_router(submissions.router)
    app.include_router(edms.router)
    return app


def _perspectives():
    return [{"code": "GR", "label": "Gross"},
            {"code": "RL", "label": "Pre-Cat Net"},
            {"code": "WX", "label": "Working Excess"},
            {"code": "QS", "label": "Quota Share"},
            {"code": "GU", "label": "Ground Up"}]


def _column(analysis_id, name, *, produced=("RL",), value=1_000_000.0,
            aal=4_000_000.0, std=14_000_000.0, run_currency="USD",
            engine="DLM · 23.0"):
    from app.services import analysis_service
    from app.services.analysis_service import PerspectiveResults, ResultsColumn

    labels = analysis_service.expanded_return_periods()
    results = []
    for p in _perspectives():
        if p["code"] in produced:
            rows = [{"rp": rp, "oep": value, "aep": value * 2,
                     "oep_display": "x", "aep_display": "x"} for rp in labels]
            results.append(PerspectiveResults(
                code=p["code"], label=p["label"], produced=True,
                aal=aal, std_dev=std, rows=rows))
        else:
            results.append(PerspectiveResults(
                code=p["code"], label=p["label"], produced=False))
    return ResultsColumn(id=analysis_id, name=name, currency=run_currency,
                         results_state="ready", results=results,
                         engine=engine, run_currency=run_currency)


def _pair(base, second):
    from app.services.analysis_service import ComparisonPair
    return ComparisonPair(base=base, second=second)


class TestComparisonPage:
    """GET /results/comparison — render only; the pair resolution has its own
    coverage in test_comparison_service.py."""

    def _client(self, monkeypatch, pair_list, drops=(), *, submission_name=None,
                edm_name=None):
        from app.services import analysis_service, edm_service, submission_service

        monkeypatch.setattr(analysis_service, "list_analysis_perspectives",
                            lambda: _perspectives())
        monkeypatch.setattr(
            analysis_service, "list_comparison_pairs",
            lambda *, pairs: (list(pair_list), list(drops)))
        monkeypatch.setattr(
            submission_service, "get_submission",
            lambda sid: (SimpleNamespace(id=SUB_ID, name=submission_name)
                         if submission_name and str(sid) == SUB_ID else None))
        monkeypatch.setattr(
            edm_service, "get_edm",
            lambda eid: (SimpleNamespace(id=EDM_ID, name=edm_name)
                         if edm_name and str(eid) == EDM_ID else None))
        return TestClient(_make_app())

    def test_one_pair_renders_three_columns_with_sub_lines(self, monkeypatch):
        base = _column(AN_A, "Alpha Analysis", value=1_000_000.0)
        second = _column(AN_B, "Beta Analysis", value=1_500_000.0,
                         aal=4_800_000.0, std=7_000_000.0,
                         engine="RL · 25.0")
        client = self._client(monkeypatch, [_pair(base, second)])

        resp = client.get(f"/results/comparison?pairs={AN_A}:{AN_B}")

        assert resp.status_code == 200
        # base first, second next, % Chg header closing the pair
        assert resp.text.index("Alpha Analysis") < resp.text.index("Beta Analysis")
        assert "% Chg" in resp.text
        # each side's header sub-line carries its run currency and engine
        assert "USD · DLM · 23.0" in resp.text
        assert "USD · RL · 25.0" in resp.text
        # percent change rendered server-side, signed, one decimal
        assert "+50.0%" in resp.text     # every return period: 1.0M → 1.5M
        assert "+20.0%" in resp.text     # AAL: 4.0M → 4.8M
        assert "-50.0%" in resp.text     # Std dev: 14.0M → 7.0M
        # loss cells ride the units sliver; the verbatim number is preserved
        assert 'data-unit-value="1000000.0"' in resp.text

    def test_unknown_perspective_and_ep_type_fall_back_to_defaults(
            self, monkeypatch):
        client = self._client(monkeypatch, [
            _pair(_column(AN_A, "Alpha Analysis"),
                  _column(AN_B, "Beta Analysis"))])

        resp = client.get(f"/results/comparison?pairs={AN_A}:{AN_B}"
                          "&perspective=BOGUS&ep_type=NOPE")

        assert resp.status_code == 200
        assert "Pre-Cat Net" in resp.text
        # OEP curve selected: OEP values render, AEP values do not
        assert 'data-unit-value="1000000.0"' in resp.text
        assert 'data-unit-value="2000000.0"' not in resp.text

    def test_breadcrumbs_and_tab_title_submission_entry(self, monkeypatch):
        client = self._client(
            monkeypatch,
            [_pair(_column(AN_A, "Alpha Analysis"),
                   _column(AN_B, "Beta Analysis"))],
            submission_name="Coastal Re HO 2026")

        resp = client.get(
            f"/results/comparison?pairs={AN_A}:{AN_B}&submission={SUB_ID}")

        assert f'href="/submissions/{SUB_ID}"' in resp.text
        assert "<title>Coastal Re HO 2026 · Risk Workbench</title>" in resp.text

    def test_breadcrumbs_and_tab_title_edm_entry(self, monkeypatch):
        client = self._client(
            monkeypatch,
            [_pair(_column(AN_A, "Alpha Analysis"),
                   _column(AN_B, "Beta Analysis"))],
            submission_name="Coastal Re HO 2026", edm_name="Coastal HO 2026")

        resp = client.get(f"/results/comparison?pairs={AN_A}:{AN_B}"
                          f"&submission={SUB_ID}&edm={EDM_ID}")

        sub_pos = resp.text.index(f'href="/submissions/{SUB_ID}"')
        edm_pos = resp.text.index(f'href="/edms/{EDM_ID}"')
        assert sub_pos < edm_pos
        assert "<title>Coastal HO 2026 · Risk Workbench</title>" in resp.text

    def test_no_surviving_pairs_reads_the_empty_state(self, monkeypatch):
        client = self._client(monkeypatch, [])

        resp = client.get("/results/comparison")

        assert resp.status_code == 200
        assert "No comparisons to display" in resp.text

    # ── User story 2 — several pairs, one set of controls (FR-012/FR-014) ──

    def test_multiple_pairs_share_one_return_period_column(self, monkeypatch):
        client = self._client(monkeypatch, [
            _pair(_column(AN_A, "Alpha Analysis"),
                  _column(AN_B, "Beta Analysis")),
            _pair(_column(AN_C, "Gamma Analysis"),
                  _column(AN_D, "Delta Analysis"))])

        resp = client.get(
            f"/results/comparison?pairs={AN_A}:{AN_B},{AN_C}:{AN_D}")

        assert resp.status_code == 200
        assert resp.text.count("Return period") == 1
        assert resp.text.count("% Chg") == 2
        for name in ("Alpha Analysis", "Beta Analysis",
                     "Gamma Analysis", "Delta Analysis"):
            assert name in resp.text
        # pairs are identified by their analysis names only
        assert "Pair 1" not in resp.text

    def test_perspective_and_ep_type_apply_to_every_pair(self, monkeypatch):
        produced = ("GR", "RL")
        client = self._client(monkeypatch, [
            _pair(_column(AN_A, "Alpha Analysis", produced=produced,
                          value=1_000_000.0),
                  _column(AN_B, "Beta Analysis", produced=produced,
                          value=3_000_000.0)),
            _pair(_column(AN_C, "Gamma Analysis", produced=produced,
                          value=5_000_000.0),
                  _column(AN_D, "Delta Analysis", produced=produced,
                          value=7_000_000.0))])

        resp = client.get(
            f"/results/comparison?pairs={AN_A}:{AN_B},{AN_C}:{AN_D}"
            "&perspective=GR&ep_type=AEP")

        assert resp.status_code == 200
        assert "Gross" in resp.text
        # the AEP curve renders for all four columns — every pair moved
        for aep in ("2000000.0", "6000000.0", "10000000.0", "14000000.0"):
            assert f'data-unit-value="{aep}"' in resp.text

    def test_toolbar_re_renders_comparison_view_carrying_pairs(
            self, monkeypatch):
        client = self._client(
            monkeypatch,
            [_pair(_column(AN_A, "Alpha Analysis"),
                   _column(AN_B, "Beta Analysis"))],
            submission_name="Coastal Re HO 2026")

        resp = client.get(
            f"/results/comparison?pairs={AN_A}:{AN_B}&submission={SUB_ID}")

        # perspective and EP type re-render #comparison-view, each carrying
        # the other's value and the pairs/entry params in the base URL
        assert 'hx-target="#comparison-view"' in resp.text
        assert 'hx-include="#res-ep"' in resp.text
        assert 'hx-include="#res-persp"' in resp.text
        assert "pairs=" in resp.text
        assert f"submission={SUB_ID}" in resp.text
        # units (default millions) and Copy table are the existing slivers
        assert "data-units-select" in resp.text
        assert '<option value="millions" selected>' in resp.text
        assert "data-copy-table" in resp.text

    def test_absent_perspective_shows_partner_numbers_and_em_dash(
            self, monkeypatch):
        base = _column(AN_A, "Alpha Analysis", produced=("RL",),
                       value=1_000_000.0)
        second = _column(AN_B, "Beta Analysis", produced=("GR",))
        client = self._client(monkeypatch, [_pair(base, second)])

        resp = client.get(f"/results/comparison?pairs={AN_A}:{AN_B}")

        assert resp.status_code == 200
        # the base's numbers still render
        assert 'data-unit-value="1000000.0"' in resp.text
        # the absent side reads absent — never an error
        assert "did not produce this perspective" in resp.text
        # % Chg is an em dash
        assert '<td class="chg"><span class="na">—</span></td>' in resp.text

    def test_percent_cells_carry_no_unit_value(self, monkeypatch):
        base = _column(AN_A, "Alpha Analysis", value=1_000_000.0)
        second = _column(AN_B, "Beta Analysis", value=1_500_000.0)
        client = self._client(monkeypatch, [_pair(base, second)])

        resp = client.get(f"/results/comparison?pairs={AN_A}:{AN_B}")

        # the percent cell has no data-unit-value, so the units sliver
        # never rescales it (T-06)
        assert '<td class="chg"><span title="0.5">+50.0%</span></td>' \
            in resp.text
        assert 'data-unit-value="0.5"' not in resp.text

    # ── User story 3 — drop notice and empty state (FR-015, SC-003, P-06) ──

    def test_missing_side_drop_notice_names_the_missing_analysis(
            self, monkeypatch):
        client = self._client(
            monkeypatch,
            [_pair(_column(AN_A, "Alpha Analysis"),
                   _column(AN_B, "Beta Analysis"))],
            drops=[{"kind": "missing", "ids": [AN_C]}])

        resp = client.get(
            f"/results/comparison?pairs={AN_A}:{AN_B},{AN_C}:{AN_A}")

        assert resp.status_code == 200
        assert "no longer exists" in resp.text
        assert AN_C in resp.text
        # surviving pairs render normally
        assert "Alpha Analysis" in resp.text
        assert "% Chg" in resp.text

    def test_currency_mismatch_drop_notice_names_both_currencies(
            self, monkeypatch):
        client = self._client(
            monkeypatch,
            [_pair(_column(AN_A, "Alpha Analysis"),
                   _column(AN_B, "Beta Analysis"))],
            drops=[{"kind": "currency", "currencies": ("USD", "EUR")}])

        resp = client.get(
            f"/results/comparison?pairs={AN_A}:{AN_B},{AN_C}:{AN_D}")

        assert resp.status_code == 200
        assert "USD" in resp.text and "EUR" in resp.text
        assert "never converted" in resp.text

    def test_generic_drop_notice_for_other_causes(self, monkeypatch):
        client = self._client(
            monkeypatch,
            [_pair(_column(AN_A, "Alpha Analysis"),
                   _column(AN_B, "Beta Analysis"))],
            drops=[{"kind": "other"}])

        resp = client.get(
            f"/results/comparison?pairs={AN_A}:{AN_B},{AN_A}:{AN_A}")

        assert resp.status_code == 200
        assert "A pair was dropped" in resp.text

    def test_all_pairs_dropped_renders_notice_and_empty_state(
            self, monkeypatch):
        client = self._client(monkeypatch, [],
                              drops=[{"kind": "missing", "ids": [AN_A]}])

        resp = client.get(f"/results/comparison?pairs={AN_A}:{AN_B}")

        assert resp.status_code == 200
        assert "no longer exists" in resp.text
        assert "No comparisons to display" in resp.text
        assert "Compare" in resp.text


class TestNoRiskModelerCallInRenderPath:
    """FR-016: the whole render reads stored extracts — a poisoned gateway
    proves no Risk Modeler call serves the page."""

    def test_render_with_gateway_unreachable(self, iteration2_db):
        from app.services import irp_gateway
        from db import execute_command

        class _Poison:
            def __getattr__(self, name):
                raise AssertionError(f"Risk Modeler call attempted: {name}")

        edm = str(uuid.uuid4())
        execute_command(
            "INSERT INTO irp_edm (id, name, status) VALUES (:id, 'E', 'ready')",
            {"id": edm}, connection="WORKBENCH")
        eleven = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000)
        extract = {
            "engine_type": "DLM", "engine_version": "23.0",
            "perspectives": {"RL": {
                "aal": 100.0, "std_dev": 50.0,
                "oep": {str(rp): float(rp) for rp in eleven},
                "aep": {str(rp): float(rp) for rp in eleven}}}}
        ids = []
        for name in ("A", "B"):
            analysis = str(uuid.uuid4())
            ids.append(analysis)
            execute_command(
                "INSERT INTO irp_analysis (id, edm_id, name, full_name, "
                "status_code, submitted_settings, loss_results, execution_id, "
                "execution_item_no) VALUES (:id, :edm, :n, :n, 'ready', "
                ":sub, :loss, :x, 0)",
                {"id": analysis, "edm": edm, "n": name,
                 "sub": json.dumps({"currency": {"code": "USD"}}),
                 "loss": json.dumps(extract), "x": str(uuid.uuid4())},
                connection="WORKBENCH")

        irp_gateway.configure(_Poison())
        try:
            resp = TestClient(_make_app()).get(
                f"/results/comparison?pairs={ids[0]}:{ids[1]}")
        finally:
            irp_gateway.reset()

        assert resp.status_code == 200
        assert "% Chg" in resp.text
        assert "+0.0%" in resp.text


class TestCompareModalRoutes:
    """The three §1 fragment routes share one handler and one template."""

    def _rows(self):
        from app.services.analysis_service import ComparableAnalysis
        return [
            ComparableAnalysis(id=AN_A, name="Own Analysis", rdm_name=None,
                               run_currency="USD", results_state="ready"),
            ComparableAnalysis(id=AN_B, name="Broker Analysis",
                               rdm_name="Acme RDM", run_currency="EUR",
                               results_state="ready"),
        ]

    def _client(self, monkeypatch, rows):
        from app.services import analysis_service

        calls = []

        def fake_list(*, submission_id=None, edm_id=None):
            calls.append({"submission_id": submission_id, "edm_id": edm_id})
            return rows

        monkeypatch.setattr(analysis_service, "list_comparable_analyses",
                            fake_list)
        return TestClient(_make_app()), calls

    def test_submission_scope(self, monkeypatch):
        client, calls = self._client(monkeypatch, self._rows())

        resp = client.get(f"/submissions/{SUB_ID}/analyses/compare")

        assert resp.status_code == 200
        assert calls == [{"submission_id": SUB_ID, "edm_id": None}]
        assert "Own Analysis" in resp.text
        assert 'data-currency="USD"' in resp.text
        # the cart builds the §2 URL from the entry point's ids
        assert f'data-submission="{SUB_ID}"' in resp.text
        assert "data-edm" not in resp.text

    def test_contextual_scope(self, monkeypatch):
        client, calls = self._client(monkeypatch, self._rows())

        resp = client.get(
            f"/submissions/{SUB_ID}/edms/{EDM_ID}/analyses/compare")

        assert resp.status_code == 200
        assert calls == [{"submission_id": SUB_ID, "edm_id": EDM_ID}]
        assert f'data-submission="{SUB_ID}"' in resp.text
        assert f'data-edm="{EDM_ID}"' in resp.text

    def test_plain_edm_scope(self, monkeypatch):
        client, calls = self._client(monkeypatch, self._rows())

        resp = client.get(f"/edms/{EDM_ID}/analyses/compare")

        assert resp.status_code == 200
        assert calls == [{"submission_id": None, "edm_id": EDM_ID}]
        assert f'data-edm="{EDM_ID}"' in resp.text

    def test_broker_rows_carry_their_rdm_name(self, monkeypatch):
        client, _ = self._client(monkeypatch, self._rows())

        resp = client.get(f"/submissions/{SUB_ID}/analyses/compare")

        assert "Broker Analysis" in resp.text
        assert "Acme RDM" in resp.text

    def test_gone_scope_renders_the_notice(self, monkeypatch):
        client, _ = self._client(monkeypatch, None)

        resp = client.get(f"/submissions/{SUB_ID}/analyses/compare")

        assert resp.status_code == 200
        assert "no longer" in resp.text

    # ── User story 3 — non-ready and currency-less rows (FR-002, P-05) ──

    def test_pending_and_failed_rows_disabled_with_state_named(
            self, monkeypatch):
        from app.services.analysis_service import ComparableAnalysis
        rows = [
            ComparableAnalysis(id=AN_A, name="Ready One", rdm_name=None,
                               run_currency="USD", results_state="ready"),
            ComparableAnalysis(id=AN_B, name="Still Running", rdm_name=None,
                               run_currency="USD", results_state="pending"),
            ComparableAnalysis(id=AN_C, name="Broke Down", rdm_name=None,
                               run_currency="USD", results_state="failed"),
        ]
        client, _ = self._client(monkeypatch, rows)

        resp = client.get(f"/submissions/{SUB_ID}/analyses/compare")

        assert resp.status_code == 200
        assert "retrieving…" in resp.text
        assert "retrieval failed" in resp.text
        # the two non-ready rows are listed but never tickable
        assert resp.text.count("cmp-row--disabled") == 2
        assert resp.text.count('<input type="checkbox" disabled>') == 2

    def test_unrecorded_currency_row_tickable_without_data_currency(
            self, monkeypatch):
        from app.services.analysis_service import ComparableAnalysis
        rows = [ComparableAnalysis(id=AN_A, name="No Currency", rdm_name=None,
                                   run_currency=None, results_state="ready")]
        client, _ = self._client(monkeypatch, rows)

        resp = client.get(f"/submissions/{SUB_ID}/analyses/compare")

        assert resp.status_code == 200
        assert "data-currency" not in resp.text
        # still tickable — the pair-add refusal, not the list, names the gap
        assert 'disabled' not in resp.text.split('cmp-list')[1].split(
            'cmp-cart')[0]
        assert '@change="toggle($event)"' in resp.text


class TestHandTypedUrls:
    """P-06/T-01 route-level proof over the real service: garbage input is a
    render, never a 500."""

    def test_garbage_pairs_render_the_empty_state(self, iteration2_db):
        resp = TestClient(_make_app()).get(
            "/results/comparison?pairs=garbage:junk,notauuid")

        assert resp.status_code == 200
        assert "A pair was dropped" in resp.text
        assert "No comparisons to display" in resp.text
