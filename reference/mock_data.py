"""
mock_data.py — in-memory fixtures + a tiny run simulation.

Stands in for SQL Server. The shapes here mirror the entities in DATA_MODEL.md
so Claude Code can see the intended fields. Nothing is persisted; restarting
the server resets state. The workflow "run" advances over wall-clock time so
the live monitor visibly progresses without a real worker.
"""
import time

CUSTOMERS = ["Zurich", "AIG", "Allianz", "Munich Re"]
PROGRAMS = {
    "Zurich": ["Property Cat", "Marine"],
    "AIG": ["NA Property", "Global Casualty"],
    "Allianz": ["EU Windstorm"],
    "Munich Re": ["Global Property"],
}

# ---- Submissions -----------------------------------------------------------
SUBMISSIONS = {
    "SUB-1042": {"id": "SUB-1042", "name": "Zurich Q2 Property Renewal", "customer": "Zurich",
                 "program": "Property Cat", "status": "active", "handler": "Anil Venugopal"},
    "SUB-1043": {"id": "SUB-1043", "name": "AIG NA Property New Business", "customer": "AIG",
                 "program": "NA Property", "status": "review", "handler": "Anil Venugopal"},
    "SUB-1044": {"id": "SUB-1044", "name": "Allianz EU Windstorm 2026", "customer": "Allianz",
                 "program": "EU Windstorm", "status": "active", "handler": "Dana Lee"},
    "SUB-1045": {"id": "SUB-1045", "name": "Munich Re Global Property", "customer": "Munich Re",
                 "program": "Global Property", "status": "complete", "handler": "Anil Venugopal"},
}

# ---- File inventory (immutable artifacts) ----------------------------------
# status: present | changed | missing ; tag: edm | rdm | "" ; severity for discrepancy rows
INVENTORY = {
    "SUB-1042": [
        {"id": "ART-01", "filename": "zurich_property_2026.mdf", "size": "1.4 GB",
         "modified": "2026-06-24 09:12", "status": "present", "tag": "edm"},
        {"id": "ART-02", "filename": "zurich_results_q1.mdf", "size": "880 MB",
         "modified": "2026-06-20 14:03", "status": "present", "tag": "rdm"},
        {"id": "ART-03", "filename": "exposure_appendix.xlsx", "size": "2.1 MB",
         "modified": "2026-06-24 09:15", "status": "present", "tag": ""},
        {"id": "ART-04", "filename": "zurich_property_2026.mdf (v1)", "size": "1.4 GB",
         "modified": "2026-06-18 11:00", "status": "changed", "tag": "edm"},
        {"id": "ART-05", "filename": "broker_cover_note.pdf", "size": "320 KB",
         "modified": "2026-06-12 08:40", "status": "missing", "tag": ""},
    ],
    "SUB-1043": [
        {"id": "ART-11", "filename": "aig_na_property.mdf", "size": "2.0 GB",
         "modified": "2026-06-25 16:20", "status": "present", "tag": "edm"},
        {"id": "ART-12", "filename": "aig_schedule.xlsx", "size": "5.4 MB",
         "modified": "2026-06-25 16:25", "status": "present", "tag": ""},
    ],
    "SUB-1044": [
        {"id": "ART-21", "filename": "allianz_windstorm.mdf", "size": "1.1 GB",
         "modified": "2026-06-26 10:02", "status": "present", "tag": "edm"},
    ],
    "SUB-1045": [],
}

DISCREPANCIES = {
    "SUB-1042": [
        {"artifact": "zurich_property_2026.mdf (v1)", "severity": "critical",
         "reason": "Tagged EDM changed on disk; referenced by WF-2001"},
        {"artifact": "broker_cover_note.pdf", "severity": "warning",
         "reason": "File missing from directory"},
    ],
}

# ---- Workflows -------------------------------------------------------------
# EDM-analysis definition (type drives stages, resolved in code). No HITL kind:
# review is generic via per-stage auto_complete (default false -> parks in review).
EDM_STAGES_DEF = [
    {"name": "EDM Upload",              "skippable": True},
    {"name": "Portfolio Summary Extract", "skippable": False},
    {"name": "Sub-Portfolio Creation",  "skippable": False},
    {"name": "Geo-coding",              "skippable": False},
    {"name": "Hazard lookup",           "skippable": False},
    {"name": "Analysis",                "skippable": False},
    {"name": "Grouping",                "skippable": False},
    {"name": "Export",                  "skippable": False},
]
STAGE_SECS = 3
# stage exec status: not_started / blocked / running / review / complete / canceled
# ERROR is a dynamic rollup (any task failed), never a stored status.
ACTIVE_GATES = ("review", "blocked")

_TASK_SEQ = {"n": 0}
_WF_SEQ = {"n": 2004}


def _new_task_id():
    _TASK_SEQ["n"] += 1
    return f"T-{_TASK_SEQ['n']}"


def make_task(stage_name, status="not_started", param_set="default", peril="EQ"):
    return {"id": _new_task_id(), "name": f"{stage_name} task", "param_set": param_set,
            "peril": peril, "status": status,
            "output": "result.bin" if status == "completed" else "",
            "error": ""}


