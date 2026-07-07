# Change Request — Drop Workflow/Stage/Task; Build Directly on IRP Jobs + RWB Jobs

**ID:** CR-002
**Status:** APPLIED (2026-07-06) to `DATA_MODEL.md`, `PRD.md`, and `.specify/memory/constitution.md` (bumped to v2.0.0). Not yet applied to code (no implementation debt exists — see §1). Practice-lead resolutions folded in beyond the settled §2a design (see "Practice-lead resolutions" below).

> **Practice-lead resolutions (2026-07-06), folded into the applied docs:**
> - **`irp_analysis.rdm_id` — UNBLOCKED.** Nullable. `rdm_id` set → the analysis entered the app as a result of importing that RDM (a broker analysis); null → a net-new analysis the analyst ran (own). `origin` (own/broker) becomes derivable from `rdm_id`. This resolves the sole BLOCKED item in §2a.
> - **`irp_rdm.edm_id` — NOT NULL** (the §2a draft had it nullable "for a standalone broker RDM"). There is no scenario where an RDM exists without an EDM.
> - **Everything lives under a `submission`.** `irp_analysis.edm_id` made NOT NULL (analyses are always scoped to an EDM per the installed library); RDMs stay `submission`-scoped (resolves §8 item 1).
> - **`irp_analysis_status_kind` created** — §2a specified `irp_analysis.status` as a kind table but omitted the table itself; added on application.
> - **`mvp-scope.md` added to the repo; `execution-design.md` ignored** (§9.1); the auth-audit gap left by deferring `audit_log` is carried as an open item in `DATA_MODEL.md §13` (§8 item 2).
**Applies to (once approved):** `docs/PRD.md` §12–14 (and every cross-reference to Workflow/Stage/Task elsewhere in the PRD), `docs/DATA_MODEL.md` §1–§11 + table manifest + kind-seed checklist, `.specify/memory/constitution.md` Articles 1, 2, 4, 5, 10, `docs/sequence_diagrams/` (as confirming ground truth, not as something this CR changes).
> **⚠️ `docs/DATA_MODEL.md` is the source of truth for the schema.** This CR is a point-in-time record of the pivot and the reasoning behind it. The §2a catalog reflects the design **as proposed**; where any table, column, or nullability here differs from `DATA_MODEL.md`, **DATA_MODEL.md governs** — do not treat §2a as a build spec. Deltas folded in after §2a was written (some in the practice-lead resolutions below, some in the later 2026-07-06 practice-lead review recorded in `DATA_MODEL.md`):
> - `irp_analysis.origin` — **dropped**; own vs. broker is derived from `rdm_id`. (§2a still lists an `origin` column.)
> - `irp_analysis.rdm_id` — **resolved, nullable**. (§2a marks it BLOCKED / "do not build".)
> - `irp_analysis.edm_id` and `irp_rdm.edm_id` — **NOT NULL**. (§2a shows both nullable.)
> - Packages — **RDM-only is invalid; `package.edm_id` is NOT NULL**. (§2a allows an RDM-only package.)
> - Analysis templates & suites — **IN MVP**. (§2a marks them DEFERRED.)

**Owner:** Analyst + Practice Leader (IRP domain expert), joint review.
**Supersedes:** the workflow-authoring/execution model in PRD §12–14 and DATA_MODEL §6–8 as it stands today (post-CR-001). Does **not** supersede CR-001's heartbeat/reconciler resilience mechanism, which is unchanged — though `rwb_job`'s own columns are substantially redesigned here (§2a).

> **How to use this document.** §2a is the settled design — a master index (every table, one row, what changed) followed by full field-by-field detail for every table in the schema. §3–§5 restate the pivot's rationale and shape for readability. §6 records where the original open design questions were resolved (pointers into §2a, not a live discussion anymore). §7 is the constitution cleanup this pivot forces, still pending. §8 lists the two genuinely unresolved items still outstanding. §9 records inconsistencies found and fixed while building this document, plus one still-unresolved item (§9.1). §10 is explicitly out of scope. §11 is the concrete next-step list.

---

## 1. Why this exists

The analyst's practice-lead partner, who owns the IRP integration relationship, reviewed the Workflow/Stage/Task/Handle model (PRD §12–14, DATA_MODEL §6–8, constitution Articles 1/2/5) and recommended removing it entirely. His core objection: the app modeled itself as a **workflow engine** (authored DAG topology as data: manifests, typed ports, handle-type compatibility, stage machines with review gates) when the actual job is thinner — **submit an IRP operation, track it, chain the next one, let the analyst decide what's next.** The workflow layer added a large surface area (3 declarative sources of truth, a projection/consistency-check subsystem, two independent event streams per stage/task, typed port resolution) to solve a problem — *authored, evolvable process topology* — that this app doesn't actually have. The real sequencing is a short fixed list of ops gated by prerequisite existence checks (§5.4), not a stored state machine.

This is a **legitimate simplification**, not scope-cutting: nothing in the granular/composite sequence diagrams (`docs/sequence_diagrams/`) requires a stage machine, typed handles, or a manifest-projected task template. Every diagram already describes the real interaction as "check prerequisites exist → call one IRP endpoint → track the job → resolve the produced id." The workflow layer was infrastructure invented ahead of a requirement, not derived from one.

**Ground truth for what IRP actually needs** lives in `docs/sequence_diagrams/` (11 granular + composite flows, read those before trusting any job/entity claim below) — but note **`mvp-scope.md` and `execution-design.md`, both referenced by the sequence-diagram README and by your PRD-update draft, do not currently exist in the repo** (confirmed by search — see §9.1). Either they were never checked in, or they live somewhere this pass didn't look. This needs to be resolved before final sign-off, because the README's classification table (Sync / Job / Heavy) and the "boundaries worth noting" call-outs in each granular flow are exactly the input this CR's job-tracking design should be built from.

**No implementation debt to unwind.** Confirmed by inspecting the repo: `alembic/versions/0001_initial.py` creates only 8 tables (none of them workflow/stage/task/irp_job/rwb_job), and no application code under `app/` or `db/` references `workflow`, `stage_instance`, `task_instance`, or `task_template` beyond a nav-manifest label ("Workflows" rail item) and one placeholder template. **This is a documentation-and-design pivot, not a migration.** That changes the risk calculus — there's no rollback plan to write, no data to migrate, no dual-write period. The cost of getting this right before writing code is low; the cost of writing code against a model you're not confident in is the thing worth avoiding.

---

## 2. Scope of this document

This CR captures the pivot and its settled design (§2a). It does **not**:
- Edit `PRD.md`, `DATA_MODEL.md`, or the constitution directly — that fold-back happens in a follow-up pass (§11), table by table, not as one big-bang rewrite.
- Resolve `irp_analysis.rdm_id`, which stays genuinely **BLOCKED** pending the practice lead's direct input (§2a).
- Resolve the missing `mvp-scope.md`/`execution-design.md` files (§9.1).

---

## 2a. Full table catalog (living — built up one group at a time)

This is the authoritative running record of the table-by-table review. Every
table in `DATA_MODEL.md` will appear here by the end of the review pass — either
confirmed unchanged, modified, or marked dropped/deferred. Source of truth for
each entry is `CR_02_DISCUSSION_SCRATCHPAD.md`; this table is the distilled
version for quick reference, not a replacement for the scratchpad's reasoning.

**Legend — Change (If Any) With CR-002:** `UNCHANGED` (confirmed as-is, no
edit) · `MODIFIED` (field-level change) · `RENAMED` (table renamed, fields
otherwise same unless noted) · `NEW` (table introduced by CR-002) ·
`DROPPED` (table removed) · `DEFERRED` (out of scope for this CR; neither
built nor removed — revisit in a future version).

### Master index — every table, one row each

Every table in `DATA_MODEL.md` today, plus every table this CR
introduces. Cross-checked against the ERD diagrams **and** the §12 table
manifest in `DATA_MODEL.md` (six kind tables — `workflow_authoring_status_kind`,
`workflow_execution_status_kind`, `stage_comp_status_kind`,
`stage_exec_status_kind`, `task_status_kind`, `input_source_kind` — are
referenced only in field notes, not drawn as ERD boxes, and would be missed
by reading the diagrams alone). Full detail for every row lives in the
per-table sections below; this index is for at-a-glance review.

The dropped tables are the entire Workflow/Stage/Task construct plus five
unrelated tables (`notification_preference`, `irp_edm_cache`,
`reference_table`, `reference_table_row`, `parameter`). The added tables are
`irp_treaty` and `irp_analysis` (2 new entity tables), plus new kind/detail
tables from the `irp_job` redesign (`irp_job_type_kind`, `irp_job_resource`,
`irp_job_resource_type_kind`), the `rwb_job` redesign
(`rwb_job_requestor_type_kind`, `rwb_job_type_kind`), and the Phase A
validation standardization pass (`validation_run_status_kind`,
`validation_result_category_kind`) — reflecting this review's "always kind
table, strong reason needed to deviate" default applied to several fields
that were previously plain strings (`irp_job_type`, `rwb_job_type`,
`rwb_job.requestor_type`, `validation_run.status`,
`validation_result.category`).

| Table | Disposition | What changed / why |
|---|---|---|
| **Auth & business spine** | | |
| `customer` | UNCHANGED | — |
| `program` | UNCHANGED | — |
| `submission` | MODIFIED | `crm_id` added (new field); `authoring_status`/`cycle` confirmed dropped (pre-dates this CR) |
| `submission_status_kind` | UNCHANGED | — |
| `submission_status_event` | UNCHANGED | — |
| `app_user` | UNCHANGED | — |
| `role_kind` | UNCHANGED | — |
| `user_role` | UNCHANGED | — |
| `user_customer_access` | UNCHANGED | — |
| `audit_log` | DEFERRED | Full auditing (this table + `user_action`) out of scope for this CR entirely; not built |
| `notification_preference` | DROPPED | Notifications re-added in a future version |
| **File inventory** | | |
| `submission_directory` | UNCHANGED | — |
| `file_artifact` | UNCHANGED | — |
| `artifact_source_kind` | MODIFIED | Seed `workflow_output` dropped (no replacement — job outputs land in the same submission directory like any other file); shrinks to 2 seeds |
| `artifact_status_kind` | UNCHANGED | — |
| `artifact_tag_kind` | UNCHANGED | — |
| `discrepancy` | MODIFIED (behavior only, no schema change) | Escalation rule's 2nd tier changes from "referenced by a workflow" to "used in a `package`" |
| `discrepancy_severity_kind` | UNCHANGED | — |
| `ignore_rule` | UNCHANGED | — |
| `ignore_rule_scope_kind` | UNCHANGED | — |
| **EDM & RDM entities + Package** | | |
| `edm` | RENAMED → `irp_edm` | + `file_artifact_id` (renamed from `source_artifact_id`), `irp_id` (renamed from `irp_exposure_id`), `created_by_irp_job_irp_id` (new), `as_of` (new) |
| `rdm` | RENAMED → `irp_rdm` | Same rename pattern as `irp_edm`, plus **new** `edm_id` FK → `irp_edm` |
| `irp_portfolio` | MODIFIED | `irp_portfolio_id` → `irp_id` (renamed); `as_of` added; **no** creation-lineage column (synchronous creation) |
| `package` | UNCHANGED (field-level) | Only FK *targets* renamed (`edm`→`irp_edm`, `rdm`→`irp_rdm`); `edm_id`/`rdm_id` confirmed to stay independent, not redundant with `irp_rdm.edm_id` |
| `irp_treaty` | NEW | New table; `edm_id`, `customer_id`, `irp_id` (not `irp_treaty_id`), `as_of`; no creation-lineage column (synchronous creation) |
| **Analysis** | | |
| `irp_analysis` | NEW | New table; `rdm_id` **BLOCKED** pending practice-lead review (see dedicated note in that section); `status` is a kind table; `created_by_irp_job_irp_id` included |
| **Analysis templates & suites** | | |
| `analysis_template` | DEFERRED | Nice-to-have; build only if users request reusable configs |
| `analysis_template_tag` | DEFERRED | Same | 
| `template_suite` | DEFERRED | Same |
| `template_suite_item` | DEFERRED | Same |
| **Phase A validation** | | |
| `validation_run` | DEFERRED + MODIFIED | `edm_id` FK target renamed; `customer_id` denorm added; `status` → kind table (`validation_run_status_kind`) |
| `validation_run_status_kind` | NEW (kind table) | Created as part of the standardization fix above |
| `validation_result` | DEFERRED + MODIFIED | `category` → kind table (`validation_result_category_kind`) |
| `validation_result_category_kind` | NEW (kind table) | Created as part of the standardization fix above |
| **Workflow / Stage / Task — entire construct** | | |
| `workflow_type_kind` | DROPPED | No replacement — no authored workflow-type distinction |
| `workflow_definition` | DROPPED | No replacement — no manifest to project |
| `stage_kind` | DROPPED | Replaced by the prerequisite gate (§5.4), computed in code |
| `stage_mode_kind` | DROPPED | No replacement — concurrency is a property of the op, not a stage mode |
| `definition_stage` | DROPPED | No replacement — projected data with no manifest left |
| `task_template` | DROPPED | Replaced by `irp_job_type_kind`'s six seeds |
| `port_template` | DROPPED | Replaced by name-based coupling (every op resolves inputs live by name) |
| `handle_type_kind` | DROPPED | No replacement — entities are referenced directly, not via a generic handle type |
| `workflow` | DROPPED | No replacement — `submission` is the anchor; progress derived from `irp_job` rows |
| `workflow_status_event` | DROPPED | No replacement — full auditing deferred project-wide |
| `workflow_authoring_status_kind` | DROPPED | No replacement — no authoring phase distinct from execution |
| `workflow_execution_status_kind` | DROPPED | No replacement — status derived live from `irp_job` rows, not cached on a `workflow` row |
| `stage_instance` | DROPPED | Replaced by the prerequisite gate (§5.4) for enablement; no replacement for the review-gate mechanism specifically |
| `stage_comp_status_kind` | DROPPED | No replacement |
| `stage_exec_status_kind` | DROPPED | Replaced by `irp_job.status`'s own vocabulary |
| `stage_comp_event` | DROPPED | No replacement — auditing deferred |
| `stage_exec_event` | DROPPED | No replacement — auditing deferred |
| `task_instance` | DROPPED — **merged into `irp_job`** | `task_type`→`irp_job_type`; `parameters`→`last_submission_payload`; `heartbeat_at`→`last_tracked_at` |
| `task_status_kind` | DROPPED | Replaced by `irp_job.status`'s own vocabulary |
| `task_comp_event` | DROPPED | No replacement — auditing deferred |
| `task_exec_event` | DROPPED | No replacement — auditing deferred |
| `task_input` | DROPPED | Replaced by name-based coupling |
| `input_source_kind` | DROPPED | No replacement |
| `task_output` | DROPPED — **merged into entity tables** | Replaced by `created_by_irp_job_irp_id` on each entity table |
| **IRP jobs & RWB jobs** | | |
| `irp_job` | MODIFIED (extensively) | See dedicated `irp_job` section — `irp_id` (renamed from `external_ref`), `irp_edm_id`/`irp_portfolio_id`/`irp_rdm_id` (new), `irp_job_type` (kind table), `status` (renamed from `mirrored_status`, stays plain string), `last_submission_payload`/`last_submission_response`/`last_completion_result` (new, replace 4 abandoned satellite-table designs), `submitted_at`/`completed_at`/`last_tracked_at` (new/renamed); `resource_uri` moved off this table; `retry_locked_until` removed |
| `irp_job_type_kind` | NEW | — |
| `irp_job_resource` | NEW | Replaces `irp_job.resource_uri` with a typed `(resource_type, resource_uri)` pair — flagged for future review on multiplicity |
| `irp_job_resource_type_kind` | NEW | — |
| `irp_job_status_event` | DROPPED — never built | `irp_job.last_tracked_at` covers the "still being tracked" need; full audit deferred |
| `rwb_job` | MODIFIED (extensively) | See dedicated `rwb_job` section — `requestor_type`/`requestor_id` (new, replace `origin`/`irp_job_id`), composite `UNIQUE(requestor_type, requestor_id, rwb_job_type)` replaces `request_key`, `rwb_job_type` (kind table, renamed from `work_type`), `input_data` (renamed from `payload`), `output_data` (new), `submitted_at` (new) |
| `rwb_job_requestor_type_kind` | NEW | — |
| `rwb_job_type_kind` | NEW | — |
| `rwb_job_heartbeat` | UNCHANGED | Not revisited — CR-001's mechanism, untouched |
| `rwb_job_status_kind` | UNCHANGED | Not revisited |
| **Analysis results** | | |
| `analysis_result_meta` | MODIFIED | `analysis_id` re-pointed from `task_instance_id`; **new** `rdm_id` FK → `irp_rdm` (settled independently of the still-BLOCKED `irp_analysis.rdm_id` — different question) |
| `result_export` | MODIFIED | `customer_id` denorm added |
| `delivery_kind` | UNCHANGED | — |
| **IRP reference cache** | | |
| `irp_model_profile` | MODIFIED | `synced_at` → `as_of` |
| `irp_output_profile` | MODIFIED | `synced_at` → `as_of` |
| `irp_event_rate_scheme` | MODIFIED | `synced_at` → `as_of` |
| `irp_database_server` | MODIFIED | `synced_at` → `as_of` |
| `irp_tag` | MODIFIED | `irp_tag_id` → `irp_id`; `synced_at` → `as_of` |
| `irp_simulation_set` | MODIFIED | `synced_at` → `as_of` |
| `irp_currency` | MODIFIED | `synced_at` → `as_of` |
| `irp_edm_cache` | DROPPED | Orphaned-EDM/RDM governance question deferred; would need `submission`/`package`/file-artifact association this release doesn't solve |
| **Reference data & parameters** | | |
| `reference_table` | DROPPED | "Add back if there is ever a need" |
| `reference_table_row` | DROPPED | Same |
| `parameter` | DROPPED | Same |

