"""Route tests for the loss results export pages and fragments (spec 014 T030,
T038, T041, T061): the Export link, the form and its selection fragment, the
POST, the exports table and its filters, and Retry.

Harness: TestClient over the real submissions router against the SQLite
WORKBENCH and LOSS mirrors (the test_submission_routes.py pattern)."""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from app.services import rwb_job_service
from db import execute, execute_command, execute_one
from tests.unit.export_client import csrf as _csrf
from tests.unit.export_client import make_client, make_deal, make_export
from tests.unit.export_client import post_export as _post
from tests.unit.export_rows import (
    manifest_row,
    rwb_jobs,
    seed_analysis,
    seed_export_job,
    seed_manifest,
    seed_submission,
)


@pytest.fixture()
def client(iteration2_db, loss_db) -> TestClient:
    return make_client(iteration2_db, loss_db)


@pytest.fixture()
def deal(client):
    return make_deal(client)


@pytest.fixture()
def export(client, deal):
    return make_export(client, deal)


def _mid(analysis_id: str, export_id: str | None = None):
    """Rows, Retry, and Close are keyed by manifest row (spec 016 T-06); every
    export under test has one row per analysis, so the analysis names it."""
    clause = " AND export_id = :e" if export_id else ""
    params = {"a": analysis_id} | ({"e": export_id} if export_id else {})
    row = execute_one("SELECT manifest_id FROM stage.rwb_loss_result_manifest "
                      f"WHERE irp_analysis_id = :a{clause}", params, connection="LOSS")
    return row["manifest_id"] if row else 999999


# ── Export link ──────────────────────────────────────────────────────────────

def test_export_link_on_the_submission_results_section_only(client, deal):
    page = client.get(f"/submissions/{deal['submission_id']}/analyses")
    assert page.status_code == 200
    assert f'href="/submissions/{deal["submission_id"]}/exports/new"' in page.text
    assert ">Export</a>" in page.text

    edm_scoped = client.templates.env.get_template(
        "partials/analyses_merged_section.html").render(
            analyses=[], groups=[], edm=SimpleNamespace(id="edm-1"),
            analyses_table_url="/edms/edm-1/analyses")
    assert "/exports/new" not in edm_scoped


def test_exports_section_has_no_export_link(client, deal):
    section = client.get(f"/submissions/{deal['submission_id']}/exports")
    assert section.status_code == 200
    assert "/exports/new" not in section.text


def test_form_ticks_the_analyses_carried_from_the_link(client, deal):
    page = client.get(f"/submissions/{deal['submission_id']}/exports/new",
                      params={"analysis_ids": [deal["a"], str(uuid.uuid4())]})
    assert page.status_code == 200
    assert re.search(rf'value="{deal["a"]}"[^>]*checked', page.text)
    assert not re.search(rf'value="{deal["b"]}"[^>]*checked', page.text)
    assert "Selected analyses (1)" in page.text
    assert 'value="GU"' in page.text


# ── form and fragment ────────────────────────────────────────────────────────

def test_form_renders_defaults_rows_and_disabled_reason(client, deal):
    page = client.get(f"/submissions/{deal['submission_id']}/exports/new")
    assert page.status_code == 200
    # Two contracts on the deal: nothing is picked until the analyst chooses
    # one (spec 017 P-06), so CRM ID and inception open blank.
    assert 'name="treaty_incept" required x-ref="incept"\n               value=""' in page.text
    assert 'name="crm_id" maxlength="30" x-ref="crm"\n               value=""' in page.text
    assert '<option value="">Choose a contract…</option>' in page.text
    assert 'data-crm-id="CRM-1" data-inception="2026-04-01"\n                  >' in page.text
    assert 'data-crm-id="CRM-2" data-inception="2026-04-01"\n                  >' in page.text
    assert '<option value="1" >1 - Example Re</option>' in page.text
    assert '<option value="2" >2 - Retired</option>' in page.text
    assert 'name="model_version" required' in page.text
    assert '<option value="25.0" selected>25.0</option>' in page.text
    assert '<option value="23.0" >23.0</option>' in page.text
    assert "Cannot be exported: Risk Modeler application analysis ID &#39;A-388&#39;" in page.text
    assert page.text.count('name="analysis_ids"') == 3
    assert 'name="perspective" required\n            disabled' in page.text
    assert "Tick at least one analysis" in page.text


