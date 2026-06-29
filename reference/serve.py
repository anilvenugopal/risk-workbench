#!/usr/bin/env python3
"""
serve.py — self-contained mock server for the cat-modeling workflow tool.

Pure stdlib (no pip install). Run:  python serve.py   ->  http://localhost:8000

WHAT THIS IS / ISN'T
  - Real frontend: the actual ITCSS CSS, real HTMX, real Alpine, manifest-driven
    shell. The interaction patterns are production-faithful.
  - The /api/* handlers below are a FIRST SKETCH OF THE API CONTRACT. Each shows
    the method, path, params, and the HTML-fragment response the real FastAPI
    route should return. Data is in-memory (mock_data.py) — no DB, no auth, no
    file reads, no IRP. See MOCK_NOTES.md for what Claude Code should replace.

ROUTING MODEL
  - Top-level navigation = full page loads (each page renders the shell with the
    correct rail/sidebar/breadcrumb resolved from the manifest by URL).
  - In-page interactions (master-detail, inventory, workflow monitor, search) =
    HTMX requests to /api/* returning HTML fragments.
  - Breadcrumb/active-state are a pure function of the manifest node for the URL,
    which is why a workflow opened from a submission still shows Workflows context.
"""
import html
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import nav_manifest as nav
import mock_data as data

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))

# Pretend signed-in principal (backdoor or SSO both land here in the real app).
SESSION = {"user": "Anil Venugopal", "role": "engineer", "env": "LOCAL"}


# ============================================================ templating
def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def icon_svg(name):
    p = os.path.join(ROOT, "static", "icons", f"{name}.svg")
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return f.read()


def esc(s):
    return html.escape(str(s))


# ============================================================ shell rendering
ACTION_ROUTES = {"+ New workflow": "/workflows/new", "+ New submission": "/submissions"}


def render_rail(active_top_key):
    def btn(n):
        active = " is-active" if n["key"] == active_top_key else ""
        return (f'<a class="shell__railbtn{active}" href="{n["route"]}" '
                f'title="{esc(n["label"])}" aria-label="{esc(n["label"])}">'
                f'{icon_svg(n.get("rail_icon",""))}</a>')
    top = [btn(n) for n in nav.NODES if n["parent"] is None and not n.get("bottom")]
    bottom = [btn(n) for n in nav.NODES if n["parent"] is None and n.get("bottom")]
    return (f'<nav class="shell__rail">{"".join(top)}'
            f'<div class="rail__spacer"></div>{"".join(bottom)}</nav>')


def render_sidebar(section_key, active_key):
    if section_key is None:
        return ""  # Home: no sidebar
    section = nav.BY_KEY[section_key]
    items = []
    for c in nav.children(section_key):
        if c.get("hidden"):
            continue
        active = " is-active" if c["key"] == active_key else ""
        items.append(f'<a class="nav-item{active}" href="{c["route"]}">{esc(c["label"])}</a>')
    actions = "".join(
        f'<a class="sidebar__action" href="{ACTION_ROUTES.get(a, "#")}">{esc(a)}</a>'
        for a in section.get("actions", []))
    actions_block = f'<div class="sidebar__footer">{actions}</div>' if actions else ""
    return (f'<aside class="shell__sidebar">'
            f'<div class="mk-sidehdr">{esc(section.get("sidebar_title", section["label"]))}</div>'
            f'<div class="sidebar__nav">{"".join(items)}</div>{actions_block}</aside>')


def render_breadcrumb(node_key, entity_label=None):
    trail = nav.breadcrumb(node_key)
    # For a detail page, the node's generic label ("Workflow") is replaced by the
    # entity label ("WF-2001"), so drop the placeholder crumb to avoid duplication.
    if entity_label and trail and trail[-1].get("hidden"):
        trail = trail[:-1]
    parts = []
    for i, n in enumerate(trail):
        last = (i == len(trail) - 1) and not entity_label
        label = esc(n["label"])
        if last:
            parts.append(f'<span class="crumb crumb--current">{label}</span>')
        else:
            parts.append(f'<a class="crumb" href="{n["route"]}">{label}</a>')
    if entity_label:
        parts.append(f'<span class="crumb crumb--current">{esc(entity_label)}</span>')
    sep = '<span class="crumb__sep">/</span>'
    return sep.join(parts)


def render_statusbar(activity="2 jobs running", last_action="Ready"):
    env = SESSION["env"]
    env_cls = "statusbar__env--local" if env == "LOCAL" else ""
    return (
        f'<footer class="shell__statusbar">'
        f'<div class="statusbar__left">'
        f'<span class="statusbar__env {env_cls}">{esc(env)}</span>'
        f'<span class="statusbar__user">{esc(SESSION["user"])} \u00b7 {esc(SESSION["role"])}</span>'
        f'</div>'
        f'<div class="statusbar__center" id="sb-activity">{esc(activity)}</div>'
        f'<div class="statusbar__right" id="sb-last">{esc(last_action)}</div>'
        f'</footer>')