### Auth & business spine

**Table Name:** `customer`
**Purpose:** Top of business hierarchy; RLS root.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `name` | string | — | UNCHANGED |
| `short_code` | string, UNIQUE | Used in auto-naming patterns | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `updated_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user, nullable | — | UNCHANGED |
| `updated_by` | FK → app_user, nullable | — | UNCHANGED |

**Table Name:** `program`
**Purpose:** Program within a customer.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `customer_id` | FK → customer | — | UNCHANGED |
| `name` | string | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `updated_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |
| `updated_by` | FK → app_user | — | UNCHANGED |

**Table Name:** `submission`
**Purpose:** Broker package; anchors all work (directories, artifacts, EDMs, RDMs, jobs).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `program_id` | FK → program | — | UNCHANGED — confirmed explicitly; existing shape stays |
| `customer_id` | FK → customer, denormalized | Set once at creation, immutable | UNCHANGED — confirmed explicitly; existing shape stays |
| `assigned_analyst_id` | FK → app_user | "My submissions" view | UNCHANGED |
| `name` | string, UNIQUE per `program_id` | — | UNCHANGED |
| `status_code` | FK → submission_status_kind | `ACTIVE` / `COMPLETED` / `CANCELLED`, event-sourced, cached current | UNCHANGED |
| `crm_id` | string | The CRM identifier this submission wraps; plain unvalidated text, manually copy-pasted from Salesforce (no SF API integration yet). No click-through. Forward-compatible reference field only. | **NEW field on existing table** |
| `authoring_status` | *(was: plain string, draft/active/complete)* | Prior field, already superseded by `status_code` | **DROPPED** (pre-dates CR-002, reconfirmed here) |
| `cycle` | *(was: string, e.g. "2026Q1")* | Prior field for auto-naming | **DROPPED** (pre-dates CR-002, listed here for completeness of the full catalog) |
| `inserted_at` | datetime | — | UNCHANGED |
| `updated_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |
| `updated_by` | FK → app_user | — | UNCHANGED |

**Table Name:** `submission_status_kind`
**Purpose:** Kind table for `submission.status_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `ACTIVE` / `COMPLETED` / `CANCELLED` — exactly these three | UNCHANGED |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

**Table Name:** `submission_status_event`
**Purpose:** Event-sourced log of `submission.status_code` transitions.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `submission_id` | FK → submission | — | UNCHANGED |
| `status_code` | FK → submission_status_kind | — | UNCHANGED |
| `reason` | string, nullable | Free text, mainly for `CANCELLED` | UNCHANGED |
| `at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |

**Table Name:** `app_user`
**Purpose:** Provisioned user (Entra OID or dev stub).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `entra_oid` | string, nullable, UNIQUE when set | — | UNCHANGED |
| `email` | string | — | UNCHANGED |
| `display_name` | string | — | UNCHANGED |
| `is_active` | bool | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `updated_at` | datetime | — | UNCHANGED |
| *(v1-auth additions, PRD §5.5: `password_hash`, `must_change_password`)* | — | Handled separately under auth feature, not part of this CR's review | UNCHANGED — noted for catalog completeness, not re-litigated here |

**Table Name:** `role_kind`
**Purpose:** Global role vocabulary.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | — | UNCHANGED |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `is_admin` | bool | `true` → `apply_scope()` bypass | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

**Table Name:** `user_role`
**Purpose:** User↔role assignment.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `user_id` | FK → app_user | Composite PK with `role_code` | UNCHANGED |
| `role_code` | FK → role_kind | Composite PK with `user_id` | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |

**Table Name:** `user_customer_access`
**Purpose:** RLS — customers a user may access.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `user_id` | FK → app_user | Composite PK with `customer_id` | UNCHANGED |
| `customer_id` | FK → customer | Composite PK with `user_id` | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |

**Table Name:** `audit_log`
**Purpose:** Append-only: who did what, when.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DEFERRED |
| `user_id` | FK → app_user | — | DEFERRED |
| `action` | string | — | DEFERRED |
| `entity_type` | string | — | DEFERRED |
| `entity_id` | string | — | DEFERRED |
| `detail` | string | — | DEFERRED |
| `at` | datetime | — | DEFERRED |

**Table-level note:** auditing (this table AND the new spine's proposed
`user_action`) is out of scope for CR-002 entirely. Not designed, not built.
Scope/design of an audit mechanism is a future addition. Table stays
documented as deferred, not deleted from DATA_MODEL.md — final placement TBD
at fold-back.

**Table Name:** `notification_preference`
**Purpose:** Per-user notification channel preferences.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DROPPED |
| `user_id` | FK → app_user | — | DROPPED |
| `channel` | string | `teams` / `email` / `in_app` | DROPPED |
| `enabled` | bool | — | DROPPED |
| `on_success` | bool | — | DROPPED |
| `on_failure` | bool | — | DROPPED |
| `inserted_at` | datetime | — | DROPPED |
| `updated_at` | datetime | — | DROPPED |
| `inserted_by` | FK → app_user | — | DROPPED |
| `updated_by` | FK → app_user | — | DROPPED |

**Table-level note:** notifications will be re-added in a future version;
not carried forward now.

### File inventory

**Table Name:** `submission_directory`
**Purpose:** Shared-drive folder linked to a submission; source of the file inventory.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `submission_id` | FK → submission | — | UNCHANGED |
| `unc_path` | string, UNIQUE | Windows UNC path (human-facing) | UNCHANGED |
| `linux_path` | string | Linux mount path (for reading) | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `updated_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |
| `updated_by` | FK → app_user | — | UNCHANGED |

**Table Name:** `file_artifact`
**Purpose:** One immutable version of a file. Append-only — a detected change retains the old row and inserts a new one.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `submission_id` | FK → submission | Included in identity triple since the same relative path can appear in different submissions | UNCHANGED |
| `customer_id` | FK → customer, denorm | — | UNCHANGED |
| `directory_id` | FK → submission_directory, nullable | Null for uploads | UNCHANGED |
| `source_code` | FK → artifact_source_kind | — | UNCHANGED |
| `status_code` | FK → artifact_status_kind | — | UNCHANGED |
| `tag_code` | FK → artifact_tag_kind, nullable | — | UNCHANGED |
| `relative_path` | string | Part of `UNIQUE(submission_id, relative_path, size_bytes, fs_modified_at)` identity triple | UNCHANGED |
| `filename` | string | Original filename with extension | UNCHANGED |
| `name` | string | Display name; initialized as `UPPERCASE(filename without ext)`, user-editable | UNCHANGED |
| `size_bytes` | bigint | Part of identity triple | UNCHANGED |
| `fs_modified_at` | datetime | Part of identity triple | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `updated_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |
| `updated_by` | FK → app_user | — | UNCHANGED |

**Table Name:** `artifact_source_kind` — **SETTLED**
**Purpose:** Kind table for `file_artifact.source_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `shared_drive` / `upload` — **`workflow_output` dropped, no replacement** | **MODIFIED** — seed shrinks from 3 values to 2 |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

**Table-level note — resolved:** `workflow_output` (a file produced by a
workflow task, under the pre-CR-002 model) referenced a source that no
longer exists once workflow/task is deleted. Discussed directly: `shared_drive`
and `upload` both describe files whose **content originates outside the
app** (a broker file discovered on a mounted share, or manually uploaded by
the analyst) — the whole point of `file_artifact`'s drift-detection
machinery (`fs_modified_at`/`size_bytes` identity triple, `present`/
`changed`/`missing` status) is to notice when an externally-controlled file
changes. A file the app produces itself (e.g. a downloaded export via the
`download_export_file` `rwb_job`) has none of that — the app already knows
exactly what's in it and when it was created, so there's nothing to
reconcile.

**Analyst's resolution:** job-produced files are written into the **same
submission directory** the analyst already has set up. They're picked up by
the next reconciliation scan as an ordinary `shared_drive` file (or the
analyst can `upload` one manually) like anything else — **no special
tracking or identification needed.** `workflow_output` is dropped entirely,
with no renamed replacement; `artifact_source_kind` shrinks to its two real
values.

**Table Name:** `artifact_status_kind`
**Purpose:** Kind table for `file_artifact.status_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `present` / `changed` / `missing` | UNCHANGED |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

**Table Name:** `artifact_tag_kind`
**Purpose:** Kind table for `file_artifact.tag_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `edm` / `rdm` only | UNCHANGED |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

**Table Name:** `discrepancy`
**Purpose:** Flagged change/missing on a tracked artifact.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `artifact_id` | FK → file_artifact | — | UNCHANGED |
| `severity_code` | FK → discrepancy_severity_kind | — | UNCHANGED |
| `reason` | string | — | UNCHANGED |
| `resolved` | bool | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `updated_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |
| `updated_by` | FK → app_user | — | UNCHANGED |

**Table-level note (escalation rule, MODIFIED behavior, no schema change):**
prior rule was "severity escalates if the artifact was tagged, and further if
it had been referenced/pinned by a **workflow** (provenance in question)." The
workflow-pinning mechanism (`task_input`) is deleted by this CR. **Analyst
decision:** escalation's second tier is now **"the file artifact is used in a
`package`"** (i.e. it's the `source_artifact_id` behind an `edm`/`rdm` that is
part of a `package` row) — same intent (provenance in question once something
downstream depends on this file), traced through a construct that still
exists. No new column needed on `discrepancy` itself; this is a change to the
*rule* the app evaluates, not the schema.

**Table Name:** `discrepancy_severity_kind`
**Purpose:** Kind table for `discrepancy.severity_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `info` / `warning` / `critical`; `sort_order` is meaningful (escalation) | UNCHANGED |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