def test_form_preselects_the_only_contract_and_the_submission_client(client, deal):
    sid = seed_submission(client.db.user_a, name="One contract", inception="2027-01-01",
                          crm_ids=("T-100",))
    execute_command("UPDATE submission SET client_id = 2, data_vintage = '2026-06-30' "
                    "WHERE id = :s", {"s": sid}, connection="WORKBENCH")
    page = client.get(f"/submissions/{sid}/exports/new")
    assert page.status_code == 200
    assert 'data-crm-id="T-100" data-inception="2027-01-01"\n                  selected>' in page.text
    assert 'name="treaty_incept" required x-ref="incept"\n               value="2027-01-01"' in page.text
    assert 'name="crm_id" maxlength="30" x-ref="crm"\n               value="T-100"' in page.text
    assert 'name="data_vintage" required\n               value="2026-06-30"' in page.text
    assert '<option value="2" selected>2 - Retired</option>' in page.text


def test_form_without_contracts_disables_the_contract_select(client, deal):
    sid = seed_submission(client.db.user_a, name="No contract", crm_ids=())
    page = client.get(f"/submissions/{sid}/exports/new")
    assert 'aria-label="Contract"\n                @change="pick($event.target.selectedOptions[0])" disabled>' in page.text
    assert "No contracts on this submission" in page.text


def test_form_for_a_gone_submission_shows_the_notice(client, deal):
    page = client.get(f"/submissions/{uuid.uuid4()}/exports/new")
    assert page.status_code == 404
    assert "This submission no longer exists." in page.text


def test_fragment_intersection_and_data_name_fields(client, deal):
    frag = client.get(f"/submissions/{deal['submission_id']}/exports/new/fields",
                      params=[("analysis_ids", deal["a"]), ("analysis_ids", deal["b"]),
                              ("perspective", "RL")])
    assert frag.status_code == 200
    assert re.findall(r'<option value="(\w+)"', frag.text) == ["GR", "RL"]
    assert '<option value="RL" selected>' in frag.text
    assert f'name="data_name[{deal["a"]}]"' in frag.text
    assert f'name="data_name[{deal["b"]}]"' in frag.text
    assert frag.text.count("AAL 100") == 2
    assert "hx-swap-oob" not in frag.text


def test_fragment_keeps_a_data_name_typed_before_the_next_tick(client, deal):
    frag = client.get(f"/submissions/{deal['submission_id']}/exports/new/fields",
                      params=[("analysis_ids", deal["a"]), ("analysis_ids", deal["b"]),
                              ("perspective", "RL"),
                              (f"data_name[{deal['a']}]", "AmFam HU GR 2026")])
    assert 'value="AmFam HU GR 2026"' in frag.text
    kept = frag.text.split(f'name="data_name[{deal["a"]}]"')[1].split(">")[0]
    assert 'value="AmFam HU GR 2026"' in kept


def test_fragment_empty_intersection_message(client, deal):
    only_gu = seed_analysis(edm_id=deal["edm_id"], name="GU only", irp_app_analysis_id="7",
                            perspectives=("GU",))
    frag = client.get(f"/submissions/{deal['submission_id']}/exports/new/fields",
                      params=[("analysis_ids", only_gu), ("analysis_ids", deal["b"])])
    assert "The selected analyses share no exportable perspective" in frag.text
    assert "No shared perspective" in frag.text