def page(node_key, main_html, entity_label=None, title=None, actions=""):
    """Compose a full page: shell chrome (from manifest) + a page-header band
    (breadcrumb + optional actions) + page main content. The app name lives in
    the top bar; the breadcrumb sits where the page title used to."""
    top = nav.top_ancestor(node_key)
    active_top = top["key"] if top else None
    node = nav.BY_KEY[node_key]
    section_key = active_top if (active_top and nav.children(active_top)) else None
    active_item = node_key
    if node.get("hidden"):
        active_item = node["parent"]
    pagehdr = (
        f'<div class="mk-pagehdr">'
        f'<div class="topbar__crumbs">{render_breadcrumb(node_key, entity_label)}</div>'
        f'<div class="pagehdr__actions">{actions}</div>'
        f'</div>')
    shell = load("templates/shell.html")
    return (shell
            .replace("$TITLE", esc(title or node["label"]))
            .replace("$RAIL", render_rail(active_top))
            .replace("$SIDEBAR", render_sidebar(section_key, active_item))
            .replace("$STATUSBAR", render_statusbar())
            .replace("$MAIN", pagehdr + main_html))


# ============================================================ fragment builders
def badge(status):
    m = {
        "active": ("badge--brand", "Active"), "draft": ("badge--neutral", "Draft"),
        "review": ("badge--warning", "In review"), "complete": ("badge--positive", "Complete"),
        "present": ("badge--positive", "Present"), "changed": ("badge--warning", "Changed"),
        "missing": ("badge--negative", "Missing"),
        "critical": ("badge--negative", "Critical"), "warning": ("badge--warning", "Warning"),
    }
    cls, label = m.get(status, ("badge--neutral", status.title()))
    return f'<span class="badge {cls}">{esc(label)}</span>'


def tag_badge(tag):
    if tag == "edm":
        return '<span class="badge badge--brand">EDM</span>'
    if tag == "rdm":
        return '<span class="badge badge--info">RDM</span>'
    return '<span class="tag-empty">\u2014</span>'


def submissions_rows(customer=None, program=None):
    rows = []
    for s in data.SUBMISSIONS.values():
        if customer and customer != "All" and s["customer"] != customer:
            continue
        if program and program != "All" and s["program"] != program:
            continue
        rows.append(
            f'<tr class="data-row" onclick="location.href=\'/submissions/{s["id"]}\'">'
            f'<td>{esc(s["id"])}</td><td>{esc(s["name"])}</td>'
            f'<td>{esc(s["customer"])}</td><td>{esc(s["program"])}</td>'
            f'<td>{badge(s["status"])}</td></tr>')
    if not rows:
        return '<tr><td colspan="5" class="empty">No submissions match.</td></tr>'
    return "".join(rows)


def submission_detail_page(sub_id):
    """Full-page submission detail with summary cards + tabs (Files / Workflows)
    instead of cramped side-by-side columns."""
    s = data.SUBMISSIONS.get(sub_id)
    if not s:
        return '<div class="page-pad"><h2>Submission not found</h2></div>'
    items = data.INVENTORY.get(sub_id, [])
    discs = data.DISCREPANCIES.get(sub_id, [])
    wfs = data.workflows_for_submission(sub_id)
    active_wfs = [w for w in wfs if w["status"] == "active"]
    edm_n = sum(1 for it in items if it["tag"] == "edm")
    rdm_n = sum(1 for it in items if it["tag"] == "rdm")

    def card(label, value, sub_html=""):
        return (f'<div class="sum-card"><div class="sum-card__label">{esc(label)}</div>'
                f'<div class="sum-card__value">{value}</div>{sub_html}</div>')

    files_sub = (f'<div class="sum-card__sub sum-card__sub--alert">{len(discs)} discrepancies</div>'
                 if discs else '<div class="sum-card__sub">no discrepancies</div>')
    wf_sub = (f'<div class="sum-card__sub sum-card__sub--warn">{len(active_wfs)} active</div>'
              if active_wfs else '<div class="sum-card__sub">none active</div>')
    tagged_sub = f'<div class="sum-card__sub">{edm_n} EDM \u00b7 {rdm_n} RDM</div>'
    cards = (card("Files", len(items), files_sub)
             + card("Workflows", len(wfs), wf_sub)
             + card("Tagged", edm_n + rdm_n, tagged_sub))

    wf_rows = "".join(
        f'<a class="wf-link" href="/workflows/{w["id"]}">'
        f'<span>{esc(w["id"])} \u00b7 {esc(w["name"])}</span>'
        f'<span class="wf-link__meta">{esc(w["type"])}</span>{badge(w["status"])}</a>'
        for w in wfs) or '<div class="muted">No workflows yet.</div>'

    return (
        f'<div class="page-pad" x-data="{{ tab: \'files\' }}">'
        f'<div class="detail-head"><h1>{esc(s["name"])}</h1>'
        f'<div class="detail-meta">{esc(s["customer"])} \u00b7 {esc(s["program"])} \u00b7 '
        f'{esc(s["id"])} {badge(s["status"])} '
        f'<span class="detail-meta__handler">Handler: {esc(s["handler"])}</span></div></div>'
        f'<div class="sum-cards">{cards}</div>'
        f'<div class="tabs">'
        f'<button class="tab" :class="{{ \'tab--active\': tab===\'files\' }}" @click="tab=\'files\'">Files ({len(items)})</button>'
        f'<button class="tab" :class="{{ \'tab--active\': tab===\'workflows\' }}" @click="tab=\'workflows\'">Workflows ({len(wfs)})</button>'
        f'</div>'
        f'<div class="tab-panel" x-show="tab===\'files\'">'
        f'<div class="section-head"><h2>File inventory</h2>'
        f'<button class="modal-btn modal-btn--secondary" hx-post="/api/submissions/{sub_id}/inventory/refresh" '
        f'hx-target="#inventory" hx-swap="innerHTML">Refresh inventory</button></div>'
        f'<div id="inventory">{inventory_fragment(sub_id)}</div>'
        f'</div>'
        f'<div class="tab-panel" x-show="tab===\'workflows\'" x-cloak>'
        f'<div class="section-head"><h2>Workflows</h2>'
        f'<a class="modal-btn modal-btn--primary" href="/workflows/new?sub={sub_id}">+ New workflow</a></div>'
        f'<div class="wf-list">{wf_rows}</div>'
        f'</div>'
        f'</div>')