**Table Name:** `ignore_rule`
**Purpose:** Admin-managed, gitignore-style ruleset controlling which discovered files become `file_artifact` rows at all.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `scope_code` | FK → ignore_rule_scope_kind | `global` / `customer` / `submission` | UNCHANGED |
| `customer_id` | FK → customer, nullable | Set only when `scope_code=customer` | UNCHANGED |
| `submission_id` | FK → submission, nullable | Set only when `scope_code=submission` | UNCHANGED |
| `pattern` | string | Gitignore-style glob; may start with `!` for negation | UNCHANGED |
| `position` | int | Evaluation order within a scope level | UNCHANGED |
| `is_active` | bool | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |
| `updated_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |
| `updated_by` | FK → app_user | — | UNCHANGED |

**Table Name:** `ignore_rule_scope_kind`
**Purpose:** Kind table for `ignore_rule.scope_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `global` / `customer` / `submission` | UNCHANGED |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

### EDM & RDM entities + Package

**Table Name:** `edm` → **RENAMED to `irp_edm`**
**Purpose:** An EDM as it exists in IRP, distinct from the `.bak`/`.mdf` artifact that produced it.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `submission_id` | FK → submission | — | UNCHANGED |
| `customer_id` | FK → customer, denorm | — | UNCHANGED |
| `file_artifact_id` | FK → file_artifact, nullable | The tagged file artifact used for import | **RENAMED** from `source_artifact_id` |
| `name` | string | IRP EDM name; initialized from `file_artifact.name` | UNCHANGED |
| `irp_id` | int, nullable | Backfilled by poller on import `FINISHED` | **RENAMED** from `irp_exposure_id` |
| `created_by_irp_job_irp_id` | string, nullable | The `irp_job.irp_id` of the job whose completion created this EDM | **NEW field** |
| `as_of` | datetime, nullable | UI trust signal only — when this row was last confirmed against IRP. Stamped on app-driven writes (poller backfill) and by manual "Sync"/"Refresh." No weight on the submit path. | **NEW field** |
| `server_name` | string | IRP DataBridge server | UNCHANGED |
| `status` | string (plain, not kind table) | `pending_import` / `importing` / `ready` / `error` / `delete_pending` / `deleted` | UNCHANGED |
| `deleted_at` | datetime, nullable | Soft delete | UNCHANGED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | UNCHANGED |