def test_fragment_warns_about_an_analysis_exported_from_another_submission(client, deal):
    other = str(uuid.uuid4())
    seed_manifest(submission_id=other, irp_app_analysis_id=41958, perspective_code="GR",
                  requested_at="2026-08-01 08:00:00", requested_by_email="d.owens@example.com")
    row = seed_manifest(submission_id=other, irp_app_analysis_id=41958, perspective_code="GR",
                        requested_at="2026-09-01 08:00:00",
                        requested_by_email="r.patel@example.com", load_status="loaded",
                        stage_status="staged", data_id=7)
    frag = client.get(f"/submissions/{deal['submission_id']}/exports/new/fields",
                      params=[("analysis_ids", deal["a"]), ("analysis_ids", deal["b"]),
                              ("perspective", "GR")])
    assert f'href="/submissions/{other}#submission-exports"' in frag.text
    assert row["export_id"] not in frag.text
    assert "r.patel@example.com" in frag.text and "loaded" in frag.text
    assert "d.owens@example.com" not in frag.text
    assert "(2 times)" in frag.text
    assert "Exporting again creates a new data set." in frag.text
    assert f'name="data_name[{deal["a"]}]"' in frag.text
    assert f'name="data_name[{deal["b"]}]"' in frag.text


def test_fragment_leaves_every_row_and_the_perspective_select_usable(client, deal):
    """An already-exported analysis must stay unticking-able: the row keeps its
    live checkbox and the select keeps the selection's shared perspectives, so
    the analyst is never left with a form that has no working control."""
    seed_manifest(submission_id=str(uuid.uuid4()), irp_app_analysis_id=41958,
                  perspective_code="GR")
    frag = client.get(f"/submissions/{deal['submission_id']}/exports/new/fields",
                      params=[("analysis_ids", deal["a"]), ("perspective", "GR")])
    assert "hx-swap-oob" not in frag.text
    assert "disabled" not in frag.text
    assert re.findall(r'<option value="(\w+)"', frag.text) == ["GU", "GR", "RL"]
    assert f"untick('{deal['a']}')" in frag.text


# ── POST ─────────────────────────────────────────────────────────────────────

def test_post_writes_manifest_rows_and_redirects(client, deal):
    response = _post(client, deal, [deal["a"], deal["b"]],
                     **{f"data_name[{deal['a']}]": "Named A", "crm_id": "CRM-9"})
    assert response.status_code == 303
    assert response.headers["location"] == f"/submissions/{deal['submission_id']}#submission-exports"
    rows = execute("SELECT * FROM stage.rwb_loss_result_manifest ORDER BY analysis_name", {},
                   connection="LOSS")
    assert [(r["analysis_name"], r["data_name"], r["crm_id"]) for r in rows] == [
        ("A", "Named A", "CRM-9"), ("B", None, "CRM-9")]
    assert len({r["export_id"] for r in rows}) == 1
    assert len(rwb_jobs("submit_results_export")) == 1
    contracts = execute("SELECT inception_date FROM contract WHERE submission_id = :s",
                        {"s": deal["submission_id"]}, connection="WORKBENCH")
    assert {c["inception_date"] for c in contracts} == {"2026-04-01"}


def test_form_posts_plainly_so_a_422_rerender_is_shown(client, deal):
    page = client.get(f"/submissions/{deal['submission_id']}/exports/new")
    tag = re.search(r'<form[^>]*id="export-form"[^>]*>', page.text).group(0)
    assert "hx-post" not in tag and 'method="post"' in tag
    assert 'x-data="analysisPicks()"' in tag
    assert ':disabled="!count || !treatiesOk"' in page.text


def test_post_at_ty_writes_one_row_per_ticked_treaty(client, deal):
    c = seed_analysis(edm_id=deal["edm_id"], name="C", full_name="C long", irp_id="41960",
                      irp_app_analysis_id="41960", perspectives=("GU", "GR"),
                      treaties=(("33833", "PR1", "PR1"), ("33832", "PR2", "Layer two")))

    response = _post(client, deal, [c], perspective="TY", **{
        f"treaty[{c}]": ["PR1", "PR2"],
        f"treaty_data_name[{c}][PR1]": "AmFam HU 3x2 2026"})

    assert response.status_code == 303
    rows = execute("SELECT treaty_number, treaty_name, data_name "
                   "FROM stage.rwb_loss_result_manifest ORDER BY manifest_id", {},
                   connection="LOSS")
    assert [(r["treaty_number"], r["treaty_name"], r["data_name"]) for r in rows] == [
        ("PR1", "PR1", "AmFam HU 3x2 2026"), ("PR2", "Layer two", "C PR2")]