def inventory_fragment(sub_id):
    items = data.INVENTORY.get(sub_id, [])
    if not items:
        return '<div class="muted">No files tracked. Associate a directory and refresh.</div>'
    rows = []
    for it in items:
        tag_controls = (
            f'<button class="tag-btn" hx-post="/api/inventory/{it["id"]}/tag?tag=edm&sub={sub_id}" '
            f'hx-target="#inv-{it["id"]}" hx-swap="outerHTML">EDM</button>'
            f'<button class="tag-btn" hx-post="/api/inventory/{it["id"]}/tag?tag=rdm&sub={sub_id}" '
            f'hx-target="#inv-{it["id"]}" hx-swap="outerHTML">RDM</button>')
        rows.append(
            f'<tr id="inv-{it["id"]}" class="inv-row inv-row--{it["status"]}">'
            f'<td class="inv-name">{esc(it["filename"])}</td>'
            f'<td class="mono">{esc(it["size"])}</td>'
            f'<td class="mono">{esc(it["modified"])}</td>'
            f'<td>{badge(it["status"])}</td>'
            f'<td>{tag_badge(it["tag"])}</td>'
            f'<td class="inv-actions">{tag_controls}</td></tr>')
    disc = data.DISCREPANCIES.get(sub_id, [])
    disc_block = ""
    if disc:
        drows = "".join(
            f'<div class="disc-row">{badge(d["severity"])}'
            f'<span class="disc-art">{esc(d["artifact"])}</span>'
            f'<span class="disc-reason">{esc(d["reason"])}</span></div>' for d in disc)
        disc_block = f'<div class="disc-block"><div class="disc-head">Discrepancies</div>{drows}</div>'
    return (
        f'<table class="mk-table inv-table"><thead><tr>'
        f'<th>File</th><th>Size</th><th>Modified</th><th>Status</th><th>Tag</th><th>Tag as</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>{disc_block}')


def inventory_row(sub_id, art_id):
    for it in data.INVENTORY.get(sub_id, []):
        if it["id"] == art_id:
            tag_controls = (
                f'<button class="tag-btn" hx-post="/api/inventory/{it["id"]}/tag?tag=edm&sub={sub_id}" '
                f'hx-target="#inv-{it["id"]}" hx-swap="outerHTML">EDM</button>'
                f'<button class="tag-btn" hx-post="/api/inventory/{it["id"]}/tag?tag=rdm&sub={sub_id}" '
                f'hx-target="#inv-{it["id"]}" hx-swap="outerHTML">RDM</button>')
            return (
                f'<tr id="inv-{it["id"]}" class="inv-row inv-row--{it["status"]}">'
                f'<td class="inv-name">{esc(it["filename"])}</td>'
                f'<td class="mono">{esc(it["size"])}</td>'
                f'<td class="mono">{esc(it["modified"])}</td>'
                f'<td>{badge(it["status"])}</td>'
                f'<td>{tag_badge(it["tag"])}</td>'
                f'<td class="inv-actions">{tag_controls}</td></tr>')
    return ""


def review_indicator(w):
    gates = [s for s in w["stages"] if s["exec_status"] in data.ACTIVE_GATES]
    if gates:
        kind = "blocked" if any(s["exec_status"] == "blocked" for s in gates) else "review"
        return f'<span class="badge badge--warning">{kind.upper()}</span>'
    if data.workflow_has_error(w):
        return '<span class="badge badge--negative">ERROR</span>'
    return '<span class="tag-empty">\u2014</span>'


def workflows_rows():
    rows = []
    for w in data.WORKFLOWS.values():
        rows.append(
            f'<tr class="data-row" onclick="location.href=\'/workflows/{w["id"]}\'">'
            f'<td>{esc(w["id"])}</td><td>{esc(w["name"])}</td>'
            f'<td>{esc(w["type"])}</td><td>{esc(w["customer"])}</td>'
            f'<td>{badge(w["status"])}</td>'
            f'<td>{review_indicator(w)}</td>'
            f'<td><span class="open-link">Open \u2197</span></td></tr>')
    return "".join(rows)


def stage_status_badge(code):
    m = {"not_started": ("badge--neutral", "NOT STARTED"), "blocked": ("badge--warning", "BLOCKED"),
         "running": ("badge--brand", "RUNNING"), "review": ("badge--warning", "REVIEW"),
         "complete": ("badge--positive", "COMPLETE"), "canceled": ("badge--negative", "CANCELED")}
    cls, label = m.get(code, ("badge--neutral", code.upper()))
    return f'<span class="badge {cls}">{label}</span>'