**Key decisions:**
- **Rename `edm` → `irp_edm`** and `source_artifact_id` → `file_artifact_id` (analyst's PRD-update draft).
- **`irp_exposure_id` renamed to `irp_id`** — analyst's explicit call, to standardize on `irp_id` as the single convention for "this row's own id in Risk Modeler" across every `irp_*` entity table (matches `irp_model_profile.irp_id`, `irp_event_rate_scheme.irp_id`, etc., which already used this name).
- **`created_by_irp_job_irp_id` added** — entity-side creation lineage, settled in §6.1's reversal (see scratchpad). Points at `irp_job.irp_id` (see naming-consistency decision below), not a job-side reference table.
- **Naming consistency, settled after back-and-forth:** the column was originally named `created_by_irp_job_ref_id`, because `irp_job`'s own id column had briefly been renamed `irp_job.irp_id` → `irp_job.ref_id`. On review, `irp_id` was found to already be the established convention in five other places in the schema (`rdm.irp_id`, `irp_model_profile.irp_id`, `irp_output_profile.irp_id`, `irp_event_rate_scheme.irp_id`, `irp_simulation_set.irp_id`) — so `ref_id` would have been the inconsistent outlier, not the fix. **Reverted: `irp_job.ref_id` → back to `irp_job.irp_id`; `created_by_irp_job_ref_id` → `created_by_irp_job_irp_id`.**
- **`as_of` added** — settled in §6.2: pure UI trust signal, applies uniformly to all `irp_*` tables, never consulted on the submit path.

**Table Name:** `rdm` → **RENAMED to `irp_rdm`**
**Purpose:** A broker-supplied RDM as it exists in IRP.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `submission_id` | FK → submission | — | UNCHANGED |
| `customer_id` | FK → customer, denorm | — | UNCHANGED |
| `file_artifact_id` | FK → file_artifact, nullable | — | **RENAMED** from `source_artifact_id` |
| `edm_id` | FK → irp_edm, nullable | Null for a standalone broker RDM not paired to any EDM in this app | **NEW field** |
| `name` | string | IRP RDM name; initialized from `file_artifact.name` | UNCHANGED |
| `irp_id` | int, nullable | Backfilled by poller on import completion | UNCHANGED — already used this name before this CR |
| `created_by_irp_job_irp_id` | string, nullable | Same pattern as `irp_edm` | **NEW field** |
| `as_of` | datetime, nullable | Same pattern as `irp_edm` | **NEW field** |
| `status` | string (plain, not kind table) | `pending_import` / `importing` / `ready` / `error` / `delete_pending` / `deleted` | UNCHANGED |
| `deleted_at` | datetime, nullable | — | UNCHANGED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | UNCHANGED |

**Key decisions:**
- **`edm_id` added** — analyst's explicit call: "irp_rdm will have an FK reference to the irp_edm.id." A real new relationship; the current live schema has no `edm_id` on `rdm` at all, only `submission_id`.
- **Coexists with `package.edm_id`/`package.rdm_id`, does not replace them.** Raised as a question (does this new direct FK make `package`'s pairing redundant?) and explicitly resolved: **`package` keeps its own independent nullable `edm_id`/`rdm_id`** because a package can be EDM-only or RDM-only — cases where there is no `irp_rdm` row to hang an `edm_id` off of at all. The two relationships serve different purposes and both stay.
- Same rename/`as_of`/lineage decisions as `irp_edm` above, applied identically.

**Table Name:** `irp_portfolio`
**Purpose:** Portfolio created within an EDM in IRP.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `edm_id` | FK → irp_edm | — | UNCHANGED (target renamed, same relationship) |
| `customer_id` | FK → customer, denorm | — | UNCHANGED |
| `name` | string | Portfolio name in IRP | UNCHANGED |
| `irp_id` | int, nullable | Written synchronously — `create_portfolio()` returns `(portfolio_id, request_body)` in-request (HTTP 201); no poller involved | **RENAMED** from `irp_portfolio_id` |
| `as_of` | datetime, nullable | Same UI-trust-signal pattern as `irp_edm`/`irp_rdm` | **NEW field** |
| `deleted_at` | datetime, nullable | — | UNCHANGED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | UNCHANGED |

**Key decisions:**
- **`irp_portfolio_id` renamed to `irp_id`** — analyst's explicit call, same `irp_id`-everywhere consistency reasoning as `irp_edm`.
- **No `created_by_irp_job_irp_id` on this table — deliberately, not an oversight.** Portfolio creation is synchronous (`create_portfolio()`, HTTP 201, no `irp_job` row per the sequence diagram). Analyst confirmed directly: "irp_portfolio won't get created_by_irp_job_irp_id if it is synchronous." A later GeoHaz job that mutates this portfolio (§6.1: `role=input`, no new identity produced) does not retroactively populate this either — GeoHaz didn't create the portfolio, and mutation-only touches are deliberately not tracked as lineage at all (§6.1).

**Table Name:** `package`
**Purpose:** EDM/RDM pairing — the unit an analyst saves and syncs to Risk Modeler together.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `submission_id` | FK → submission | — | UNCHANGED |
| `customer_id` | FK → customer, denorm | — | UNCHANGED |
| `edm_id` | FK → irp_edm, nullable | — | UNCHANGED (target renamed, same relationship) |
| `rdm_id` | FK → irp_rdm, nullable | — | UNCHANGED (target renamed, same relationship) |
| `deleted_at` | datetime, nullable | Soft delete | UNCHANGED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | UNCHANGED |

**Key decisions:**
- **No field-level changes at all** — every column is exactly what exists in DATA_MODEL.md §3a today; only the FK *targets* are renamed (`edm`→`irp_edm`, `rdm`→`irp_rdm`), not the columns themselves.
- **`edm_id`/`rdm_id` stay independently nullable, confirmed not redundant with the new `irp_rdm.edm_id`** — analyst's explicit reasoning: "package still need its own edm_id/rdm_id — this is because it may in rare cases have EDM only or RDM only." An EDM-only or RDM-only package has no counterpart row to derive a pairing from, so `package` cannot rely solely on `irp_rdm.edm_id`.
- **Still no independent status column** — unchanged reasoning from existing DATA_MODEL §3a (EDM/RDM each already carry their own status; a third rolled-up value adds no benefit). Not revisited, still holds post-pivot.
- **Package's own pre-existing open TBD is still open, not resolved by this pass:** how a stub/real `edm_upload` `rwb_job` triggers the chained `rdm_upload` once the *IRP job* it submits reaches a terminal status (the existing "worker succeeds → worker creates next `rwb_job`" pattern doesn't cover a poller-originated trigger). Flagging that §6.1's reversal (lineage via `created_by_irp_job_irp_id` on the entity) may make this easier to solve later — e.g. the poller, on seeing an EDM's `irp_job` reach `FINISHED`, could look up any `package` row with a matching `edm_id` and enqueue `rdm_upload` directly — but this is **not decided, just a possible direction for a future session.**

**Table Name:** `irp_treaty` — **NEW table** (no `treaty` table exists anywhere in DATA_MODEL.md today)
**Purpose:** A reinsurance treaty as it exists in IRP, belonging to one EDM. Referenced by analyses by name (§13 name-based coupling), not by id.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | NEW |
| `edm_id` | FK → irp_edm | The EDM this treaty belongs to | NEW |
| `customer_id` | FK → customer, denorm | For `apply_scope()` — same RLS pattern as every other entity table | NEW |
| `name` | string | Treaty name in IRP | NEW |
| `irp_id` | int, nullable | Backfilled once created in IRP (`treatyId`) | NEW |
| `as_of` | datetime, nullable | UI trust signal — when this row was last confirmed against Risk Modeler | NEW |
| `deleted_at` | datetime, nullable | Soft delete | NEW |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | NEW |

**Key decisions:**
- **`edm_id` FK added** — analyst's explicit call: "a treaty is tied to edm so we need an FK for it." Consistent with the sequence diagram (`create_treaty(edm_name, ...)`, resolved via `search_edms`) and existing PRD text ("a treaty belongs to an EDM").
- **`customer_id` denorm added** — analyst's explicit call, for RLS scoping consistency with every other entity table.
- **`irp_treaty_id` named `irp_id` instead** — analyst's explicit call, same consistency reasoning applied to `irp_edm`/`irp_rdm`/`irp_portfolio` above.
- **`as_of` included; `created_by_irp_job_irp_id` deliberately excluded.** Treaty create is always synchronous (`create_treaty`, per `treaty_view_edit.md` — no `irp_job` row is ever created for treaty CRUD), so there is no job to be its creator — same reasoning as `irp_portfolio`. `as_of` is still included, and the analyst gave the general rule for *why every* `irp_*` table gets it, worth recording verbatim: **"every irp_ table needs as of because there could be drift between Risk Modeler and our local copy."** (Sharper than this document's earlier framing, which described `as_of` mainly as a UI-trust-signal — both are true; drift-with-an-external-system-of-record is the underlying reason a trust signal is needed at all.)
- **No `irp_job_reference` / consumption tracking for treaty, still deferred** — unchanged from §6.1's original decision to skip treaty for now, since there's no job to hang any reference off of.

**Table Name:** `irp_analysis` — **NEW table** (no `analysis` table exists in DATA_MODEL.md today; drafted by the analyst, not yet reconciled with the live schema)
**Purpose:** An analysis (or, when `is_group=true`, a group — a group IS an analysis in Risk Modeler) belonging to an EDM.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | NEW |
| `edm_id` | FK → irp_edm, nullable | — | NEW |
| `customer_id` | FK → customer, denorm | — | NEW |
| `group_parent_id` | FK → irp_analysis, nullable, self-ref | The group this analysis is a member of, if any | NEW |
| `name` | string | IRP analysis name | NEW |
| `irp_id` | int, nullable | Resolves only after `FINISHED` (renamed from analyst draft's `irp_analysis_id`, per the `irp_id`-everywhere consistency decision) | NEW |
| `is_group` | bool | `true` → this analysis IS a group (`isGroup`, confirmed present in the installed `irp_integration/analysis.py` source, line ~474) | NEW |
| `origin` | string (plain) | `own` / `broker` | NEW — **flagged for practice-lead attention, not blocked** (see note below) |
| `status` | — | `pending` / `running` / `ready` / `error` | NEW — **kind table**, not plain string (see decision below) |
| `created_by_irp_job_irp_id` | string, nullable | The `irp_job.irp_id` of the job whose completion created this analysis (single-analysis submit or grouping submit) | NEW |
| `deleted_at` | datetime, nullable | — | NEW |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | NEW |

**`rdm_id` — BLOCKED, not included in the table above. Needs practice-lead
(Moody's IRP domain expert) review before this table can be finalized.**

The analyst's original draft included `uniqueidentifier rdm_id FK nullable`
alongside `edm_id`, self-flagged `------ NEEDS REVIEW`. The friction, in the
analyst's own words: *"analysis can exist in EDM as well as RDM. we need to
identify which one we are talking about."*

Investigated directly against the **installed `irp_integration` library
source** (`.venv/lib/python3.14/site-packages/irp_integration/`, version
`0.2.1.dev23` — not the sequence diagrams, the actual package code), since
this is exactly the kind of claim that needs verifying against ground truth
rather than assumed from prose:

- **Every real analysis-lookup method scopes an analysis only by
  `(analysisName, exposureName)` — i.e. relative to an EDM.**
  `analysis.py`'s `search_analyses`, `search_analyses_paginated`, and
  `get_analysis_by_name` (line 1206–1230) all filter on `exposureName`; there
  is **no `rdmName` or `sourceRdmName` filter key anywhere in the installed
  library.**
- **`rdm.py`'s `submit_rdm_import_job`** (line 677–718) takes an `edm_name`
  purely to resolve the target EDM's resource URI for the import — it does
  **not** create, tag, or expose any analysis records tied to the RDM as a
  documented return value or side effect in this library version.
- **This directly contradicts existing project documentation.** The
  project's own `docs/sequence_diagrams/granular/rdm_upload.md` states broker
  analyses become discoverable post-import via
  `analysis.search_analyses_paginated(filter='sourceRdmName="<rdm_name>"')` —
  **a filter key that does not exist in the installed library's actual
  `search_analyses` implementation.** Either this describes a Moody's REST
  capability the installed library version doesn't wrap yet, a
  misunderstanding from when the sequence diagram was authored, or a
  different mechanism entirely (e.g. resolved via `analysis_result_meta` or a
  DataBridge query rather than the REST search API).
- Also relevant: this session's saved memory (`irp-integration-api.md`,
  4 days old, self-flags as needing re-verification) shows the **IRP Data
  Hierarchy** as `ExposureSet → Exposure/EDM → Portfolio → Treaty/Accounts` —
  **RDM does not appear in that hierarchy at all**, consistent with what the
  source code shows.

**Analyst's explicit decision:** mark this **BLOCKED**, do not guess a schema
for it. *"there might be a need for a completely new table based on what the
practice lead says. i just dont know at this time."* Possible outcomes once
the practice lead weighs in (not a decision, just the range this could land
in): (a) `rdm_id` as drafted, if Moody's does expose this relationship some
other way; (b) a wholly separate table for broker/RDM-sourced analyses,
distinct from `irp_analysis`; (c) the relationship is resolved without a
direct FK at all (e.g. via a DataBridge query at read time, matching how RDM
result comparison is described elsewhere in the PRD). **Do not build against
any of these until confirmed.**

**Key decisions on the rest of the table:**
- **`status` → kind table, not plain string.** Analyst's explicit, general
  rule, worth recording as a standing default for this whole review, not just
  this one field: **"always _kind table. there needs to be a strong strong
  reason to not have that."** Plain-string status is the exception (Article
  3's carve-out, for columns that mirror an *external* system's vocabulary
  the app doesn't control — `irp_job.status`/`irp_job_type` qualify;
  `irp_analysis.status` is an app-defined vocabulary and does not). This
  default should also inform the still-open `irp_job_type_kind`/
  `irp_job_status_kind` question later in this review (backlog item), not
  just this table.
- **`origin` (`own`/`broker`) stays on the table as drafted, not blocked** —
  analyst's explicit call: *"origin can stay on the table for now. but call
  it out for leaders attention."* Flagged here because `origin='broker'` is
  only meaningful once the `rdm_id` question above is actually resolved — an
  `origin` value with no mechanism to populate or verify it against a real
  RDM relationship is a placeholder, not a working field, until the
  practice-lead conversation happens.
- **`irp_analysis_id` renamed to `irp_id`** — same consistency decision
  applied to every other entity table in this review.
- **`is_group` confirmed against actual source**, not just the PRD's prose
  claim — `isGroup` is a real field the installed library reads
  (`analysis.py` line 474), not an assumption.
- **`created_by_irp_job_irp_id` added — confirmed by the analyst.** Analysis
  creation (single-analysis submit or grouping submit) is always async
  (`irp_job`), so this table follows the §6.1 lineage pattern like
  `irp_edm`/`irp_rdm`, unlike the synchronously-created `irp_portfolio`/
  `irp_treaty`.

### Analysis templates & suites — **DEFERRED (feature-level, not CR-002-specific)**

**Table Name:** `analysis_template`
**Purpose:** A saved configuration for one analysis job (model profile, output profile, event rate scheme, treaty pattern, currency), used for batch submission.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DEFERRED |
| `customer_id` | FK → customer, scope | — | DEFERRED |
| `created_by` | FK → app_user | — | DEFERRED |
| `name` | string | — | DEFERRED |
| `analysis_profile_name` | string | IRP model profile name; comes directly from `submit_portfolio_analysis_job()` parameters | DEFERRED |
| `output_profile_name` | string | — | DEFERRED |
| `event_rate_scheme_name` | string, nullable | Required for DLM, optional for HD | DEFERRED |
| `treaty_name_pattern` | string, nullable | Glob/regex pattern for auto-selecting treaties from the EDM at submit time | DEFERRED |
| `currency_code` | string | — | DEFERRED |
| `region_label` | string | Display metadata; used in auto-naming | DEFERRED |
| `peril_code` | string | Display metadata; used in auto-naming | DEFERRED |
| `auto_name_pattern` | string | Jinja2 pattern, e.g. `{{ customer.short_code }}-{{ cycle }}-{{ region }}-{{ peril }}` | DEFERRED — **note:** this example string references `{{ cycle }}`, a `submission` field already dropped in a pre-CR-002 change (DATA_MODEL §1's own correction). Stale example text, not a live dependency; not touched here since the whole table is deferred. |
| `franchise_deductible` | bool | — | DEFERRED |
| `min_loss_threshold` | float, nullable | — | DEFERRED |
| `num_max_loss_event` | int, nullable | — | DEFERRED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | DEFERRED |

**Table Name:** `analysis_template_tag`
**Purpose:** Tags on a template (junction table).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `template_id` | FK → analysis_template | — | DEFERRED |
| `irp_tag_id` | string | IRP tag ID from `irp_tag` cache | DEFERRED |
| `inserted_at` | datetime | — | DEFERRED |
| `inserted_by` | FK → app_user | — | DEFERRED |

**Table Name:** `template_suite`
**Purpose:** Named collection of templates for batch submission (e.g. "Global 2026 Q1").
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DEFERRED |
| `customer_id` | FK → customer, scope | — | DEFERRED |
| `name` | string | — | DEFERRED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | DEFERRED |

**Table Name:** `template_suite_item`
**Purpose:** Ordered item within a suite.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DEFERRED |
| `suite_id` | FK → template_suite | — | DEFERRED |
| `template_id` | FK → analysis_template | — | DEFERRED |
| `position` | int | Drives submission order | DEFERRED |
| `portfolio_name_override` | string, nullable | Overrides the default portfolio for this item | DEFERRED |
| `inserted_at` | datetime | — | DEFERRED |
| `inserted_by` | FK → app_user | — | DEFERRED |

**Table-level note:** confirmed none of these four tables reference
`workflow`/`stage`/`task` anywhere in their fields or relationships — they
feed `submit_portfolio_analysis_job()` call parameters directly and are
orthogonal to the workflow-removal pivot. **Analyst's explicit call: this
whole feature is nice-to-have, not required for CR-002.** *"this is a nice
to have feature - we will implement if users request for reusable configs."*
Marked DEFERRED (same treatment as `audit_log`) — documented as-is, not
deleted, not designed further, revisit only if/when there's real demand for
reusable analysis configs.

### Phase A — DataBridge validation results — **DEFERRED**

**Table Name:** `validation_run`
**Purpose:** A triggered DataBridge validation query set execution against an imported EDM.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DEFERRED |
| `edm_id` | FK → irp_edm | — | DEFERRED (target renamed `edm`→`irp_edm`, mechanical update only) |
| `customer_id` | FK → customer, denorm | For `apply_scope()` — standardization fix, was missing from the original table | **NEW field, added during this standardization pass** |
| `triggered_by` | FK → app_user | — | DEFERRED |
| `status_code` | FK → validation_run_status_kind | — | **Converted to kind table** (was: plain string `running`/`complete`/`error`) — standardization fix |
| `error_detail` | string, nullable | — | DEFERRED |
| `started_at` | datetime | — | DEFERRED |
| `completed_at` | datetime, nullable | — | DEFERRED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | DEFERRED |

**Table Name:** `validation_run_status_kind` — **NEW kind table**
**Purpose:** Kind table for `validation_run.status_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `running` / `complete` / `error` | NEW |
| `label` | string | — | NEW |
| `sort_order` | int | — | NEW |
| `inserted_at` | datetime | — | NEW |

**Table Name:** `validation_result`
**Purpose:** Metadata for one validation query result within a run; row-level output lives in Parquet.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DEFERRED |
| `validation_run_id` | FK → validation_run | — | DEFERRED |
| `category_code` | FK → validation_result_category_kind | — | **Converted to kind table** (was: plain string `quality`/`consistency`/`completeness`/`summary`) — standardization fix |
| `check_name` | string | — | DEFERRED |
| `query_file` | string | Relative path under `app/databridge_queries/` | DEFERRED |
| `passed` | bool, nullable | Null for summary/profiling checks without binary pass-fail | DEFERRED |
| `row_count` | int | Number of rows returned by the query | DEFERRED |
| `output_file_path` | string, nullable | Path to Parquet file under submission outputs dir | DEFERRED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | DEFERRED |

**Table Name:** `validation_result_category_kind` — **NEW kind table**
**Purpose:** Kind table for `validation_result.category_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `quality` / `consistency` / `completeness` / `summary` | NEW |
| `label` | string | — | NEW |
| `sort_order` | int | — | NEW |
| `inserted_at` | datetime | — | NEW |

**Table-level note:** this section is **unrelated to** the `irp_job_validation`
satellite-table question resolved earlier in §6.3 (that one was about
job-level prereq validation, folded into `irp_job.last_submission_response` —
no separate table). This `validation_run`/`validation_result` pair is Phase A
**DataBridge data-quality validation** (SQL checks against imported EDM data —
completeness, consistency, profiling) — a different feature entirely, name
similarity aside. Confirmed neither table references
`workflow`/`stage`/`task`; both are orthogonal to the pivot.

**Standardization fixes applied during this review** (not just a rename
pass, per the analyst's explicit instruction to review and fix before
deferring):
- **`validation_run.status` → kind table** (`validation_run_status_kind`),
  same "always kind table, strong reason needed to deviate" default
  established during the `irp_analysis` review.
- **`validation_result.category` → kind table**
  (`validation_result_category_kind`), same reasoning.
- **`validation_run.customer_id` added** — every other entity-adjacent table
  in this review carries a `customer_id` denorm for `apply_scope()`; this
  table was missing it (only had `edm_id`, which would have required a join
  through `irp_edm.customer_id` to scope). Added for consistency.

**Marked DEFERRED** — same treatment as `audit_log` and analysis
templates/suites: documented with fixes applied, not deleted, not designed
further beyond this standardization pass, revisit if/when Phase A validation
work is actually picked up.

### Workflow / Stage / Task — **DROPPED (all 24 tables)**

The entire construct this CR replaces. **Confirmed complete list — 24
tables, cross-checked against both the DATA_MODEL §6–7 ERD diagrams and the
§12.6–12.7 table manifest** (6 kind tables — `workflow_authoring_status_kind`,
`workflow_execution_status_kind`, `stage_comp_status_kind`,
`stage_exec_status_kind`, `task_status_kind`, `input_source_kind` — are
referenced in field FK notes but not drawn as separate ERD boxes; they would
have been missed by reading the ERD diagrams alone).

**§6 — Workflow definition (manifest-projected), 8 tables:**

| Table | Purpose (for record; no longer needed) | Change |
|---|---|---|
| `workflow_type_kind` | Workflow type vocabulary (`edm_analysis`/`rdm_import`/`edm_import_only`) | **DROPPED** |
| `workflow_definition` | Versioned, manifest-projected workflow definition | **DROPPED** |
| `stage_kind` | Fixed 8-stage vocabulary with sort order | **DROPPED** |
| `stage_mode_kind` | `singleton`/`parallel`/`sequential` | **DROPPED** |
| `definition_stage` | Stages within a definition (projected) | **DROPPED** |
| `task_template` | Task blueprint within a stage (projected) | **DROPPED** |
| `port_template` | Typed ports of a task template (projected) | **DROPPED** |
| `handle_type_kind` | Handle type registry (`edm`/`rdm`/`analysis`/`group`) | **DROPPED** |

**§7 — Workflow instance (runtime), 16 tables:**

| Table | Purpose (for record; no longer needed) | Change |
|---|---|---|
| `workflow` | Workflow instance, pins a definition version | **DROPPED** |
| `workflow_status_event` | Append-only lifecycle log (authoring + execution streams) | **DROPPED** |
| `workflow_authoring_status_kind` | `draft`/`validated`/`runnable` | **DROPPED** |
| `workflow_execution_status_kind` | `active`/`complete`/`canceled`(/`failed`, already flagged as an unreachable seed in the current doc) | **DROPPED** |
| `stage_instance` | Stage within a workflow instance; two cached statuses | **DROPPED** |
| `stage_comp_status_kind` | `editable`/`locked` | **DROPPED** |
| `stage_exec_status_kind` | `not_started`/`blocked`/`running`/`review`/`complete`/`canceled` | **DROPPED** |
| `stage_comp_event` | Append-only per-stage composition log | **DROPPED** |
| `stage_exec_event` | Append-only per-stage execution log | **DROPPED** |
| `task_instance` | Executable unit; `task_type` + JSON parameters | **DROPPED** — collapsed into `irp_job`, one row per real IRP op |
| `task_status_kind` | `blocked`/`ready`/`running`/`succeeded`/`failed`/`skipped` | **DROPPED** |
| `task_comp_event` | Append-only per-task composition log | **DROPPED** |
| `task_exec_event` | Append-only per-task execution log | **DROPPED** |
| `task_input` | Bound input port → resolved source (typed handle) | **DROPPED** — replaced by name-based coupling (every op resolves inputs live via IRP's own `search_*` at submit time, confirmed against every sequence diagram reviewed in §6.1's discussion) |
| `input_source_kind` | `inventory`/`upstream_output`/`literal_or_reference` | **DROPPED** |
| `task_output` | Produced output handle + lineage | **DROPPED** — replaced by `created_by_irp_job_irp_id` on each entity table (§6.1) |

**Why these 24 and not fewer:** confirmed via `grep` that no application
code under `app/` or `db/` references any of these tables beyond a
nav-manifest label ("Workflows" rail item) and one placeholder template —
this is a pure documentation/design deletion, not a migration with rollback
risk. No table in this list survives in any modified form; every one of
their responsibilities is either absorbed by `irp_job` directly (the
executable unit), by the prerequisite gate in code (§5.4 — computed, not
stored), by name-based coupling (replacing typed handles/ports), or by
`created_by_irp_job_irp_id` on the entity tables (§6.1 — replacing
`task_output` lineage).

**Analyst's explicit instruction: delete.** No partial retention, no
deferred status for any of these 24 — unlike `audit_log`/analysis
templates/Phase A validation (which are deferred, not deleted), this
construct is fully replaced and has nothing worth keeping documented.

**Review note:** each of these 24 tables was individually confirmed with the
analyst in a live pass (six small groups, plus two tables reviewed
individually), not batch-assumed from the summary framing alone — the point
being to make sure nothing worth keeping got discarded along with the group.
None were flagged for reconsideration; all 24 confirmed delete as-is. The
two tables whose *responsibility* (not just their rows) carries forward into
the new design are called out explicitly above: `task_instance` → `irp_job`,
`task_output` → `created_by_irp_job_irp_id` on each entity table.

### Analysis results — **SETTLED**

**Table Name:** `analysis_result_meta` (name kept as-is — analyst confirmed
"fine for now" over the shorter `analysis_result` used inconsistently
elsewhere in the analyst's own notes)
**Purpose:** SQL metadata for one (analysis, perspective_code) result set, retrieved from IRP by the `retrieve_analysis_results` `rwb_job` on analysis `FINISHED`. Row-level ELT/EP/PLT/stats data lives in Parquet only; this table stores summary fields for list views.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `analysis_id` | FK → irp_analysis | — | **RE-POINTED** from `task_instance_id` (table dropped) |
| `rdm_id` | FK → irp_rdm, nullable | The RDM this result set was retrieved/populated from, when sourced via RDM-side API calls; null for the analyst's own analyses | **NEW field** |
| `customer_id` | FK → customer, denorm | — | UNCHANGED |
| `analysis_name` | string | IRP analysis name **at retrieval time** — a deliberate snapshot, not a live lookup; duplicates `irp_analysis.name` on purpose so a later rename doesn't retroactively change what this row says it was called when retrieved | UNCHANGED (clarified as intentional denormalization) |
| `perspective_code` | string | `GR` / `GU` / `RL` | UNCHANGED |
| `aal` | float | Average Annual Loss; from `get_stats()` | UNCHANGED |
| `elt_record_count` | int | Row count; from `get_elt()` response | UNCHANGED |
| `has_plt` | bool | `true` for HD analyses | UNCHANGED |
| `elt_file_path` | string | Relative path to ELT Parquet file | UNCHANGED |
| `ep_file_path` | string | Relative path to EP curve Parquet file | UNCHANGED |
| `plt_file_path` | string, nullable | PLT Parquet file (HD only) | UNCHANGED |
| `stats_file_path` | string | Relative path to stats Parquet file | UNCHANGED |
| `retrieved_at` | datetime | — | UNCHANGED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | UNCHANGED |

**Key decisions:**
- **`analysis_id` re-pointed from `task_instance_id`** — mechanical fix now
  that `task_instance` is dropped; the FK target is the entity the result
  belongs to, `irp_analysis`, not an execution-engine artifact.
- **No `as_of` on this table — deliberately, unlike every `irp_*` entity
  table.** Analyst's own framing, worth preserving verbatim: *"results are
  immutable once the analysis exists, so caching is safe... not a sync
  risk."* This is a different situation from `irp_edm`/`irp_rdm`/etc., which
  need `as_of` because Risk Modeler can keep changing after the app last
  looked (§6.2). A finished analysis result never changes underneath the
  app once retrieved — there is no drift to signal, so no trust-signal
  column is needed.
- **`rdm_id` added — NOT the same question as `irp_analysis.rdm_id` (still
  BLOCKED).** This was the substantive point of this review: the two
  questions look similar but are different in kind. `irp_analysis.rdm_id`
  (still blocked) is about *how a broker-sourced analysis entity itself
  enters the app's data model at all* — an open question about Moody's API
  behavior this session couldn't resolve from source code alone.
  `analysis_result_meta.rdm_id`, by contrast, is about *where this specific
  result-retrieval populated its data from* — the analyst confirmed directly
  that "analysis_result_meta will be populated from rdm using apis," which
  is a plain factual link, not contingent on the still-unresolved question.
  **Settled independently: `analysis_result_meta` gets `rdm_id` now; whether
  `irp_analysis` itself ever gets one stays BLOCKED**, tracked separately.
- **Driving need, recorded for context:** the analyst's stated reason for
  needing this link at all — a unified list of analyses run by the analyst
  *and* analyses shared by the broker, since analysts routinely run
  supplemental analyses on top of what the broker already provided. Whether
  that unified list is best assembled by querying `analysis_result_meta`
  directly (own + broker rows side by side, distinguished by `rdm_id IS NULL`
  vs. not) or by projecting a combined view scoped to a `package` (which
  already links one EDM + one RDM together) is a **UI/query-design
  question, not a schema question** — not resolved here, doesn't block
  anything in this table.

**Table Name:** `result_export`
**Purpose:** An exported result deliverable — Parquet file export or SQL (Loss Repository / RDM) export.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `analysis_result_meta_id` | FK → analysis_result_meta | — | UNCHANGED (name already matches the table's confirmed name) |
| `customer_id` | FK → customer, denorm | — | **NEW field** |
| `delivery_code` | FK → delivery_kind | — | UNCHANGED |
| `location` | string | File path (Parquet export) or SQL ref (**Loss Repository / RDM** export) | UNCHANGED — wording corrected in description only (analyst's notes say "RDM/LOSS export," more accurate than the live doc's "RDM export" alone, given `push_results_to_loss_repo` is a real export destination per the `rwb_job` work-type table); no schema change |
| `inserted_at` | datetime | — | UNCHANGED |
| `inserted_by` | FK → app_user | — | UNCHANGED |

**Key decisions:**
- **`customer_id` denorm added** — analyst's explicit call: "keep customer id
  denorm for RLS." Every other table in this review carries this for
  `apply_scope()`; `result_export` was missing it (only reachable via a join
  through `analysis_result_meta.customer_id`).

**Table Name:** `delivery_kind`
**Purpose:** Kind table for `result_export.delivery_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `file` / `sql` | UNCHANGED |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

### IRP reference cache — **SETTLED**

Populated by the "Sync IRP Metadata" action; the app never writes to these
tables outside of that action.

**Table Name:** `irp_model_profile`
**Purpose:** Cached model profiles (determines DLM vs HD).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `irp_id` | string | IRP's profile ID | UNCHANGED (already used `irp_id`) |
| `name` | string | — | UNCHANGED |
| `software_version_code` | string | Contains `"HD"` → HD profile, else DLM | UNCHANGED |
| `description` | string, nullable | — | UNCHANGED |
| `as_of` | datetime | Stamped by the "Sync IRP Metadata" bulk action | **RENAMED** from `synced_at`, for naming consistency with the `as_of` used across every `irp_*` entity table (§6.2) |
| `inserted_at` / `updated_at` | audit fields | — | UNCHANGED |

**Table Name:** `irp_output_profile`
**Purpose:** Cached output profiles.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `irp_id` | string | — | UNCHANGED |
| `name` | string | — | UNCHANGED |
| `as_of` | datetime | — | **RENAMED** from `synced_at` |
| `inserted_at` / `updated_at` | audit fields | — | UNCHANGED |

**Table Name:** `irp_event_rate_scheme`
**Purpose:** Cached event rate schemes (required for DLM analyses).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `irp_id` | string | — | UNCHANGED |
| `name` | string | — | UNCHANGED |
| `peril_code` | string, nullable | — | UNCHANGED |
| `model_region_code` | string, nullable | — | UNCHANGED |
| `as_of` | datetime | — | **RENAMED** from `synced_at` |
| `inserted_at` / `updated_at` | audit fields | — | UNCHANGED |

**Table Name:** `irp_database_server`
**Purpose:** Cached IRP DataBridge server names.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `name` | string | IRP server name | UNCHANGED — no `irp_id`-style external id at all (server is identified purely by name in this library); not a naming inconsistency, just no such field exists |
| `as_of` | datetime | — | **RENAMED** from `synced_at` |
| `inserted_at` / `updated_at` | audit fields | — | UNCHANGED |

**Table Name:** `irp_tag`
**Purpose:** Cached IRP tags, referenced by `analysis_template_tag.irp_tag_id` (deferred table, §2a).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `irp_id` | string | IRP's tag ID | **RENAMED** from `irp_tag_id`, for consistency with every other `irp_*` table's own-id column |
| `name` | string | — | UNCHANGED |
| `as_of` | datetime | — | **RENAMED** from `synced_at` |
| `inserted_at` / `updated_at` | audit fields | — | UNCHANGED |

**Table Name:** `irp_simulation_set`
**Purpose:** Cached simulation sets.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `irp_id` | string | IRP's simulation set ID | UNCHANGED |
| `name` | string | — | UNCHANGED |
| `description` | string, nullable | — | UNCHANGED |
| `as_of` | datetime | — | **RENAMED** from `synced_at` |
| `inserted_at` / `updated_at` | audit fields | — | UNCHANGED |

**Table Name:** `irp_currency`
**Purpose:** Cached ISO 4217 currencies.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `code` | string | ISO 4217 currency code (e.g. `USD`, `GBP`) | UNCHANGED — natural key is the ISO code itself, not an arbitrary Moody's-assigned id, so no `irp_id` rename applies here |
| `name` | string | — | UNCHANGED |
| `as_of` | datetime | — | **RENAMED** from `synced_at` |
| `inserted_at` / `updated_at` | audit fields | — | UNCHANGED |

**Table Name:** `irp_edm_cache` — **DROPPED**
**Purpose (historical, no longer needed):** EDMs already in IRP, not necessarily created via this app — used for a "skip upload, link an existing EDM" path.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DROPPED |
| `irp_exposure_id` | string | — | DROPPED |
| `name` | string | EDM name in IRP | DROPPED |
| `server_name` | string | — | DROPPED |
| `synced_at` | datetime | — | DROPPED |
| `inserted_at` / `updated_at` | audit fields | — | DROPPED |

**Analyst's reasoning, preserved in full:** *"If EDMs and RDMs are ever
uploaded from outside RWB, we will have them as orphans on Risk Modeler. They
won't be on RWB. Even if we cache them, we would want to store them in
irp_edm and irp_rdm, but that would need to have a submission and package.
Local file association also would be missing. More governance questions than
we can solve in this release of the product. We will defer that decision for
now."* This is a deliberate deferral of a real product question (how does
the app handle EDMs/RDMs that exist in Risk Modeler but were never imported
through this app), not a simplification-for-simplification's-sake drop — the
alternative (caching them properly in `irp_edm`/`irp_rdm`) would require
those tables to tolerate a null `submission_id`/no `package`, which they
currently don't and which raises its own governance questions this release
isn't solving.

**Key decisions for this whole section:**
- **`as_of` naming applied uniformly** — every surviving table's `synced_at`
  renamed to `as_of`, for consistency with the `as_of` convention settled in
  §6.2 for `irp_edm`/`irp_rdm`/`irp_portfolio`/`irp_treaty`/`irp_analysis`.
  Same underlying concept (when was this locally-cached row last confirmed
  against IRP), even though the population mechanism differs (bulk "Sync IRP
  Metadata" action here, vs. per-row automatic-stamp-plus-manual-refresh for
  entity tables) — the name should reflect the concept, not the mechanism.
- **`irp_tag.irp_tag_id` renamed to `irp_id`** — same consistency fix applied
  everywhere else in this review (`irp_portfolio.irp_portfolio_id`,
  `irp_edm.irp_exposure_id`, `irp_treaty.irp_treaty_id`, all → `irp_id`).
- **`irp_database_server.name` and `irp_currency.code` are correctly NOT
  renamed** — neither has an arbitrary Moody's-assigned identifier to rename;
  a server is identified by name, a currency by its ISO code. Flagging this
  explicitly so it doesn't read as an inconsistency left unfixed.
- **`irp_edm_cache` dropped entirely**, per the analyst's explicit decision
  and reasoning above.

### Reference data & parameters — **DROPPED (all 3 tables)**

**Table Name:** `reference_table`
**Purpose:** Named reference list (global).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DROPPED |
| `name` | string, UNIQUE | — | DROPPED |
| `description` | string, nullable | — | DROPPED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | DROPPED |

**Table Name:** `reference_table_row`
**Purpose:** Rows/values in a reference table.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DROPPED |
| `reference_table_id` | FK → reference_table | — | DROPPED |
| `key` | string | — | DROPPED |
| `value` | string | — | DROPPED |
| `version` | int | Versionable for pin-on-use | DROPPED |
| `inserted_at` | datetime | — | DROPPED |
| `inserted_by` | FK → app_user | — | DROPPED |

**Table Name:** `parameter`
**Purpose:** Named parameter value (global).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | DROPPED |
| `name` | string, UNIQUE | — | DROPPED |
| `value` | string | — | DROPPED |
| `version` | int | Versionable | DROPPED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | DROPPED |

**Table-level note:** confirmed none of these three tables reference
`workflow`/`stage`/`task`, EDM/RDM/analysis, or any other construct touched
by this CR — they're generic global config/reference-value infrastructure,
never wired to anything else in the schema (no inbound FKs from any other
table reviewed so far). **Analyst's explicit call: remove, not defer** —
*"We can remove these tables. Will add back if there is ever a need."*
Distinct treatment from `audit_log`/analysis templates/Phase A validation
(marked DEFERRED — documented as-is, paused): this is a full removal from
the schema, to be re-added from scratch if a concrete need arises later,
not a paused-but-preserved design.

### `irp_job` — **SETTLED** (the central table; worked field-by-field, slowly, per analyst's explicit request)

**Table Name:** `irp_job`
**Purpose:** Local record of one IRP async operation (one row per real IRP op — the executable unit that replaces `task_instance`).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `submission_id` | FK → submission, **NOT NULL** | Every job belongs to exactly one submission, always | **NOT NULL** — analyst's explicit call, settled during auth/business-spine review |
| `customer_id` | FK → customer, denorm, **NOT NULL** | — | **NOT NULL** — same call |
| `irp_edm_id` | FK → irp_edm, nullable | Set for EDM creation/import jobs; also set alongside `irp_rdm_id` for RDM creation/import (RDM always has its EDM); also set alongside `irp_portfolio_id` for portfolio creation/operations incl. GeoHaz | **NEW** — §6.1 reversal (typed FK columns restored, replacing the abandoned `irp_job_reference` design) |
| `irp_portfolio_id` | FK → irp_portfolio, nullable | Set for portfolio creation/operations (incl. GeoHaz), alongside `irp_edm_id` | **NEW** — §6.1 reversal |
| `irp_rdm_id` | FK → irp_rdm, nullable | Set for RDM creation/import (alongside `irp_edm_id`) and RDM operations | **NEW, renamed from the scratchpad's original `rdm_id`** — analyst's explicit naming-consistency call, matching `irp_edm_id`/`irp_portfolio_id` |
| `irp_job_type` | — | `edm_import` / `rdm_import` / `geohaz` / `analysis` / `grouping` / `export` — selects the single-status-check poll method per the semantic job-type table (PRD draft) | **KIND TABLE** (`irp_job_type_kind`) — analyst's explicit call, **reversing** the Article 3 plain-string carve-out for this field specifically (contrast with `irp_job.status`, kept plain — see below) |
| `irp_id` | string, nullable until submission succeeds | Moody's own integer job id, stored as string | **RENAMED** from `external_ref`, per the `irp_id`-everywhere convention applied throughout this review |
| `status` | — | RM-mirrored (`PENDING`/`QUEUED`/`RUNNING`/`FINISHED`/`FAILED`/`CANCEL_REQUESTED`/`CANCELING`/`CANCELED`, one-L spellings) plus app-local (`UNSUBMITTED`/`SUBMITTING`/`BLOCKED`/`SUBMISSION FAILED`) | **RENAMED** from `mirrored_status`; **stays plain string**, Article 3 carve-out applies (mirrors an external system's vocabulary the app doesn't control) — this is the genuine exception to the "always kind table" default, confirmed intentional by contrast with `irp_job_type` just above |
| `last_submission_payload` | JSON/text | What was sent to RM on the most recent submit attempt | **NEW** — §6.3, replaces the abandoned `irp_job_submission` satellite table |
| `last_submission_response` | JSON/text | RM's response to that submit, **as a full object** (e.g. for grouping: includes `included_items`/`skipped_items`, not just a bare success/fail) | **NEW** — §6.3 |
| `last_completion_result` | JSON/text | The terminal poll response — **covers both `FINISHED` and `FAILED` outcomes**, since RM's poll endpoint (`get_analysis_job`, `get_import_job`, etc.) returns the identical response shape either way, just with a different `status` value inside. This column is the single source of truth for "what did the terminal poll say," regardless of which way the job ended. | **NEW** — §6.3, replaces both the abandoned `irp_job_completion` **and** `irp_job_failure` satellite tables |
| `submission_attempt_count` | int, default 0 | Incremented per submit attempt | UNCHANGED |
| `submitted_at` | datetime, nullable | — | **NEW** — explicitly listed in the analyst's PRD-update draft; not present in the live schema today |
| `completed_at` | datetime, nullable | — | **NEW** — same |
| `last_tracked_at` | datetime, nullable | Null until first poll | **RENAMED** from `last_synced_at`, per the analyst's PRD-update draft |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | UNCHANGED |

**Removed from the live schema, with reasoning:**
- **`resource_uri` → moved off this table entirely, into a new child table
  `irp_job_resource`** (`irp_job_id` FK, `resource_type` FK →
  `irp_job_resource_type_kind`, `resource_uri`). **Tagged for review, not
  fully closed** — researched directly against the installed
  `irp_integration` source (`analysis.py` lines 217, 325–326): the submit
  payload for an analysis job is literally `{"resourceUri": portfolio_uri,
  "resourceType": "portfolio", ...}` — i.e. RM's own API already treats this
  as a `(type, value)` pair, not a bare string, which is what motivated the
  restructure. Confirmed against `view_results.md`: RM's completion response
  never returns this value back, so it must be captured at submission time
  or it's unrecoverable without a separate search call — same requirement as
  before, just relocated. **Open question, explicitly not resolved:** is
  this always exactly one row per job (only `resourceType: "portfolio"`
  exists today), or genuinely designed for potential multiplicity (a future
  job type submitting more than one resource, or a different
  `resourceType`)? Analyst's instruction: proceed on this understanding,
  note it, and flag for a future review pass rather than settle it now.
- **`retry_locked_until` → removed entirely, no replacement.** Analyst's
  explicit decision: *"we will make submission retry a single thread batch
  job. submission is not a long running process so no need for dramatiq
  workers."* This column existed solely to let multiple **concurrent**
  `submission_retry` actor instances atomically claim a row without
  double-processing it (`UPDATE ... WHERE retry_locked_until <
  GETUTCDATE()`, pushing the lock forward on claim). A single-threaded batch
  process never races against itself, so the claim/lock mechanism has
  nothing to protect against — retry eligibility becomes a plain query
  (e.g. `status = 'SUBMISSION FAILED' AND submission_attempt_count < max`),
  no lock column needed.
- **`last_failure_result` → removed, never added.** Reasoned through
  directly: RM's poll response has one shape regardless of outcome (no
  distinct "failure payload" format), so whatever `last_completion_result`
  captures already includes the failure case whenever `status = 'FAILED'`.
  A separate column would only ever duplicate data already sitting in
  `last_completion_result`. The one place a *genuinely distinct* failure
  concept exists — a submission that never reached RM at all
  (`SUBMISSION FAILED`) — is already covered by `last_submission_response`,
  a different column for a different reason. No gap left uncovered.

**Key decisions, summarized:**
- **`irp_job_type` is a kind table; `irp_job.status` is not.** These look
  like a contradiction (both are external-ish vocabularies) but the analyst
  drew the line deliberately: `status` keeps the Article 3 carve-out (RM can
  add a new status value at any time, and a kind table would need a seed
  migration to avoid crashing the poller on an unrecognized value); the set
  of job *types* the app poller needs to know how to dispatch is closed and
  app-defined (six today, changes only when the app itself adds support for
  a new op), so it gets the "always kind table" default like everything
  else in this review.
- **`submission_id`/`customer_id` non-nullable** — settled earlier in this
  review (auth & business-spine session), reconfirmed here as it lands on
  this table for the first time.
- **Everything from §6.3 (the four `last_*` columns) is now down to two**
  (`last_completion_result` absorbs what would have been
  `last_failure_result`) — the simplification the analyst was originally
  aiming for ("without overburdening the system and bloating the data
  footprint") went one step further than the original §6.3 settlement once
  actually walked field-by-field.

**Table Name:** `irp_job_type_kind` — **NEW**
**Purpose:** Kind table for `irp_job.irp_job_type`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `edm_import` / `rdm_import` / `geohaz` / `analysis` / `grouping` / `export` | NEW |
| `label` | string | — | NEW |
| `sort_order` | int | — | NEW |
| `inserted_at` | datetime | — | NEW |

**Table Name:** `irp_job_resource` — **NEW**
**Purpose:** The resource(s) submitted alongside an `irp_job` (e.g. the portfolio URI an analysis job needs echoed back for later result reads). Replaces the single `irp_job.resource_uri` column with a typed `(resource_type, resource_uri)` pair, matching the shape RM's own submit payload already uses.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | NEW |
| `irp_job_id` | FK → irp_job | — | NEW |
| `resource_type` | FK → irp_job_resource_type_kind | — | NEW |
| `resource_uri` | string | The resource's URI as returned by the relevant `search_*` call at submission time (e.g. a portfolio's `uri`); must be captured at submit time since RM's completion response does not return it | NEW |
| `inserted_at` | datetime | — | NEW |

**Table-level note — tagged for future review, not fully closed:**
confirmed against installed `irp_integration` source
(`analysis.py` lines 217, 325–326) that the analysis-job submit payload is
literally `{"resourceUri": portfolio_uri, "resourceType": "portfolio", ...}`
— RM's own API already models this as a typed pair, which is what motivated
pulling it into its own table rather than keeping a single opaque string
column. **Open, not decided:** is this always exactly one row per job (only
`resourceType: "portfolio"` exists in any flow reviewed so far), or is the
table genuinely meant to support more than one resource per job in the
future? Proceed on the current understanding; revisit once more job types
are designed.

**Table Name:** `irp_job_resource_type_kind` — **NEW**
**Purpose:** Kind table for `irp_job_resource.resource_type`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `portfolio` (only value confirmed against source so far) | NEW |
| `label` | string | — | NEW |
| `sort_order` | int | — | NEW |
| `inserted_at` | datetime | — | NEW |

### `irp_job_status_event` — **DROPPED (never built)**

**Table Name:** `irp_job_status_event`
**Purpose (as originally proposed, not built):** Event-sourced log of `irp_job.status` transitions, mirroring the existing `submission_status_event` pattern (§1 of this catalog).
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| *(would have mirrored `submission_status_event`: `id`, `irp_job_id` FK, `status_code`, `at`, `inserted_by`)* | — | — | **DROPPED — never built, not a removal of an existing table** |

**Analyst's explicit call:** *"we can drop this table. last tracked at on the
irp_job table accomplishes the 'heartbeat' purpose."* The main functional
need this table would have served — proving a job is still actively being
tracked/polled — is already covered by `irp_job.last_tracked_at`. What
remains (a full per-transition audit trail of every status change) is
exactly the kind of general auditing capability already deferred for the
whole application (`audit_log`/`user_action`, settled DEFERRED in the
auth/business-spine review) — building a one-off event log for this single
table while auditing everywhere else stays deferred would be inconsistent.
Note this is **not the same construct as `irp_job_status_kind`** (whether
`irp_job.status` itself should be a kind table) — that question was already
resolved separately during the `irp_job` review (kept as a plain string,
Article 3 carve-out); this is about the *event log*, which never gets built
at all.

### `rwb_job` — **SETTLED** (worked field-by-field, per analyst's explicit request for a slow review)

**Table Name:** `rwb_job`
**Purpose:** General app-side queued-work row — work **this app itself executes** (a Dramatiq worker doing the work in-process), as distinct from `irp_job` which tracks a job running remotely in Moody's SaaS. This distinction, drawn explicitly by the analyst, is what drives most of this table's field differences from `irp_job`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `id` | uniqueidentifier PK | — | UNCHANGED |
| `requestor_type` | FK → rwb_job_requestor_type_kind, **NOT NULL** | Replaces `origin`; discriminates how to interpret `requestor_id` (e.g. an `irp_job` completion, an analyst-initiated action, or another `rwb_job` chaining to this one) | **NEW, replaces `origin`** |
| `requestor_id` | uniqueidentifier, **NOT NULL** | The id of whatever triggered this job — an `irp_job.id`, an analyst-action id, or a parent `rwb_job.id` for the chained case. No DB-level FK constraint (the target table varies by `requestor_type`); integrity enforced by app code. May pivot to `string` in future if a `requestor_type` needs a non-UUID id. | **NEW, replaces `irp_job_id` entirely** |
| `rwb_job_type` | FK → rwb_job_type_kind, **NOT NULL** | — | **RENAMED** from `work_type`; **kind table**, reversing the plain-string treatment (same direction as `irp_job_type`) |
| `status_code` | FK → rwb_job_status_kind | — | UNCHANGED |
| `customer_id` | FK → customer, denorm | For `apply_scope()` | UNCHANGED |
| `input_data` | JSON | What the job was asked to do — the work order handed to the worker | **RENAMED** from `payload` |
| `output_data` | JSON, nullable | What the job produced; populated on success | **NEW** |
| `error_detail` | string, nullable | Set on failure | UNCHANGED |
| `attempt_count` | int, default 0 | Incremented on each Dramatiq delivery | UNCHANGED |
| `claimed_by` | string, nullable | Worker id; observability only | UNCHANGED |
| `submitted_at` | datetime, nullable | — | **NEW** — analyst's explicit call, "to be consistent with irp_job" |
| `completed_at` | datetime, nullable | — | UNCHANGED |
| `inserted_at` / `updated_at` / `inserted_by` / `updated_by` | audit fields | — | UNCHANGED |

**Removed from the live schema:**
- **`request_key`** — the single VARCHAR idempotency key (computed from
  three different string-templated schemes depending on `origin`) is
  **replaced entirely** by a genuine composite `UNIQUE(requestor_type,
  requestor_id, rwb_job_type)` constraint. Three concrete, indexable,
  joinable columns replace one opaque templated string.
- **`irp_job_id`** — removed, no FK to `irp_job` at all. This goes further
  than CR-001's own decision (which made `irp_job_id` a *nullable* FK, "soft
  lineage"); the analyst's explicit instruction here is full decoupling —
  `requestor_id` covers the same lineage need generically, without a
  dedicated column that only makes sense for one `requestor_type`.
- **`origin`** — replaced by `requestor_type`, which carries the same
  discriminator meaning while also being the actual typed pointer, not just
  an observability label.
- **`payload`** — renamed to `input_data` (same concept).

**Key decisions:**
- **Full decoupling from `irp_job`, explicitly beyond CR-001's original
  nullable-FK compromise.** Analyst: *"No FK for job_id."* The generalized
  `requestor_type`/`requestor_id` pair covers `irp_job` lineage as one case
  among three (`irp_job` completion, analyst request, chained `rwb_job`), not
  as a privileged first-class relationship.
- **Composite natural key replaces the derived string key.** Analyst: *"These
  along with rwb_job_type - all three non nullable will serve as the
  composite key we need for 'request_key'."* Confirmed mapping: for the
  `chained` case, `requestor_type='rwb_job'` and `requestor_id` = the parent
  `rwb_job.id` — chaining becomes self-referential through the same general
  mechanism rather than a distinct `chain:{parent_id}:{work_type}` string
  template.
- **`requestor_type` is a kind table — "necessary for governance"**
  (analyst's own words), same "always kind table" default applied
  throughout this review, here with an explicit stated reason beyond the
  general rule.
- **`input_data`/`output_data`/`error_detail` are NOT modeled after
  `irp_job`'s `last_submission_payload`/`last_submission_response`/
  `last_completion_result`.** Analyst's explicit distinction: *"This is
  different from IRP Job. In IRP Job, we are tracking IRP SaaS job. Here we
  are running the job."* `irp_job` needs submission/response/poll-result
  columns because it's a remote system's job being tracked from outside;
  `rwb_job` is work the app's own worker executes in-process, so the natural
  shape is simply input → output (+ error on failure), not a
  submit/poll/complete lifecycle.
- **No `as_of`** — analyst's explicit call. `as_of` (§6.2) exists to signal
  drift between a local cache and external Risk Modeler state; `rwb_job`
  isn't a cache of anything external, so the concept doesn't apply.
- **Retry design is NOT copied from `irp_job`'s (removed) `retry_locked_until`
  mechanism** — no equivalent column proposed or discussed for `rwb_job`;
  Dramatiq's own retry/redelivery handles `rwb_job` failures per CR-001,
  which was never in question during this review.

**Table Name:** `rwb_job_requestor_type_kind` — **NEW**
**Purpose:** Kind table for `rwb_job.requestor_type`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | e.g. `irp_job` / `analyst_request` / `rwb_job` (exact seed values not yet finalized — mirrors the three `origin` cases this replaces) | NEW |
| `label` | string | — | NEW |
| `sort_order` | int | — | NEW |
| `inserted_at` | datetime | — | NEW |

**Table Name:** `rwb_job_type_kind` — **NEW**
**Purpose:** Kind table for `rwb_job.rwb_job_type`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | e.g. `retrieve_analysis_results` / `push_results_to_loss_repo` / `push_rdm_to_loss_repo` / `notify_analyst` / `download_export_file` / `edm_upload` / `rdm_upload` / `edm_delete` / `rdm_delete` (the existing `work_type` vocabulary, per DATA_MODEL §8's checklist) | NEW — was previously a plain string, "document in worker registry, not in the DB" |
| `label` | string | — | NEW |
| `sort_order` | int | — | NEW |
| `inserted_at` | datetime | — | NEW |

**Table Name:** `rwb_job_heartbeat` — **UNCHANGED**
**Purpose:** Per-job progress heartbeat (CR-001). Confirmed not revisited in this review — no discussion touched this table.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `rwb_job_id` | FK → rwb_job, UNIQUE | One row per job, upserted | UNCHANGED |
| `worker_id` | string | Worker currently processing this job | UNCHANGED |
| `heartbeat_at` | datetime | Stamped every `RWB_HEARTBEAT_INTERVAL_SECS` by the daemon thread | UNCHANGED |

**Table Name:** `rwb_job_status_kind` — **UNCHANGED**
**Purpose:** Kind table for `rwb_job.status_code`.
| Field | Definition | Description | Change (If Any) With CR-002 |
|---|---|---|---|
| `code` | string PK | `pending` / `running` / `succeeded` / `failed` | UNCHANGED |
| `label` | string | — | UNCHANGED |
| `sort_order` | int | — | UNCHANGED |
| `inserted_at` | datetime | — | UNCHANGED |

---

## 3. The pivot, stated plainly

**Before (current PRD/DATA_MODEL, pre-CR-002):**
```
Workflow (pins a versioned, manifest-projected definition)
  └─ Stage instance (8 fixed stages, two status streams: composition + execution)
       └─ Task instance (task_type + JSON parameters, typed input/output ports)
            └─ Handle (typed: edm/rdm/analysis/group, propagated + compatibility-checked)
                 └─ irp_job (the actual async IRP call)
                 └─ rwb_job (post-completion app-side work, per CR-001)
```

**After (this CR's proposal):**
```
Submission (workbench-only; wraps EDM/RDM entities)
  └─ irp_job (one row per IRP async op; job_type discriminates the poll method)
       └─ rwb_job (post-completion app-side work — UNCHANGED from CR-001, decoupled, no FK)
  └─ Entity rows (irp_edm, irp_rdm, irp_portfolio, irp_analysis, irp_treaty) — produced/updated by jobs
  └─ user_action (audit — every action, including synchronous ones)
```

**What disappears:** `workflow`, `workflow_definition`, `workflow_status_event`, `stage_instance`, `stage_comp_event`, `stage_exec_event`, `task_instance`, `task_comp_event`, `task_exec_event`, `task_input`, `task_output`, `task_template`, `port_template`, `handle_type_kind`, `workflow_type_kind`, `stage_kind`, `stage_mode_kind`, plus their kind tables and the projection/consistency-check machinery in Articles 1–2 of the constitution.

**What replaces "what's next":** a static prerequisite-gate table (already in your PRD-update draft, reproduced in §5.4) — code, not stored state. "What can the analyst do right now" is computed live from entity existence + job terminal status, not read off a `stage_instance.exec_status_code`.

**What replaces typed handles:** name-based coupling. Every op resolves its inputs by calling IRP's own `search_*` functions at submit time (`search_edms`, `search_portfolios`, `search_analyses`, `search_treaties`) — which the sequence diagrams show the app already doing on every submit path regardless of what the local metamodel says. The typed-handle registry was tracking something IRP re-validates anyway.

---

## 4. Inventory — what's deleted, what survives, what's new

### 4.1 Deleted outright (PRD §12–14, DATA_MODEL §6–8)

| Construct | Why it goes |
|---|---|
| `workflow`, `workflow_definition`, `workflow_type_kind` | No authored process topology to version-pin. |
| `stage_instance`, `stage_kind`, `stage_mode_kind`, `stage_comp_event`, `stage_exec_event` | No stage machine; sequencing is the prerequisite gate (§5.4), computed not stored. |
| `task_instance`, `task_template`, `task_comp_event`, `task_exec_event` | Collapsed into `irp_job` — one row per real IRP op is the executable unit now. |
| `task_input`, `task_output`, `port_template`, `handle_type_kind` | Name-based coupling replaces typed handles; IRP's own `search_*` is the resolution mechanism, already used on every submit path per the sequence diagrams. |
| The manifest-projection subsystem (content-hash consistency check, version-retention) | Only existed to serve the workflow-definition manifest. Nothing left to project. |
| Article 1 (Manifest-Driven Extensibility) and Article 2 (Manifest Is Canonical) as currently written | Both are framed entirely around workflow-definition/type-registry manifests that no longer exist. See §7. |
| Article 5 (Generic Stage Review) | No stages. |

### 4.2 Survives, mostly unchanged

- `submission`, `program`, `customer`, `app_user`, RLS/scoping tables (DATA_MODEL §1) — the business spine is untouched by this CR, except `submission` itself (`crm_id` added, `authoring_status` confirmed dropped — see §2a's auth & business spine entry).
- `file_artifact`, `submission_directory`, `discrepancy`, `ignore_rule` (DATA_MODEL §2) — file inventory is orthogonal to the workflow layer (one behavioral change: `discrepancy`'s escalation rule now keys off `package` membership instead of workflow-reference — see §2a).
- `package` (DATA_MODEL §3a) — unchanged at the field level (only FK targets renamed); its own pre-existing open question (how a package's `edm_upload` `rwb_job` chains to `rdm_upload` across the IRP-job/RWB-job boundary) is **still open, not resolved by this CR** — see the `package` entry in §2a for a possible direction worth raising in a future session, given `rwb_job`'s own redesign (`requestor_type`/`requestor_id`) may make it easier to solve.
- CR-001's `rwb_job` + `rwb_job_heartbeat` + reconciler design — explicitly assumed and built on; `rwb_job` itself gets substantial field changes in this CR (see §2a), but the heartbeat/reconciler mechanism is untouched.
- Analysis templates, template suites, Phase A validation — marked DEFERRED (not workflow-related; see §2a), not "survives unchanged."
- Analysis results tables, IRP reference cache — see §2a for the field-level changes both groups get (FK re-pointing, `as_of`/`irp_id` renames).

### 4.3 New — final list (see §2a for full field-level detail on every table named here)

- `irp_edm`, `irp_rdm`, `irp_portfolio`, `irp_analysis` (renamed from `edm`/`rdm`/`portfolio`/`analysis`, with modifications), plus new `irp_treaty`.
- `irp_job` (redesigned — `irp_edm_id`/`irp_portfolio_id`/`irp_rdm_id` entity-lineage FKs; `irp_job_type` is a **kind table**, `status` stays a **plain string** per Article 3's carve-out — these two were resolved to different treatments deliberately, not left as a contradiction; `SUBMISSION FAILED` replacing bare `ERROR`).
- `irp_job_type_kind`, `irp_job_resource` + `irp_job_resource_type_kind` (the last two replace what would have been a single `resource_uri` column, restructured to match RM's own `(resourceType, resourceUri)` submit-payload shape).
- `irp_job.last_submission_payload` / `last_submission_response` / `last_completion_result` — three columns on `irp_job` itself, not separate satellite tables. No `irp_job_status_event`, `irp_job_validation`, `irp_job_submission`, `irp_job_completion`, or `irp_job_failure` tables — all considered and dropped in favor of this simpler shape.
- `rwb_job_type_kind`, `rwb_job_requestor_type_kind` — `rwb_job` itself decoupled from `irp_job` (`requestor_type`/`requestor_id` replace `origin`/`irp_job_id`); `input_data`/`output_data`/`error_detail` columns directly on `rwb_job`, no separate satellite tables there either.
- No `irp_job_reference` table. A key-value reference-table design was considered, then explicitly reversed in favor of typed FK columns directly on `irp_job` — see §2a's `irp_job` entry for the full reasoning.

---

## 5. The new spine (as sketched in your PRD-update draft)

Reproduced here for continuity — this is what you already wrote, organized for review rather than re-derived:

```
Submission            broker package (Name + CRM ID); assigned analyst; WORKBENCH-only concept
  ├──< Edm / Rdm      multiple (EDM + RDM) sets per submission; RDM paired to its EDM
  │       └── work is anchored to an EDM (portfolios / analyses / groups / treaties belong to one EDM)
  ├──< Job    one IRP operation (async-polled or heavy-deferred); resubmit lineage
  ├──< {Portfolio, Analysis, Group, Treaty}   entity artifacts produced by ops (a Group IS an Analysis)
  └──< UserAction     audit: every action, incl. synchronous ones
```

### 5.1 Persistence tiers (the governing principle)

> **A construct earns a table only if it must persist after the HTTP response returns.**

- **Entity** — EDM/RDM/Portfolio/Analysis/Treaty: a durable artifact.
- **`irp_job`** — must be tracked after the response returns: async IRP poll, or heavy-deferred (e.g. large S3 upload inside a synchronous submit call, per the EDM-upload sequence diagram's "Heavy" classification).
- **`rwb_job`** — app-side post-terminal / analyst-requested / chained work (CR-001, redesigned in this CR — see §2a).
- **Audit** — *deferred, not built.* `user_action` appears in the spine sketch above as an aspirational design tier, but full auditing (this table and `audit_log`) is explicitly out of scope for this CR — see §2a's auth & business-spine entry.

Synchronous single ops (create-subportfolio, treaty CRUD) create **no Job** — confirmed directly by the `create_subportfolio.md` and `treaty_view_edit.md` sequence diagrams, both classified "Sync" in the README table, both persisting the entity in-request with no job row.

### 5.2 What a Group is

A group is an `irp_analysis` row with `is_group=true` — not a separate entity. Confirmed by `grouping.md`: RM's own model treats a group as `isGroup` on an analysis object, viewed/exported identically to a non-group analysis. No separate `group` table.

### 5.3 `irp_job.status` vocabulary

RM-mirrored verbatim (`PENDING → QUEUED → RUNNING → FINISHED`; `FAILED`; the `CANCEL_REQUESTED/CANCELING/CANCELED` one-L lane) plus app-local states RM doesn't have: `UNSUBMITTED`, `SUBMITTING`, `BLOCKED` (a prerequisite failed — the only "needs attention" pre-submit state), `SUBMISSION FAILED` (never reached RM, no `irp_id`). This is a straight rename/rationalization of the existing `mirrored_status` vocabulary in DATA_MODEL §8 — `ERROR` is retired in favor of `SUBMISSION FAILED` since (per your note) there should not be a bare `ERROR` status; every failure needs to say *which side* failed.

### 5.4 The prerequisite gate (replaces the stage machine)

| Op | Enabled once these exist / are `FINISHED` |
|---|---|
| EDM import | server exists; EDM name not already in RM |
| RDM import | its EDM imported (`FINISHED`) |
| Create subportfolio | EDM + ≥1 portfolio exist |
| GeoHaz | EDM + portfolio exist |
| Treaty create/edit | EDM exists |
| Analysis | EDM + portfolio (+ named treaties) exist |
| Grouping | member analyses/groups exist (`FINISHED`) |
| Export → Loss Repo | analysis/group exists (`FINISHED`) |

This table is code (a lookup + existence check), not a stored `stage_kind.sort_order`. **Cross-check against the sequence diagrams:** this matches every granular flow's stated "Pre-requisites" section almost verbatim — which is good evidence the gate table isn't inventing new rules, just centralizing what each flow already independently documents.

Mechanical follow-up auto-fires (EDM→RDM, since a broker package is one intent); anything requiring judgment (picking analysis settings) waits for a click. This distinction (auto vs. click-gated) needs to be **made explicit per op**, not left as two examples, when this table is applied to the PRD — a follow-up task for the fold-back pass, not resolved in this review.

---

## 6. Open design area — **RESOLVED during this review; see §2a**

This section originally posed three open questions (the reference-shape
question for what an `irp_job` touches, `as_of` entity-cache staleness, and
the submission/completion/failure detail-table split) as options with
tradeoffs, to be worked through live with the practice lead. That
conversation happened; all three are now decided. Rather than leave the
original options-analysis sitting here alongside the actual answer (which
would read as an unresolved question to anyone skimming top-down), each
resolution is stated once, in place, in **§2a**:

- **Reference shape** — resolved in the `irp_job` entry (§2a): typed nullable
  FK columns directly on `irp_job` (`irp_edm_id`, `irp_portfolio_id`,
  `irp_rdm_id`), populated per job type. An intermediate design (a separate
  `irp_job_reference` table) was tried and explicitly reversed — kept in
  `CR_02_DISCUSSION_SCRATCHPAD.md` as a record of that reversal, not repeated
  here.
- **`as_of` staleness** — resolved in the "IRP reference cache" and entity
  table entries (§2a): a pure UI trust signal, applied uniformly to every
  `irp_*` table, stamped automatically on app-driven writes plus a manual
  "Sync"/"Refresh" action. Carries no weight on the submit path.
- **Submission/completion/failure detail tables** — resolved in the `irp_job`
  entry (§2a): no separate satellite tables at all. Three `last_*` columns
  directly on `irp_job` (`last_submission_payload`, `last_submission_response`,
  `last_completion_result` — a fourth, `last_failure_result`, was considered
  and dropped as redundant with `last_completion_result`), each holding only
  the latest value. `rwb_job` gets an analogous but distinct treatment
  (`input_data`/`output_data`/`error_detail` — see the `rwb_job` entry in
  §2a), since it tracks work the app itself executes, not a remote SaaS job.

---

## 7. Constitution cleanup this pivot forces

You flagged that design details "leaked into the constitution." Confirmed — Articles 1, 2, and 5 are not general architectural principles, they're the workflow-engine's own implementation spec promoted to constitutional status. Once §3's pivot is approved, these need rewriting, not just pruning:

| Article | Current framing | Problem once workflow is gone |
|---|---|---|
| **Article 1** — Manifest-Driven Extensibility | "the navigation manifest, the workflow-definition manifest, and the type/port registry" | Two of the three named manifests no longer exist. The nav manifest is legitimately a general principle (worth keeping); workflow-definition and type/port registry are gone. Needs rewriting to state the *general* principle (config-as-versioned-manifest, one-place-to-change) without naming defunct constructs, or needs demoting from "Article" to a note if the nav manifest alone doesn't carry constitutional weight on its own. |
| **Article 2** — Manifest Is Canonical; DB Is Generated Projection | Entirely about `workflow_definition`/`definition_stage`/`task_template`/`port_template` projection + content-hash consistency check | No projected tables left once workflow goes (confirm: does anything else in the data model use the projection pattern? A scan of DATA_MODEL.md's other sections doesn't show one). Likely deletable outright, not just edited — unless another future manifest wants this pattern, in which case it should be rewritten generically rather than referencing dead table names. |
| **Article 4** — Status Is Event-Sourced with a Cached Current | "submissions, workflows, stages, and tasks" | List needs to become "submissions and irp_job" (rwb_job's event-sourcing status is already unclear — it's plain `UPDATE` per CR-001 §3 item 10, not event-sourced — worth confirming that's intentional and stating it explicitly here rather than leaving Article 4 to imply rwb_job should be event-sourced too). |
| **Article 5** — Generic Stage Review (No HITL Stage Type) | Entirely about `stage_instance` execution status lifecycle and the review gate | No stages. Deletable outright. If the underlying concept ("mechanical follow-up auto-fires, judgment waits for a click," §5.4 above) deserves constitutional status, it should be restated in those terms, not as a stage-review mechanism. |
| **Article 10** — SQL Table Is the Queue | References "IRP already queues/executes" and the reclaim-stuck sweep | Mostly still applies to `irp_job`/`rwb_job` as-is; check wording doesn't implicitly assume `task_instance` was "the queue" anywhere — a quick grep-and-reread, not a rewrite. |

**Also re-check Article 3's carve-out table** (external-status-mirror columns) — it currently lists `task_instance.task_type`, `edm.status`, `rdm.status`. Once those tables are renamed/removed, the carve-out list needs updating to `irp_job.job_type` (already listed), `irp_job.status` (rename of `mirrored_status`), and whatever `irp_edm.status`/`irp_rdm.status` become — **and this is exactly where §4.3's flagged contradiction (new `irp_job_type_kind`/`irp_job_status_kind` tables vs. the existing plain-string carve-out) needs to be resolved, since Article 3 is the article that would have to change to accommodate either answer.**

This is a MAJOR version bump territory (Article removals/redefinitions), not a MINOR/PATCH — per the constitution's own Governance section.

---

## 8. Outstanding items for the fold-back pass

The full table-by-table review this section originally planned to organize
is **complete** — every table is now settled in §2a. Two genuinely unresolved
threads surfaced during that review that don't have an answer anywhere in
this document yet; both should be settled before or during the fold-back
edit to `PRD.md`/`DATA_MODEL.md`, not silently assumed:

1. **`irp_rdm.submission_id` when `edm_id` is null.** §2a's `irp_rdm` entry
   adds `edm_id` (nullable, "null for a standalone broker RDM") but never
   explicitly addresses whether `submission_id` stays non-nullable
   regardless — i.e., is a standalone broker RDM still always scoped to a
   submission, or can `irp_rdm` exist independent of any submission too?
   Needs a yes/no before the fold-back edit.
2. **Auth-specific audit trail, given `audit_log` is deferred.** Current PRD
   §5.1.6 states every authenticated state-changing action inserts an
   `audit_log` row. With `audit_log` deferred (§2a, auth & business-spine
   entry) and `user_action` never built either, does auth logging lose its
   trail entirely for now, or does it need its own narrow, separately-scoped
   mechanism carved out from the general deferral? Not yet asked.

Everything else originally tracked here (the full renamed-table list,
`irp_job`/`rwb_job` design, dropped-table confirmations, constitution edits)
is answered in §2a and §7 respectively — no remaining checklist beyond the
two items above.

---

## 9. Inconsistencies found while cross-referencing (flag, don't fix)

### 9.1 `mvp-scope.md` and `execution-design.md` don't exist in the repo

Both are referenced repeatedly — by `docs/sequence_diagrams/README.md` (`../mvp-scope.md §2/§3/§4`, `../execution-design.md`), and by your PRD-update draft text ("Create / upgrade / delete are EDM entity-management operations, not the MVP analysis spine (`mvp-scope.md §1`)"). A repo-wide search found neither file. Either they exist outside this repo (a separate `irp-workbench/` reference repo, per PRD.md's own "Source of domain truth: `irp-workbench/`" line), were never committed here, or the filenames drifted. **This needs to be resolved before this CR is finalized** — several of this CR's own claims (e.g. what's "MVP spine" vs. entity-management-out-of-scope) are only as good as that document, which nobody reviewing this CR can currently see in-repo.

### 9.2 New kind tables vs. the existing plain-string carve-out — **RESOLVED** (this section originally read as an open question; kept below for context, resolution stated first)

**Resolved during the `irp_job` review (§2a catalog, above), not left open.**
`irp_job_type` **is** a kind table (`irp_job_type_kind`) — the analyst's
explicit call. `irp_job.status` **stays a plain string** — the Article 3
carve-out this section worried about still applies, deliberately, to
`status` specifically. The line the analyst drew: RM can add a new *status*
value at any time (external, open-ended vocabulary — the exact case Article
3's carve-out exists for), whereas the set of job *types* the app poller
dispatches on is closed and app-defined (six today, changes only when the
app itself adds support for a new op) — so it gets the "always kind table"
default applied throughout this review instead. Not a contradiction once
the line is drawn this way; the original concern below was legitimate to
raise, just resolved in favor of splitting the two fields rather than
treating them identically.

*(Original flag, for context — no longer an open question):* Your
PRD-update text said: *"New table: `irp_job_type_kind` / New table:
`irp_job_status_kind`"* — but also kept the framing that
`irp_job.status`/`irp_job_type` are "mirrored-from-RM or app-local... plain
string." Constitution Article 3 currently carves these exact columns out of
the kind-table rule specifically because a kind table would need a seed
migration every time RM adds a status, which would crash the poller on
unrecognized values. This needed an explicit decision, not a table name —
see the resolution above.

### 9.3 Grouping/export jobs reference multiple analyses — **RESOLVED, and now moot** (originally read as open; resolution stated first)

**No longer a live concern.** The `irp_job` design settled in this review
does **not** carry an `irp_analysis_id` column at all — analysis lineage
was deliberately dropped from the job side entirely (§6.1's reversal:
"not capturing consumptions is fine for now"). A grouping job's member
analyses are recoverable from `irp_job.last_submission_payload` (the
requested `analysis_names`) and `irp_job.last_submission_response`
(`included_items`/`skipped_items`, confirmed sufficient by the analyst) —
not via a normalized FK relationship at all. The original problem this
section raised (a single nullable FK can't hold N member analyses) doesn't
need solving because the design no longer attempts to hold *any* member
reference on the job row — see §6.1 in the discussion scratchpad and the
`irp_job` entry in §2a for the full resolution path (which passed through
an intermediate `irp_job_reference` design, itself later abandoned, before
landing here).

*(Original flag, for context — no longer an open question):* Your draft's
`irp_job` columns included a single nullable `irp_analysis_id FK`. But
`grouping.md`/`export_to_loss_repo.md` describe jobs referencing N analyses
at once. A single nullable FK can't represent that — this needed resolving
as part of `irp_job` design, not deferred. See the resolution above.

### 9.4 `irp_job.rdm_id` naming — **RESOLVED**

Renamed to `irp_rdm_id` during the `irp_job` review (§2a catalog, above),
for consistency with `irp_edm_id`/`irp_portfolio_id`. Confirmed by the
analyst directly ("irp_rdm_id it is").

### 9.5 PRD §12's heading typo

Your update text: `## 12. Feature: Workflow model ===>>>> ## 12. Feature: Work model — Submission → EDM/RDM → Job`. Read as "replace this heading with that heading" — noted here so the eventual PRD edit doesn't literally paste the arrow markup.

### 9.6 File-artifact naming: `source_artifact_id` → `file_artifact_id` is a global rename, not scoped to one table

Your draft says "Rename source_artifact_id - file_artifact_id" under the IRP entity section, but `source_artifact_id` currently appears on **both** `edm` and `rdm` (DATA_MODEL §3). **Resolved in §2a: applied to both** — `irp_edm.file_artifact_id` and `irp_rdm.file_artifact_id` are both renamed, confirmed in their respective §2a entries.

---

## 10. Explicitly out of scope for this CR

- **CR-001's heartbeat/reconciler mechanism** (AOF durability, stale-job recovery) — untouched. Note: `rwb_job`'s own columns *were* substantially redesigned in this CR (decoupled from `irp_job`, `request_key` replaced by a composite key — see §2a) — it's specifically the heartbeat/reconciler resilience *mechanism* from CR-001 that's out of scope here, not `rwb_job` as a whole.
- **Analysis templates / template suites, Phase A validation** — marked **DEFERRED** in §2a (not "unaffected" — deferred means paused/not built, with a couple of standardization fixes applied while reviewing them). Full design of either feature is out of scope for this CR.
- **File inventory, ignore rules** — genuinely unaffected by removing workflow, confirmed in §2a. `package`'s own pre-existing open TBD (job chaining across the IRP-job/RWB-job boundary) is **still open**, not resolved by this CR — see the `package` entry in §2a.
- **Actually editing `PRD.md` / `DATA_MODEL.md` / the constitution** — happens in a follow-up pass, per §11.
- **Resolving §9.1** (locating or reconstructing `mvp-scope.md`/`execution-design.md`) — still unresolved; finding/writing that document is its own task, not part of this CR.

---

## 11. Suggested next step

The table-by-table review is **complete** — §2a is the settled design. What
remains before or during the fold-back edit to `PRD.md`/`DATA_MODEL.md`:

1. Resolve `irp_analysis.rdm_id` (**BLOCKED** — needs the practice lead's
   direct knowledge of how Moody's actually exposes broker-analysis
   discovery; see the `irp_analysis` entry in §2a for the full evidence
   trail).
2. Locate or reconstruct `mvp-scope.md`/`execution-design.md` (§9.1) —
   several of this CR's own claims trace back to documents nobody reviewing
   it can currently see in-repo.
3. Settle the two outstanding items in §8 (`irp_rdm.submission_id`
   nullability; the auth-audit gap left by deferring `audit_log`).
4. Apply §2a's settled design to `PRD.md` §12–14 and `DATA_MODEL.md` §1–§11,
   table by table, per the analyst's standing instruction not to do this as
   one sweeping rewrite.
5. Apply the constitution cleanup in §7, once the schema edits above have
   landed (so the constitution reflects a settled design, not a moving
   target).