def test_post_at_ty_without_a_tick_rerenders_with_the_ticks_it_had(client, deal):
    c = seed_analysis(edm_id=deal["edm_id"], name="C", full_name="C long", irp_id="41960",
                      irp_app_analysis_id="41960", perspectives=("GU", "GR"),
                      treaties=(("33833", "PR1", "PR1"), ("33832", "PR2", "Layer two")))
    other = seed_analysis(edm_id=deal["edm_id"], name="D", full_name="D long", irp_id="41961",
                          irp_app_analysis_id="41961", perspectives=("GU", "GR"),
                          treaties=(("33833", "PR1", "PR1"),))

    response = _post(client, deal, [c, other], perspective="TY",
                     **{f"treaty[{c}]": "PR2"})

    assert response.status_code == 422
    assert "Tick at least one treaty for D long." in response.text
    kept = response.text.split(f'name="treaty[{c}]" value="PR2"')[1].split(">")[0]
    assert "checked" in kept
    assert execute("SELECT 1 FROM stage.rwb_loss_result_manifest", {}, connection="LOSS") == []


def test_post_over_an_earlier_export_creates_a_second_one(client, deal):
    seed_manifest(submission_id=str(uuid.uuid4()), irp_app_analysis_id=41959,
                  perspective_code="GR", requested_by_email="r.patel@example.com")
    response = _post(client, deal, [deal["a"], deal["b"]])
    assert response.status_code == 303
    assert len(execute("SELECT 1 FROM stage.rwb_loss_result_manifest", {},
                       connection="LOSS")) == 3


def test_post_validation_message_keeps_the_analysts_values(client, deal):
    response = _post(client, deal, [deal["a"]], perspective="RP", crm_id="CRM-typed")
    assert response.status_code == 422
    assert "A long has no RP results" in response.text
    assert 'value="CRM-typed"' in response.text


def test_post_rejects_a_bad_csrf_token(client, deal):
    response = client.post(f"/submissions/{deal['submission_id']}/exports",
                           data={"csrf_token": "nope", "analysis_ids": deal["a"]})
    assert response.status_code == 303
    assert execute("SELECT 1 FROM stage.rwb_loss_result_manifest", {}, connection="LOSS") == []


def test_post_enqueue_failure_redirects_to_failed_rows_with_retry(client, deal, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("queue down")
    monkeypatch.setattr(rwb_job_service, "enqueue_rwb_job", boom)
    response = _post(client, deal, [deal["a"]])
    assert response.status_code == 303
    row = execute_one("SELECT stage_status, error_message "
                      "FROM stage.rwb_loss_result_manifest", {}, connection="LOSS")
    assert response.headers["location"] == f"/submissions/{deal['submission_id']}#submission-exports"
    assert row["stage_status"] == "failed" and "queue down" in row["error_message"]
    monkeypatch.undo()
    section = client.get(f"/submissions/{deal['submission_id']}/exports")
    assert ">failed</span>" in section.text and "Retry</button>" in section.text
    assert "queue down" in section.text


# ── exports section ──────────────────────────────────────────────────────────

def _row(text, export_id, analysis_id):
    return next(chunk for chunk in text.split('id="export-analysis-')
                if chunk.startswith(f"{export_id}-{_mid(analysis_id, export_id)}"))


def test_section_renders_queued_rows_with_the_export_columns(client, export):
    section = client.get(export["section"])
    assert section.status_code == 200
    assert section.text.count(">queued</span>") == 2
    assert "A long" in section.text and "B long" in section.text
    row = _row(section.text, export["export_id"], export["a"])
    for value in ("#1", "GR", "Example Re", "CRM-1", "2026-04-01", "2025-12-31",
                  "analyst.a@example.com"):
        assert f">{value}<" in row
    assert f'hx-get="{export["section"]}" hx-trigger="every 10s"' in section.text
    assert "Retry" not in section.text
    assert "data-copy-table" in section.text
    assert '<details class="drow"' not in section.text


def test_rows_show_the_recorded_attributes(client, export):
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET data_name = 'AmFam HU GR 2026', "
        "engine_type = 'DLM' WHERE irp_analysis_id = :a",
        {"a": export["a"]}, connection="LOSS")

    row = _row(client.get(export["section"]).text, export["export_id"], export["a"])

    for value in ("AmFam HU GR 2026", "41958", "USD", "25.0", "DLM", "EQ", "NAEQ"):
        assert f">{value}<" in row