def make_stages(seed_tasks=1, done_through=-1, review_at=None, blocked_at=None,
                error_at=None, all_complete=False):
    out = []
    for i, d in enumerate(EDM_STAGES_DEF):
        done = all_complete or i <= done_through
        st = {"name": d["name"], "skippable": d["skippable"], "auto_complete": False,
              "exec_status": "complete" if done else "not_started",
              "decided_at": None, "validation": "", "tasks": []}
        ts = "completed" if done else "not_started"
        st["tasks"] = [make_task(d["name"], ts) for _ in range(seed_tasks)]
        if error_at == i and st["tasks"]:
            st["tasks"][0]["status"] = "failed"
            st["tasks"][0]["error"] = "IRP job 4471 failed: model license check"
        if review_at == i:
            st["exec_status"] = "review"
        if blocked_at == i:
            st["exec_status"] = "blocked"
            st["validation"] = "Portfolio summary has 3 unmatched accounts; resolve before sub-portfolio creation."
        out.append(st)
    return out


def stage_counts(st):
    total = len(st["tasks"])
    completed = sum(1 for t in st["tasks"] if t["status"] == "completed")
    errors = sum(1 for t in st["tasks"] if t["status"] == "failed")
    return total, completed, errors


def stage_has_error(st):
    return any(t["status"] == "failed" for t in st["tasks"])


def stage_editable(st):
    return st["exec_status"] == "not_started"


WORKFLOWS = {
    # Draft being composed — per-stage task CRUD + auto_complete toggles.
    "WF-2001": {"id": "WF-2001", "name": "Zurich Property EDM run", "submission": "SUB-1042",
                "customer": "Zurich", "type": "EDM analysis", "edm": "zurich_property_2026.mdf",
                "status": "draft", "current_stage": "EDM Upload",
                "stages": make_stages(seed_tasks=1), "_run": None},
    # Active, parked at a BLOCKED gate (validation) — Sub-Portfolio Creation.
    "WF-2002": {"id": "WF-2002", "name": "Munich Re EDM run", "submission": "SUB-1045",
                "customer": "Munich Re", "type": "EDM analysis", "edm": "munichre_global.mdf",
                "status": "active", "current_stage": "Sub-Portfolio Creation",
                "stages": make_stages(seed_tasks=1, done_through=1, blocked_at=2),
                "_run": {"idx": 2, "started": None}},
    # Complete, but Analysis carries a failed task -> "complete · with errors" (NOT a gate).
    "WF-2003": {"id": "WF-2003", "name": "AIG NA Property EDM run", "submission": "SUB-1043",
                "customer": "AIG", "type": "EDM analysis", "edm": "aig_na_property.mdf",
                "status": "complete", "current_stage": "Export",
                "stages": make_stages(seed_tasks=2, all_complete=True, error_at=5), "_run": None},
    # Active, parked at a REVIEW gate — Portfolio Summary Extract.
    "WF-2004": {"id": "WF-2004", "name": "Allianz Windstorm EDM run", "submission": "SUB-1044",
                "customer": "Allianz", "type": "EDM analysis", "edm": "allianz_windstorm.mdf",
                "status": "active", "current_stage": "Portfolio Summary Extract",
                "stages": make_stages(seed_tasks=1, done_through=0, review_at=1),
                "_run": {"idx": 1, "started": None}},
}


def workflows_for_submission(sub_id):
    return [w for w in WORKFLOWS.values() if w["submission"] == sub_id]


def workflow_has_active_gate(w):
    return any(s["exec_status"] in ACTIVE_GATES for s in w["stages"])


def workflow_has_error(w):
    return any(stage_has_error(s) for s in w["stages"])


def review_gate_count():
    """Active gates only (review + blocked) — the 'waiting on a human' count."""
    return sum(1 for w in WORKFLOWS.values() for s in w["stages"] if s["exec_status"] in ACTIVE_GATES)


def review_queue():
    """Stages needing attention across all workflows: active gates first."""
    rows = []
    for w in WORKFLOWS.values():
        for i, s in enumerate(w["stages"]):
            if s["exec_status"] in ACTIVE_GATES:
                rows.append({"wf": w, "idx": i, "stage": s, "kind": s["exec_status"]})
    return rows


def is_composing(w):
    return w["_run"] is None and w["status"] == "draft" and \
        all(s["exec_status"] == "not_started" for s in w["stages"])


# ---- New workflow + per-stage task CRUD ------------------------------------
def create_workflow(customer, submission_id, edm_file, wtype="EDM analysis"):
    _WF_SEQ["n"] += 1
    wid = f"WF-{_WF_SEQ['n']}"
    WORKFLOWS[wid] = {
        "id": wid, "name": f"{customer} {wtype} run", "submission": submission_id,
        "customer": customer, "type": wtype, "edm": edm_file or "(none)",
        "status": "draft", "current_stage": EDM_STAGES_DEF[0]["name"],
        "stages": make_stages(seed_tasks=0), "_run": None}
    return wid