def task_status_badge(code):
    m = {"not_started": ("badge--neutral", "Queued"), "running": ("badge--brand", "Running"),
         "completed": ("badge--positive", "Done"), "failed": ("badge--negative", "Failed")}
    cls, label = m.get(code, ("badge--neutral", code))
    return f'<span class="badge badge--sm {cls}">{label}</span>'


def oob_count(si, total):
    return f'<span id="stage-{si}-count" hx-swap-oob="true">{total} Task(s)</span>'


def task_block(wf_id, si, t, editable):
    """One collapsible task block. `editable` => compose controls (save/remove)."""
    tid = t["id"]
    remove = (
        f'<button class="btn-sm btn-sm--danger" @click.stop '
        f'hx-post="/api/workflows/{wf_id}/stages/{si}/tasks/{tid}/delete" '
        f'hx-confirm="Remove this task?" hx-target="#task-{tid}" hx-swap="outerHTML">Remove</button>'
        if editable else "")
    if editable:
        fields = (
            f'<div class="task-fields">'
            f'<label>Parameter set <input name="param_set" value="{esc(t["param_set"])}"></label>'
            f'<label>Peril <input name="peril" value="{esc(t["peril"])}"></label></div>'
            f'<div class="task-block__actions">'
            f'<button class="btn-sm btn-sm--primary" hx-post="/api/workflows/{wf_id}/stages/{si}/tasks/{tid}/save" '
            f'hx-include="closest .task-block__body" hx-target="#task-{tid}" hx-swap="outerHTML">Save task</button>'
            f'</div>')
    else:
        fields = (
            f'<div class="task-fields">'
            f'<label>Parameter set <input value="{esc(t["param_set"])}" readonly></label>'
            f'<label>Peril <input value="{esc(t["peril"])}" readonly></label></div>')
    out_has, err_has = bool(t["output"]), bool(t["error"])
    out = esc(t["output"]) if out_has else "(populated when the task completes)"
    err = esc(t["error"]) if err_has else "(populated if the task fails)"
    io = (
        f'<div class="task-io"><div class="task-io__label">OUTPUT</div>'
        f'<div class="task-io__val {"" if out_has else "muted"}">{out}</div></div>'
        f'<div class="task-io"><div class="task-io__label">ERROR</div>'
        f'<div class="task-io__val {"err" if err_has else "muted"}">{err}</div></div>')
    return (
        f'<div class="task-block" id="task-{tid}" x-data="{{open:true}}" '
        f'@expand-all.window="open=true" @collapse-all.window="open=false">'
        f'<div class="task-block__head" @click="open=!open">'
        f'<span class="chevron" :class="{{\'chevron--open\':open}}">\u25b8</span>'
        f'<span class="task-block__title">{esc(t["name"])} \u00b7 {esc(tid)}</span>'
        f'{task_status_badge(t["status"])}<span class="spacer"></span>{remove}</div>'
        f'<div class="task-block__body" x-show="open">{fields}{io}</div>'
        f'</div>')


def stage_status_cell(st):
    """Status badge + dynamic ERROR overlay (any task failed)."""
    overlay = ' <span class="err-overlay">\u00b7 with errors</span>' if data.stage_has_error(st) else ""
    return f'{stage_status_badge(st["exec_status"])}{overlay}'


def review_panel(wf_id, st, si):
    """Shown when a stage is parked in review/blocked: validation (blocked) + decide."""
    if st["exec_status"] not in data.ACTIVE_GATES:
        return ""
    if st["exec_status"] == "blocked":
        head = ('<div class="review-panel__msg review-panel__msg--blocked">'
                f'<strong>Validation</strong> \u2014 {esc(st["validation"] or "blocked")}</div>')
        note = "Review the validation result above, then resolve (Complete) or Cancel."
    else:
        head = '<div class="review-panel__msg">Review the task output / error above, then decide.</div>'
        note = "Complete advances the workflow. A stage with errors is still completed (audited)."
    return (
        f'<div class="review-panel">'
        f'{head}'
        f'<div class="review-panel__actions">'
        f'<button class="modal-btn modal-btn--primary" hx-post="/api/workflows/{wf_id}/stages/{si}/review/complete" '
        f'hx-target="#stages" hx-swap="outerHTML">Complete</button>'
        f'<button class="modal-btn modal-btn--danger" hx-post="/api/workflows/{wf_id}/stages/{si}/review/cancel" '
        f'hx-confirm="Cancel halts the whole workflow. Continue?" '
        f'hx-target="#stages" hx-swap="outerHTML">Cancel</button>'
        f'<span class="review-panel__note">{note}</span></div></div>')