def test_the_export_detail_url_is_gone(client, export):
    assert client.get(export["url"]).status_code == 404


def test_rows_show_counts_aal_error_and_stop_polling(client, export):
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET stage_status = 'staged', load_status = "
        "'loaded', data_id = 4127, staged_row_count = 15689, stochastic_row_count = 15401, "
        "historical_row_count = 288, exp_value_raised_count = 12, std_dev_zeroed_count = 3, "
        "zip_file = 'e/a/x.zip' WHERE irp_analysis_id = :a", {"a": export["a"]},
        connection="LOSS")
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET stage_status = 'failed', "
        "error_message = 'event 1001 matches 2 historical lookup rows' "
        "WHERE irp_analysis_id = :a", {"a": export["b"]}, connection="LOSS")

    section = client.get(export["section"])
    assert section.status_code == 200
    assert 'hx-trigger="every 10s"' not in section.text  # every row terminal → polling stops
    assert ">loaded</span>" in section.text and ">failed</span>" in section.text
    for value in ("4127", "15,689", "15,401", "288", "12", "3"):
        assert f'<span class="l">{value}</span>' in section.text
    assert section.text.count('<span class="l" title="100.0">100</span>') == 2  # AAL per row
    assert "event 1001 matches 2 historical lookup rows" in section.text
    assert section.text.count(">Retry</button>") == 1
    assert f"exports/{export['export_id']}/manifests/{_mid(export['b'])}/retry" in section.text


def test_section_lists_this_submissions_exports_newest_first_with_ordinals(client, export):
    other = seed_submission(client.db.user_a, name="Other")
    seed_manifest(submission_id=other, irp_app_analysis_id=41958, perspective_code="RP")
    older = seed_manifest(submission_id=export["submission_id"], irp_analysis_id=export["a"],
                          irp_app_analysis_id=41958, perspective_code="RL",
                          requested_at="2026-09-01 08:00:00", requested_by_email="r.patel@x.com",
                          stage_status="staged", load_status="loaded", data_id=5)

    section = client.get(export["section"])
    assert section.status_code == 200
    assert re.findall(r'id="export-analysis-([0-9a-f-]{36})-', section.text) == [
        export["export_id"], export["export_id"], older["export_id"]]
    assert re.findall(r'export-ordinal">#(\d)', section.text) == ["1", "1", "2"]
    assert section.text.count("export-analysis--first") == 2  # one heavier rule per export
    assert '<span class="badge badge--neutral">2</span>' in section.text
    assert ">RP<" not in section.text
    row = _row(section.text, older["export_id"], export["a"])
    assert "r.patel@x.com" in row and ">RL<" in row and ">5<" in row and ">100<" in row
    assert 'hx-trigger="every 10s"' in section.text  # the GR export is still in progress


def test_section_empty_state_without_polling(client, deal):
    section = client.get(f"/submissions/{deal['submission_id']}/exports")
    assert "No exports yet" in section.text
    assert "hx-trigger" not in section.text and "export-filters" not in section.text


def test_section_stops_polling_when_every_analysis_is_terminal(client, export):
    execute_command("UPDATE stage.rwb_loss_result_manifest SET stage_status = 'failed'", {},
                    connection="LOSS")
    section = client.get(export["section"])
    assert 'hx-trigger="every 10s"' not in section.text
    assert section.text.count(">failed</span>") == 2