def get_stage(wf_id, idx):
    w = WORKFLOWS.get(wf_id)
    if not w or idx < 0 or idx >= len(w["stages"]):
        return None
    return w["stages"][idx]


def add_task(wf_id, idx):
    st = get_stage(wf_id, idx)
    if st is None or not stage_editable(st):
        return None
    t = make_task(st["name"])
    st["tasks"].append(t)
    return t


def remove_task(wf_id, idx, task_id):
    st = get_stage(wf_id, idx)
    if st is not None and stage_editable(st):
        st["tasks"] = [t for t in st["tasks"] if t["id"] != task_id]


def update_task(wf_id, idx, task_id, param_set, peril):
    st = get_stage(wf_id, idx)
    if st is None:
        return None
    for t in st["tasks"]:
        if t["id"] == task_id:
            t["param_set"], t["peril"] = param_set, peril
            return t
    return None


def set_auto_complete(wf_id, idx, value):
    st = get_stage(wf_id, idx)
    if st is not None and stage_editable(st):
        st["auto_complete"] = bool(value)
    return st


# ---- Run simulation (cursor model, gated by review/blocked) ----------------
def start_run(wf_id):
    w = WORKFLOWS.get(wf_id)
    if not w:
        return None
    for st in w["stages"]:
        st["exec_status"] = "not_started"
        st["decided_at"] = None
        for t in st["tasks"]:
            t["status"], t["output"], t["error"] = "not_started", "", ""
    w["status"] = "active"
    w["_run"] = {"idx": 0, "started": time.time()}
    w.pop("_done", None)
    return advance(wf_id)


def review_decide(wf_id, stage_idx, decision):
    """COMPLETE or CANCEL a stage parked in review/blocked. CANCEL halts the workflow."""
    w = WORKFLOWS.get(wf_id)
    if not w:
        return None
    st = w["stages"][stage_idx]
    if st["exec_status"] not in ACTIVE_GATES:
        return w
    if decision == "cancel":
        st["exec_status"] = "canceled"
        w["status"] = "canceled"
        w["_run"] = None
        return w
    st["exec_status"] = "complete"   # complete-with-errors stays complete
    st["decided_at"] = time.time()
    if w["_run"] is None:
        w["_run"] = {"idx": stage_idx, "started": time.time()}
    w["_run"]["idx"] = stage_idx + 1
    w["_run"]["started"] = time.time()
    return advance(wf_id)


def advance(wf_id):
    """Project elapsed time onto stages/tasks, parking at review/blocked gates."""
    w = WORKFLOWS.get(wf_id)
    if not w or w["_run"] is None:
        return w
    stages = w["stages"]
    now = time.time()
    while True:
        i = w["_run"]["idx"]
        if i >= len(stages):
            w["status"] = "complete"
            w["current_stage"] = stages[-1]["name"]
            w["_run"] = None
            w["_done"] = True
            return w
        st = stages[i]
        if st["exec_status"] == "complete":
            w["_run"]["idx"] = i + 1
            w["_run"]["started"] = now
            continue
        if st["exec_status"] in ACTIVE_GATES:        # parked gate, wait for human
            w["current_stage"] = st["name"]
            w["status"] = "active"
            return w
        started = w["_run"]["started"]
        elapsed = now - started
        tasks = st["tasks"]
        n = len(tasks)
        if n == 0 or elapsed >= STAGE_SECS:
            for t in tasks:
                t["status"] = "completed"
                t["output"] = t["output"] or "result.bin"
            if st["auto_complete"]:
                st["exec_status"] = "complete"
                w["_run"]["idx"] = i + 1
                w["_run"]["started"] = started + STAGE_SECS
                continue
            st["exec_status"] = "review"             # auto_complete off -> park in review
            w["current_stage"] = st["name"]
            w["status"] = "active"
            return w
        k = int(round(n * max(0.0, elapsed) / STAGE_SECS))
        for j, t in enumerate(tasks):
            if j < k:
                t["status"] = "completed"
                t["output"] = t["output"] or "result.bin"
            elif j == k:
                t["status"] = "running"
            else:
                t["status"] = "not_started"
        st["exec_status"] = "running"
        w["current_stage"] = st["name"]
        w["status"] = "active"
        return w


def project_run(wf_id):
    return advance(wf_id) or WORKFLOWS.get(wf_id)


# ---- Search index ----------------------------------------------------------
def search(query):
    """Returns grouped results: applications (from manifest, added by serve.py),
    submissions, workflows, templates. Case-insensitive substring match."""
    q = (query or "").strip().lower()
    if not q:
        return {"submissions": [], "workflows": [], "templates": []}
    subs = [s for s in SUBMISSIONS.values() if q in s["name"].lower() or q in s["id"].lower()
            or q in s["customer"].lower()]
    wfs = [w for w in WORKFLOWS.values() if q in w["name"].lower() or q in w["id"].lower()]
    templates = [t for t in ["EDF Standard Template", "RDF Standard Template", "Marine Cat Template"]
                 if q in t.lower()]
    return {"submissions": subs[:5], "workflows": wfs[:5], "templates": templates[:5]}