def auto_complete_toggle(wf_id, st, si):
    """Per-stage compose-time toggle (only while the stage is editable/not_started)."""
    if not data.stage_editable(st):
        state = "on" if st["auto_complete"] else "off"
        return f'<span class="ac-static">Auto-complete: <strong>{state}</strong></span>'
    checked = "checked" if st["auto_complete"] else ""
    return (
        f'<label class="ac-toggle" @click.stop>'
        f'<input type="checkbox" {checked} '
        f'hx-post="/api/workflows/{wf_id}/stages/{si}/autocomplete" '
        f'hx-vals=\'js:{{value: event.target.checked ? 1 : 0}}\' '
        f'hx-target="#stage-card-{si}" hx-swap="outerHTML">'
        f'Auto-complete <span class="ac-hint">(off \u2192 needs review)</span></label>')


def stage_card(wf_id, st, si, is_current):
    cur = " stage-card--current" if is_current else ""
    editable = data.stage_editable(st)
    gate = st["exec_status"] in data.ACTIVE_GATES
    attn = " stage-card--attn" if (gate or data.stage_has_error(st)) else ""
    total, completed, _ = data.stage_counts(st)
    tasks_html = "".join(task_block(wf_id, si, t, editable) for t in st["tasks"])
    add_btn = (
        f'<button class="add-task" hx-post="/api/workflows/{wf_id}/stages/{si}/tasks" '
        f'hx-target="#stage-{si}-tasks" hx-swap="beforeend">+ Add task</button>'
        if editable else
        '<button class="add-task" disabled title="Locked once the stage starts">+ Add task</button>')
    skip = ' <span class="stage-skip">skippable</span>' if st.get("skippable") else ""
    return (
        f'<div class="stage-card{cur}{attn}" id="stage-card-{si}" x-data="{{open:true}}" '
        f'@expand-all.window="open=true" @collapse-all.window="open=false">'
        f'<div class="stage-card__head" @click="open=!open">'
        f'<span class="chevron" :class="{{\'chevron--open\':open}}">\u25b8</span>'
        f'<div class="stage-card__name">STAGE: {esc(st["name"])}{skip}</div>'
        f'<div class="stage-card__status">{stage_status_cell(st)}</div>'
        f'<div class="stage-card__counts">'
        f'<span id="stage-{si}-count">{total} Task(s)</span>'
        f'<span>{completed} Completed</span></div></div>'
        f'<div class="stage-body" x-show="open">'
        f'<div class="stage-body__bar">{auto_complete_toggle(wf_id, st, si)}</div>'
        f'{review_panel(wf_id, st, si)}'
        f'<div id="stage-{si}-tasks" class="task-list">{tasks_html}</div>'
        f'{add_btn}</div></div>')


def stage_list(wf_id, projected=None):
    w = projected or data.WORKFLOWS.get(wf_id)
    if not w:
        return '<div class="detail-empty">Not found.</div>'
    running = w.get("_run") is not None and not w.get("_done", False) and not data.workflow_has_active_gate(w)
    cards = "".join(
        stage_card(wf_id, st, i, st["name"] == w["current_stage"])
        for i, st in enumerate(w["stages"]))
    poll = (f' hx-get="/api/workflows/{wf_id}/stages" hx-trigger="every 1500ms" hx-swap="outerHTML"'
            if running else "")
    toolbar = (
        '<div class="stage-toolbar">'
        '<span class="stage-toolbar__label">Stages</span>'
        '<span class="spacer"></span>'
        '<button class="link-btn" @click="$dispatch(\'expand-all\')">Expand all</button>'
        '<button class="link-btn" @click="$dispatch(\'collapse-all\')">Collapse all</button>'
        '</div>')
    return (
        f'<div id="stages"{poll}>'
        f'{toolbar}'
        f'<div class="stage-list">{cards}</div>'
        f'<div class="stage-summary">'
        f'<div><strong>CURRENT STAGE:</strong> {esc(w["current_stage"])}</div>'
        f'<div><strong>STATUS:</strong> {badge(w["status"])}</div></div>'
        f'</div>')


def workflow_actions(wf_id):
    w = data.WORKFLOWS.get(wf_id)
    if not w or w["status"] in ("complete", "canceled"):
        return ""
    if data.workflow_has_active_gate(w) or w["_run"] is not None:
        return ""
    return (f'<button class="modal-btn modal-btn--primary" hx-post="/api/workflows/{wf_id}/run" '
            f'hx-target="#stages" hx-swap="outerHTML">Run workflow</button>')


def review_card(wf_id):
    w = data.WORKFLOWS.get(wf_id)
    if not w:
        return ""
    gates = [s for s in w["stages"] if s["exec_status"] in data.ACTIVE_GATES]
    has_err = data.workflow_has_error(w)
    if gates:
        kinds = ", ".join(sorted({s["exec_status"] for s in gates}))
        sub = f'<div class="sum-card__sub sum-card__sub--warn">{len(gates)} awaiting ({kinds})</div>'
    elif has_err:
        sub = '<div class="sum-card__sub sum-card__sub--warn">completed with errors</div>'
    else:
        sub = '<div class="sum-card__sub">no gates open</div>'
    return (f'<div class="sum-card sum-card--review"><div class="sum-card__label">Review</div>'
            f'<div class="sum-card__value">{len(gates)}</div>{sub}</div>')


def workflow_summary_cards(w):
    task_total = sum(len(s["tasks"]) for s in w["stages"])
    return (
        f'<div class="sum-card"><div class="sum-card__label">Stages</div>'
        f'<div class="sum-card__value">{len(w["stages"])}</div>'
        f'<div class="sum-card__sub">fixed sequence</div></div>'
        f'<div class="sum-card"><div class="sum-card__label">Tasks</div>'
        f'<div class="sum-card__value">{task_total}</div>'
        f'<div class="sum-card__sub">across all stages</div></div>'
        f'{review_card(w["id"])}')