def test_status_filter_keeps_the_matching_rows(client, export):
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET stage_status = 'failed' "
        "WHERE irp_analysis_id = :a", {"a": export["b"]}, connection="LOSS")
    loaded = seed_manifest(submission_id=export["submission_id"], irp_analysis_id=export["a"],
                           perspective_code="RL", requested_at="2026-09-01 08:00:00",
                           stage_status="staged", load_status="loaded", data_id=5)
    url = export["section"]

    failed = client.get(f"{url}?status=failed")
    assert f'id="export-analysis-{export["export_id"]}-{_mid(export["b"])}"' in failed.text
    assert f'{export["export_id"]}-{_mid(export["a"], export["export_id"])}"' not in failed.text
    assert loaded["export_id"] not in failed.text
    assert '<option value="failed" selected>Failed</option>' in failed.text
    assert 'retry?status=failed"' in failed.text  # Retry comes back to the same filter
    assert f'hx-get="{url}?status=failed" hx-trigger="every 10s"' in failed.text

    only_loaded = client.get(f"{url}?status=loaded")
    assert f'id="export-analysis-{loaded["export_id"]}-{_mid(export["a"], loaded["export_id"])}"' in only_loaded.text
    assert export["export_id"] not in only_loaded.text
    # the ordinal is the export's place among all of them, counted before the filter
    assert re.findall(r'export-ordinal">#(\d)', only_loaded.text) == ["2"]

    junk = client.get(f"{url}?status=junk")  # not a filter, and nothing carries it
    assert junk.text.count('id="export-analysis-') == 3
    assert "?status=" not in junk.text


def test_status_filter_matching_nothing_says_so(client, export):
    section = client.get(f"{export['section']}?status=failed")
    assert "No analyses match this filter." in section.text
    assert 'id="export-analysis-' not in section.text


def test_client_crm_and_perspective_filters_combine_over_the_tables_own_values(client, export):
    other = seed_manifest(submission_id=export["submission_id"], irp_analysis_id=export["a"],
                          irp_app_analysis_id=41958, perspective_code="RL", client_id=2,
                          crm_id="CRM-2", requested_at="2026-09-01 08:00:00")
    url = export["section"]

    section = client.get(url)
    assert re.findall(r'<option value="([^"]*)"', section.text) == [
        "", "failed", "loaded", "", "Example Re", "Retired", "", "CRM-1", "CRM-2", "", "GR", "RL"]
    assert section.text.count('hx-include="#export-filters"') == 4

    by_client = client.get(f"{url}?client=Retired")
    assert re.findall(r'id="export-analysis-([0-9a-f-]{36})-', by_client.text) == [
        other["export_id"]]
    assert '<option value="Retired" selected>Retired</option>' in by_client.text
    assert f'hx-get="{url}?client=Retired" hx-trigger="every 10s"' in by_client.text

    combined = client.get(f"{url}?crm_id=CRM-1&perspective=GR&status=loaded")
    assert "No analyses match this filter." in combined.text
    assert f'hx-get="{url}?status=loaded&amp;crm_id=CRM-1&amp;perspective=GR"' in combined.text

    unknown = client.get(f"{url}?client=Nobody&crm_id=CRM-9&perspective=TY")
    assert unknown.text.count('id="export-analysis-') == 3
    assert "?client=" not in unknown.text


def test_submission_page_keeps_the_analyses_grid_and_loads_the_exports_section(client, export):
    page = client.get(f"/submissions/{export['submission_id']}")
    assert page.status_code == 200
    assert 'id="submission-analyses"' in page.text
    assert f'hx-get="/submissions/{export["submission_id"]}/exports"' in page.text
    assert 'hx-trigger="load"' in page.text


# ── Retry ────────────────────────────────────────────────────────────────────

def _retry(client, export, analysis_id, htmx=True, query=""):
    return client.post(f"{export['url']}/manifests/{_mid(analysis_id)}/retry{query}",
                       data={"csrf_token": _csrf()},
                       headers={"HX-Request": "true"} if htmx else {})


def _fail_after_submit(export):
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET stage_status = 'failed', "
        "error_message = 'Analysis not found' WHERE irp_analysis_id = :a", {"a": export["b"]},
        connection="LOSS")
    submit_job = rwb_jobs("submit_results_export")[0]
    rwb_job_service.claim_rwb_job(rwb_job_id=submit_job["id"], worker_id="w1")
    rwb_job_service.complete_rwb_job(rwb_job_id=submit_job["id"], status="succeeded")