def workflow_detail_main(wf_id):
    w = data.WORKFLOWS.get(wf_id)
    if not w:
        return '<div class="page-pad"><h2>Workflow not found</h2></div>'
    s = data.SUBMISSIONS.get(w["submission"], {})
    composing = data.is_composing(w)
    mode = "Composing" if composing else ("Complete" if w["status"] == "complete"
                                          else ("Canceled" if w["status"] == "canceled" else "Monitoring"))
    type_field = (
        '<select class="wf-field__input"><option>EDM analysis</option></select>'
        if composing else f'<div class="wf-field__val">{esc(w["type"])}</div>')
    input_field = f'<div class="wf-field__val">{esc(w["edm"])} <span class="badge badge--brand">EDM</span></div>'
    return (
        f'<div class="wf-detail" x-data>'
        f'<div class="wf-detail__topline"><span class="wf-mode wf-mode--{mode.lower()}">{mode}</span>'
        f'<span class="muted">{esc(w["id"])} \u00b7 {esc(w["name"])}</span></div>'
        f'<div class="wf-fields">'
        f'<div class="wf-field"><label>WORKFLOW TYPE</label>{type_field}</div>'
        f'<div class="wf-field"><label>CUSTOMER</label><div class="wf-field__val">{esc(w["customer"])}</div></div>'
        f'<div class="wf-field"><label>SUBMISSION</label><div class="wf-field__val">{esc(s.get("name",""))}</div></div>'
        f'<div class="wf-field"><label>INPUT FILE</label>{input_field}</div>'
        f'</div>'
        f'<div class="sum-cards">{workflow_summary_cards(w)}</div>'
        f'{stage_list(wf_id)}'
        f'</div>')


def review_queue_main():
    rows = data.review_queue()
    if not rows:
        return ('<div class="page-pad"><div class="detail-empty">'
                'Nothing awaiting review. Workflows park here when a stage needs review or is blocked.</div></div>')
    body = "".join(
        f'<tr class="data-row" onclick="location.href=\'/workflows/{r["wf"]["id"]}\'">'
        f'<td>{esc(r["wf"]["id"])}</td><td>{esc(r["wf"]["name"])}</td>'
        f'<td>{esc(r["wf"]["customer"])}</td><td>{esc(r["stage"]["name"])}</td>'
        f'<td>{stage_status_badge(r["kind"])}</td>'
        f'<td><span class="open-link">Open \u2197</span></td></tr>'
        for r in rows)
    return (
        f'<div class="page-pad">'
        f'<p class="muted mb-body">Stages awaiting a decision \u2014 '
        f'<strong>review</strong> (work finished, auto-complete off) or <strong>blocked</strong> '
        f'(validation gate). Completed-with-errors is not listed (it isn\u2019t waiting on anyone).</p>'
        f'<table class="mk-table"><thead><tr>'
        f'<th>Workflow</th><th>Name</th><th>Customer</th><th>Stage</th><th>Gate</th><th></th>'
        f'</tr></thead><tbody>{body}</tbody></table></div>')


def edm_options(sub_id):
    items = [it for it in data.INVENTORY.get(sub_id, []) if it["tag"] == "edm" and it["status"] != "missing"]
    if not items:
        return '<option value="">(no EDM-tagged files in this submission)</option>'
    return "".join(f'<option value="{esc(it["filename"])}">{esc(it["filename"])}</option>' for it in items)


def new_workflow_main(preselect_sub=None):
    sub_opts = "".join(
        f'<option value="{esc(s["id"])}" data-customer="{esc(s["customer"])}"'
        f'{" selected" if s["id"] == preselect_sub else ""}>{esc(s["id"])} \u2014 {esc(s["name"])}</option>'
        for s in data.SUBMISSIONS.values())
    first_sub = preselect_sub or next(iter(data.SUBMISSIONS), "")
    return (
        f'<div class="page-pad">'
        f'<form class="new-wf" hx-post="/api/workflows" hx-swap="none">'
        f'<p class="new-wf__intro">The <strong>workflow type</strong> determines which stages and inputs '
        f'apply. Today the only type is <strong>EDM analysis</strong>, which takes one EDM-tagged input file.</p>'
        f'<div class="wf-field"><label>WORKFLOW TYPE</label>'
        f'<select class="wf-field__input" name="type"><option>EDM analysis</option></select></div>'
        f'<div class="wf-field"><label>CUSTOMER</label>'
        f'<input class="wf-field__input" name="customer" id="nw-customer" '
        f'value="{esc(data.SUBMISSIONS.get(first_sub,{}).get("customer",""))}" readonly></div>'
        f'<div class="wf-field"><label>SUBMISSION</label>'
        f'<select class="wf-field__input" name="submission" id="nw-sub" '
        f'hx-get="/api/workflows/new/inputs" hx-target="#nw-input" hx-swap="innerHTML" '
        f'hx-trigger="change" '
        f'onchange="document.getElementById(\'nw-customer\').value=this.selectedOptions[0].dataset.customer">'
        f'{sub_opts}</select></div>'
        f'<div class="wf-field"><label>INPUT FILE</label>'
        f'<select class="wf-field__input" name="input" id="nw-input" '
        f'hx-get="/api/workflows/new/inputs?sub={esc(first_sub)}" hx-trigger="load" hx-swap="innerHTML">'
        f'{edm_options(first_sub)}</select></div>'
        f'<div class="new-wf__actions">'
        f'<a class="modal-btn modal-btn--secondary" href="/workflows">Cancel</a>'
        f'<button type="submit" class="modal-btn modal-btn--primary">Create workflow</button></div>'
        f'</form></div>')


def list_page(node_key, title, blurb):
    """Generic placeholder page for sections we render as shells (out of scope)."""
    return f'<div class="page-pad"><p class="muted">{esc(blurb)}</p></div>'


def search_results(query):
    r = data.search(query)
    q = (query or "").lower().strip()
    apps = [n for n in nav.searchable_nodes() if q and q in n["label"].lower()]
    groups = []
    if apps:
        items = "".join(
            f'<a class="sr-item" href="{n["route"]}"><span class="sr-icon">'
            f'{icon_svg(nav.top_ancestor(n["key"]).get("rail_icon","")) if nav.top_ancestor(n["key"]) else ""}'
            f'</span><span class="sr-label">{esc(n["label"])}</span>'
            f'<span class="sr-meta">Navigation</span></a>' for n in apps[:5])
        groups.append(f'<div class="sr-group"><div class="sr-group__title">Applications</div>{items}</div>')
    if r["submissions"]:
        items = "".join(
            f'<a class="sr-item" href="/submissions/{s["id"]}"><span class="sr-label">{esc(s["name"])}</span>'
            f'<span class="sr-meta">{esc(s["customer"])}</span></a>' for s in r["submissions"])
        groups.append(f'<div class="sr-group"><div class="sr-group__title">Submissions</div>{items}</div>')
    if r["workflows"]:
        items = "".join(
            f'<a class="sr-item" href="/workflows/{w["id"]}"><span class="sr-label">{esc(w["name"])}</span>'
            f'<span class="sr-meta">{esc(w["type"])}</span></a>' for w in r["workflows"])
        groups.append(f'<div class="sr-group"><div class="sr-group__title">Workflows</div>{items}</div>')
    if r["templates"]:
        items = "".join(
            f'<a class="sr-item" href="/templates"><span class="sr-label">{esc(t)}</span>'
            f'<span class="sr-meta">Template</span></a>' for t in r["templates"])
        groups.append(f'<div class="sr-group"><div class="sr-group__title">Templates</div>{items}</div>')
    if not groups:
        if not q:
            return '<div class="sr-hint">Type to search submissions, workflows, templates, and navigation.</div>'
        return '<div class="sr-hint">No results for \u201c' + esc(query) + '\u201d.</div>'
    return "".join(groups)