def test_retry_on_a_failed_row_rearms_submit_and_rerenders_the_polling_section(client, export):
    _fail_after_submit(export)

    response = _retry(client, export, export["b"])

    assert response.status_code == 200
    assert 'id="submission-exports"' in response.text
    assert 'hx-trigger="every 10s"' in response.text
    assert f'id="export-analysis-{export["export_id"]}-{_mid(export["b"])}"' in response.text
    assert ">queued</span>" in response.text and "Retry</button>" not in response.text
    assert rwb_jobs("submit_results_export")[0]["status_code"] == "pending"
    row = manifest_row(manifest_id=execute_one(
        "SELECT manifest_id FROM stage.rwb_loss_result_manifest WHERE irp_analysis_id = :a",
        {"a": export["b"]}, connection="LOSS")["manifest_id"])
    assert row["stage_status"] == "pending" and row["error_message"] is None


def test_retry_rerenders_the_section_under_the_filter_in_force(client, export):
    _fail_after_submit(export)

    response = _retry(client, export, export["b"], query="?status=failed")

    assert response.status_code == 200
    assert '<option value="failed" selected>Failed</option>' in response.text
    # the retried row is queued again, so the Failed filter no longer holds it
    assert "No analyses match this filter." in response.text


def test_plain_retry_redirects_to_the_exports_section(client, export):
    _fail_after_submit(export)

    response = _retry(client, export, export["b"], htmx=False)

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/submissions/{export['submission_id']}#submission-exports")


def test_retry_on_a_staged_row_enqueues_the_load_only(client, export):
    irp_job_id = seed_export_job(export_id=export["export_id"], irp_analysis_id=export["a"],
                                 irp_id="500", status="FINISHED", edm_id=export["edm_id"])
    stage_job = rwb_job_service.enqueue_rwb_job(
        requestor_type="irp_job", requestor_id=irp_job_id, rwb_job_type="stage_results_export",
        link_type="edm", link_id=export["edm_id"], context_type="irp_analysis",
        context_id=export["a"], input_data={})
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET irp_export_job_id = '500', "
        "stage_status = 'staged', load_status = 'failed', error_message = 'lookup missing' "
        "WHERE irp_analysis_id = :a", {"a": export["a"]}, connection="LOSS")

    response = _retry(client, export, export["a"])

    assert response.status_code == 200
    load = rwb_jobs("load_results_export")
    assert len(load) == 1 and load[0]["requestor_id"] == stage_job
    assert rwb_jobs("stage_results_export")[0]["status_code"] == "pending"  # untouched
    # the row is back in progress, so the section polls until the load worker stamps it
    assert ">in progress</span>" in response.text and "lookup missing" not in response.text
    assert 'hx-trigger="every 10s"' in response.text


def test_retry_refused_answers_409_with_the_reason_in_the_section(client, export):
    execute_command(
        "UPDATE stage.rwb_loss_result_manifest SET stage_status = 'staged', load_status = "
        "'loaded', data_id = 4127 WHERE irp_analysis_id = :a", {"a": export["a"]},
        connection="LOSS")
    response = _retry(client, export, export["a"])
    assert response.status_code == 409
    assert 'id="submission-exports"' in response.text
    assert "Retry refused: already loaded as data ID 4127." in response.text
    assert "Retry</button>" not in response.text
    assert rwb_jobs("load_results_export") == []

    plain = _retry(client, export, export["a"], htmx=False)
    assert plain.status_code == 409


def test_retry_404s_for_a_row_outside_this_export(client, export):
    response = _retry(client, export, str(uuid.uuid4()))
    assert response.status_code == 404


def test_no_retry_button_on_a_waiting_row(client, export):
    seed_export_job(export_id=export["export_id"], irp_analysis_id=export["a"], irp_id="500",
                    status="RUNNING")
    execute_command("UPDATE stage.rwb_loss_result_manifest SET irp_export_job_id = '500' "
                    "WHERE irp_analysis_id = :a", {"a": export["a"]}, connection="LOSS")
    section = client.get(export["section"])
    assert ">in progress</span>" in section.text
    assert "Retry" not in section.text
    assert 'hx-trigger="every 10s"' in section.text