# ============================================================ HTTP handler
class Handler(BaseHTTPRequestHandler):
    def _send(self, body, status=200, ctype="text/html; charset=utf-8", headers=None):
        data_b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data_b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data_b)

    def log_message(self, *a):
        pass  # quiet

    # ---- static files ----
    def _serve_static(self, path):
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not full.startswith(ROOT) or not os.path.isfile(full):
            return self._send("Not found", 404)
        ext = os.path.splitext(full)[1]
        ctype = {".css": "text/css", ".js": "application/javascript",
                 ".svg": "image/svg+xml", ".png": "image/png"}.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            self._send(f.read(), ctype=ctype + ("; charset=utf-8" if ctype.startswith("text") or ext in (".js", ".svg") else ""))

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        if p.startswith("/static/"):
            return self._serve_static(p)

        # ---------- API (HTMX fragments) ----------
        if p == "/api/search":
            return self._send(search_results((q.get("q") or [""])[0]))
        if p == "/api/submissions":
            return self._send(submissions_rows((q.get("customer") or ["All"])[0], (q.get("program") or ["All"])[0]))
        if p == "/api/workflows":
            return self._send(workflows_rows())
        if p == "/api/workflows/new/inputs":
            return self._send(edm_options((q.get("sub") or [""])[0]))
        if p == "/workflows/new":
            sub = (q.get("sub") or [None])[0]
            return self._send(page("workflows.new", new_workflow_main(sub)))
        m = re.match(r"^/api/submissions/([^/]+)/inventory$", p)
        if m:
            return self._send(inventory_fragment(m.group(1)))
        m = re.match(r"^/api/workflows/([^/]+)/stages$", p)
        if m:
            wf_id = m.group(1)
            return self._send(stage_list(wf_id, projected=data.project_run(wf_id)))
        # any other /api GET is a real miss -> 404 (never fall through to a page)
        if p.startswith("/api/"):
            return self._send("Not found", 404)

        # ---------- Pages ----------
        if p in ("/", "/home"):
            return self._send(page("home",
                load("templates/home.html").replace("$REVIEW_COUNT", str(data.review_gate_count()))))
        if p == "/submissions":
            return self._send(page("submissions.all", load("templates/submissions.html")))
        m = re.match(r"^/submissions/(SUB-[^/]+)$", p)
        if m:
            s = data.SUBMISSIONS.get(m.group(1))
            label = s["id"] if s else m.group(1)
            return self._send(page("submissions.detail",
                                   submission_detail_page(m.group(1)),
                                   entity_label=label,
                                   actions='<a class="modal-btn modal-btn--secondary" href="/submissions">\u2190 All submissions</a>'))
        if p == "/workflows":
            return self._send(page("workflows.all", load("templates/workflows.html"),
                                   actions='<a class="modal-btn modal-btn--primary" href="/workflows/new">+ New workflow</a>'))
        if p == "/workflows/review":
            return self._send(page("workflows.review", review_queue_main()))
        m = re.match(r"^/workflows/(WF-[^/]+)$", p)
        if m:
            wf_id = m.group(1)
            w = data.WORKFLOWS.get(wf_id)
            return self._send(page("workflows.detail", workflow_detail_main(wf_id),
                                   entity_label=(w["id"] if w else wf_id),
                                   actions=workflow_actions(wf_id)))
        if p == "/signin":
            return self._send(load("templates/signin.html"))
        if p == "/signout":
            return self._send("", status=302, headers={"Location": "/signin"})
        # shell-only sections (out of scope this pass)
        section_pages = {
            "/results": ("results.results", "Results", "Result sets and reports appear here."),
            "/templates": ("templates.templates", "Templates", "Templates, parameters, and reference tables."),
            "/irp": ("irp.all", "All IRP Jobs", "Mirrored Moody's IRP jobs appear here."),
            "/admin": ("admin.users", "Users", "User and customer-access administration."),
            "/account": ("account.profile", "Account", "Profile and session."),
        }
        if p in section_pages:
            key, title, blurb = section_pages[p]
            return self._send(page(key, list_page(key, title, blurb)))

        return self._send(page("home", list_page("home", "Page not found",
                          f"No page at {esc(p)}."), title="Not found"), status=404)

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(body)

        def f1(key, default=""):
            return (form.get(key) or [default])[0]

        # ---- create a workflow (New Workflow form) ----
        if p == "/api/workflows":
            wid = data.create_workflow(f1("customer"), f1("submission"), f1("input"), f1("type", "EDM analysis"))
            return self._send("", status=204, headers={"HX-Redirect": f"/workflows/{wid}"})

        # ---- task CRUD (composition) ----
        m = re.match(r"^/api/workflows/([^/]+)/stages/(\d+)/tasks$", p)
        if m:
            wf_id, si = m.group(1), int(m.group(2))
            t = data.add_task(wf_id, si)
            total, _, _ = data.stage_counts(data.get_stage(wf_id, si))
            return self._send(task_block(wf_id, si, t, True) + oob_count(si, total))
        m = re.match(r"^/api/workflows/([^/]+)/stages/(\d+)/tasks/([^/]+)/delete$", p)
        if m:
            wf_id, si, tid = m.group(1), int(m.group(2)), m.group(3)
            data.remove_task(wf_id, si, tid)
            total, _, _ = data.stage_counts(data.get_stage(wf_id, si))
            return self._send(oob_count(si, total))
        m = re.match(r"^/api/workflows/([^/]+)/stages/(\d+)/tasks/([^/]+)/save$", p)
        if m:
            wf_id, si, tid = m.group(1), int(m.group(2)), m.group(3)
            t = data.update_task(wf_id, si, tid, f1("param_set", "default"), f1("peril", "EQ"))
            return self._send(task_block(wf_id, si, t, True) if t else "")

        m = re.match(r"^/api/submissions/([^/]+)/inventory/refresh$", p)
        if m:
            return self._send(inventory_fragment(m.group(1)))
        m = re.match(r"^/api/inventory/([^/]+)/tag$", p)
        if m:
            art = m.group(1)
            sub = (q.get("sub") or [""])[0]
            tag = (q.get("tag") or [""])[0]
            for it in data.INVENTORY.get(sub, []):
                if it["id"] == art:
                    it["tag"] = tag
            return self._send(inventory_row(sub, art))
        m = re.match(r"^/api/workflows/([^/]+)/run$", p)
        if m:
            wf_id = m.group(1)
            data.start_run(wf_id)
            return self._send(stage_list(wf_id, projected=data.project_run(wf_id)))
        m = re.match(r"^/api/workflows/([^/]+)/stages/(\d+)/review/(complete|cancel)$", p)
        if m:
            wf_id, idx, decision = m.group(1), int(m.group(2)), m.group(3)
            data.review_decide(wf_id, idx, decision)
            return self._send(stage_list(wf_id, projected=data.project_run(wf_id)))
        m = re.match(r"^/api/workflows/([^/]+)/stages/(\d+)/autocomplete$", p)
        if m:
            wf_id, si = m.group(1), int(m.group(2))
            data.set_auto_complete(wf_id, si, f1("value", "0") in ("1", "true", "on"))
            st = data.get_stage(wf_id, si)
            return self._send(stage_card(wf_id, st, si, st["name"] == data.WORKFLOWS[wf_id]["current_stage"]))
        if p == "/api/signin":
            return self._send("", status=204, headers={"HX-Redirect": "/"})
        return self._send("Not found", 404)


if __name__ == "__main__":
    print(f"Mock running -> http://localhost:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
