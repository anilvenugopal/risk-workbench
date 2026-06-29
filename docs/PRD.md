# Reinsurance Cat-Modeling Workflow Tool — Product Requirements Document

**Status:** Draft for build · **Format:** living document, kept in the repo
**Intended builder:** Claude Code (agent-built, iteration-sequenced)
**Companion artifacts (separate):** ER diagram, optional clickable mock on the CSS framework

---

## 0. How to use this PRD

This document is **feature-organized** (Sections 4–15): each feature is self-contained — its purpose, data, behavior, and rules in one place. Section 16 is a **build plan** that sequences the work into iterations and, for each, cites the feature sections in scope and names what is explicitly out of scope. Section 17 is an **adversarial review** that deliberately attacks the plan for gaps and contradictions, each with a resolution folded back into the features. Section 18 logs locked decisions, assumptions, and external dependencies.

When building, treat the three **declarative sources of truth** (§2.1) as the spine: most "add a thing" tasks are a one-place edit to one of them.

---

## 1. Product overview

### 1.1 What this is
An internal tool for a reinsurance catastrophe-modeling team. It marries a **native workflow concept** to **Moody's IRP** (the external cat-modeling platform). Users register broker submissions, inventory the files on a shared drive, build and run staged modeling workflows against EDM/RDM data, monitor execution, and review/export results. It is **not** a multi-tenant SaaS and **not** a governance platform; it is a single-organization internal application.

### 1.2 Primary users & flows
Reinsurance analysts who: handle a **Submission** (a broker's package of files), associate one or more **shared-drive directories** to it, tag **EDM/RDM** files in the inventory, create one or more **Workflows** (EDF or RDF) under the submission, wire and validate the workflow, run it (locally and/or via IRP), and review/export **Results**. Administrators manage **Users** and **customer access**.

### 1.3 Core domain glossary
- **Customer → Program → Submission** — the business hierarchy. A Program belongs to a Customer; a Submission belongs to a Program.
- **Submission** — a broker's file package handled by a user. Anchors directories, artifacts, and workflows.
- **EDM / RDM** — Moody's exposure/results database files. The primary modeling inputs.
- **Directory** — a shared-drive folder associated to a submission; source of inventory files.
- **Artifact** — one immutable *version* of a file (shared-drive file **or** an upload **or** a workflow-produced output). Identified by a cheap metadata signature (path + size + mtime); no content hash.
- **Workflow** — a staged modeling pipeline under a submission. **Type** selects the stage set and allowed input(s); currently the only type is **EDM analysis** (resolved in code, not dynamically generated).
- **Stage** — an ordered, fixed phase of a workflow (EDM Upload [optional], Portfolio Summary Extract, Sub-Portfolio Creation, Geo-coding, Hazard lookup, Analysis, Grouping, Export). Has an execution mode. Every stage carries an **execution status** (`not_started` → `blocked` → `running` → `review` → `complete`/`canceled`) and an `auto_complete` toggle (default **false**): when its work finishes, a stage with `auto_complete=false` lands in **`review`** (a human must complete it) rather than `complete`. **`ERROR` is a dynamic rollup** (any task failed) that overlays any status — so a stage can be "complete · with errors".
- **Review** — completing a stage sitting in **`review`** or **`blocked`** after reading its tasks' output/error (or, for `blocked`, the validation result). Generic to any stage — there is no special review-stage type. **Cancelling** a stage halts the whole workflow (`execution_status → canceled`).
- **Task** — the executable unit inside a stage. Consumes typed inputs, produces typed outputs.
- **Handle** — a named, typed output produced by a task (an uploaded EDM, an analysis, a group) that downstream tasks can reference. The unit of **reference chaining**.
- **Job** — an executable task instance tracked for status (local worker or IRP-mirrored).
- **DLM / HD** — two Moody's model families (Detailed Loss Module / High-Definition). Cannot be mixed within a group.

---

## 2. Architecture principles

### 2.1 The three declarative sources of truth
Everything that "changes when requirements change" is pushed into **versioned code manifests**, so the engine code stays fixed and edits are one-place, greppable, type-checked, and diff-reviewable:

1. **Navigation manifest** (§4.2) — the rail/sidebar/breadcrumb/search-nav tree.
2. **Workflow-definition manifest** (§9.2) — stages, modes, skippability, task templates, ports.
3. **Type/port registry** (§10.1) — handle types, compatibility, propagation rules.

Graph **invariants** that are genuinely custom predicates (HD/DLM homogeneity, name uniqueness, IRP validity) are **registered named validators in code** (§10.6) — isolated, independently testable functions behind a registry, run by a generic pass that knows nothing about what each does.

**Versioning rule:** each manifest/registry carries a version identifier. A workflow **instance pins** the definition + registry version it was authored/run under, so later edits never rewrite the meaning of already-executed workflows.

**Manifest-vs-DB rule:** where a manifest is *projected* into DB tables for FK/reporting (the workflow definition → `workflow_definition`/`definition_stage`/`task_template`/`port_template`), the **manifest is canonical and the projection is generated, never hand-edited**, guarded by a fail-fast startup **consistency check** (manifest content-hash vs the hash the projection was built from) and a **version-retention** rule (projection is append-only; old versions retained while any instance pins them). Full treatment in §9.1a.

### 2.2 Stack posture
Server-rendered HTML over **FastAPI + Jinja + HTMX**, with **Alpine.js** for the few client-only behaviors (modal, keyboard shortcuts, focus trap). No SPA, no client state tree, no build step for the app shell. Styling is the existing **custom ITCSS design system** (DocIntel/Verity), copied verbatim; **not** Tailwind.

**Styling discipline — extend the system via tokens, don't override it.** New UI is layered into the existing **ITCSS** structure (settings → tools → generic → elements → objects → components → utilities), not appended as a flat sheet. Every color, surface, and spacing value comes from a **design token (CSS custom property)** in the settings layer — never a hardcoded hex inline in a component. Surfaces that today carry literal values (e.g. the rail charcoal, the sidebar gray, danger reds) get named tokens such as `--surface-rail`, `--surface-sidebar`, `--surface-page`, `--color-danger`, so theme changes are one-place edits and components stay declarative. Component-specific rules live in the **components** layer; one-off helpers in **utilities**. The rule of thumb: if a new screen needs a color the system doesn't have, add a token, don't write the hex into the component. (The clickable-mock CSS deliberately took shortcuts here — hardcoded hex and an append-only sheet — which is precisely the debt this discipline prevents in the real build.)

### 2.3 Maintainability contract
The product's headline non-functional requirement. Concretely, these tasks must each be a bounded, one-place change:
- **Add a page** → one nav-manifest node + one handler + one template. Rail placement, side-nav, breadcrumb, active-state, RBAC, and search visibility are inherited.
- **Add a searchable object type** → register one search provider.
- **Add a chaining type** → add registry rows + declare ports on task templates.
- **Add a workflow constraint** → write one registered validator + register it.
- **Change a stage's mode / skippability** → one manifest edit.
- **Change a workflow definition** → edit the code manifest and re-run the projection generator (§9.1a); the startup consistency check guarantees the DB matches. Never hand-edit the projected tables.

---

## 3. Technology stack & environment

| Concern | Choice |
|---|---|
| Web | FastAPI + uvicorn |
| Templating / interactivity | Jinja2 + HTMX 2.x (self-hosted) + Alpine.js (self-hosted) |
| Styling | Custom ITCSS design system (copied from `docintel/ui/src/styles`) |
| DB | **SQL Server Express** (local dev on WSL2; supports app-level scoping and, optionally later, native RLS) |
| DB access | SQLAlchemy Core + `pyodbc` + Microsoft ODBC Driver 18 |
| Background work | Separate worker process, APScheduler (3.x) + DB-as-queue claim pattern |
| Auth | Entra ID (OIDC auth-code) via Authlib/msal; backdoor mock principal |
| Sessions | Starlette `SessionMiddleware` (signed cookie) |
| Live status | HTMX polling (default); SSE (`sse-starlette`) only where push is justified |
| Reverse proxy | nginx (serve `/static` from disk; proxy app; SSE buffering off on stream routes) |
| Assets | **All local** — no CDN/external hosts (org network blocks them) |
| Dev environment | Windows laptop + WSL2 (Ubuntu); SQL Server Express in WSL2 or Docker Desktop |

---

## 4. Feature: Application shell & navigation

### 4.1 Layout
The reusable IDE shell (already in `layout.css`): left **rail** (icons), **sidebar** (contextual nav panel), **main** area, **top bar** (breadcrumb + global search + Help), **bottom status bar**. Home renders **without** a sidebar (full-width cards + tables); all other rail destinations show a sidebar.

### 4.2 Navigation manifest (the keystone)
One declarative tree. Each **node** declares:

| Field | Meaning |
|---|---|
| `key` | Unique stable id |
| `label` | Display label |
| `parent` | Parent node key (null for rail-level roots) |
| `rail_icon` | Local icon name (rail-level nodes only) |
| `route` | URL path it owns |
| `template` | Template/handler binding |
| `breadcrumb_label` | Label used in trails (defaults to `label`) |
| `searchable` | Whether it appears in the search "navigation" group |
| `roles` | Roles permitted to see/act (RBAC, §6) |

**Derived from this one structure:** the rail (root nodes), the sidebar (a root's children, first child selected by default), breadcrumb trails (walk `parent` upward), active-state highlight (current route → node → root ancestor), and the search navigation group (filter on `searchable`).

**Dynamic detail pages** (e.g. `SUB-123`) are **not** manifest nodes. A detail route **declares the manifest node it lives under** (its "home" node); breadcrumb = walk up from that declared node, then append the entity's own label. This is what makes §4.3 work for entities that don't exist at config time.

### 4.3 Breadcrumbs — context-based, not history-based
A breadcrumb is a **pure function of position in the manifest**, never of navigation history. Walking from a Submission detail into a Workflow detail produces a **Workflows** context (rail, sidebar selection, and trail all reflect the workflow's declared home), regardless of where the user came from. No back-stack.

**Requirement:** every page and detail view has a **real URL**; HTMX navigations use `hx-push-url` so the address bar stays truthful. Breadcrumb/active-state resolution is then `current URL → manifest node (or detail's declared home) → walk up`. Refresh, deep-link, bookmark, and browser back/forward all fall out of this.

**Navigation transport — `hx-boost`.** Top-level rail/sidebar navigation uses **`hx-boost`** on the shell (anchors are progressively enhanced into AJAX swaps of the main content region, with history managed for free). This avoids the full-page reload on every nav click — preserving the persistent shell (rail, sidebar, status bar), avoiding a re-flash of unchanged chrome, and keeping scroll/transient client state — **without** introducing an SPA or client router. It degrades gracefully: with JS off, the same anchors are ordinary links to the same real URLs. `hx-boost` composes with the `hx-push-url` requirement above (boosted navigations push the real URL); breadcrumb/active-state still resolve purely from that URL.

### 4.4 Status bar
IDE-style, three zones: **left** = ambient context (environment badge — loud in LOCAL/backdoor mode — signed-in user + active role); **center** = background activity ("2 jobs running", wired to the monitor in §11.5); **right** = last-action result ("SUB-123 created") + request spinner (`htmx-indicator`). May also show current master-detail selection. (Shell built early; live activity wired when execution lands — §16.)

### 4.5 Icons
No external icon hosts. SVGs stored on disk under `static/icons/`, **inlined** via an `icon(name)` Jinja macro (reads the file into the page). Inline SVG inherits `currentColor`, so rail active-state/theming is free. **Dependency:** the actual SVG source set must be committed (§18).

---

## 5. Feature: Authentication & session management

### 5.1 Entra ID SSO
OIDC authorization-code flow. Entra authenticates **identity only**; the stable object id (`oid`) maps to a local user record, **provisioned on first sign-in**. Authorization (roles, customer access) is owned by the app, **never** read from token role claims.

### 5.2 Backdoor login
A self-identified entry door while SSO is under construction: a **dropdown of real users**; signing in establishes a session **as that user** through the *same* session-creation path as SSO (one identity pipeline, two doors). It is **not** a mock principal — it impersonates a real provisioned user.

**Fail-safe requirements (non-negotiable):** default **off**; enabled only when an `ENFORCE_SSO=false` flag **and** an independent environment signal (`APP_ENV != production`) both permit it; enforced **server-side** (hiding the UI is insufficient); every backdoor sign-in and action **audited** (§15.1); a persistent, loud UI banner while a backdoor session is active.

**Accepted tradeoff (§5.3):** with signed-cookie sessions there is **no server-side revocation**, so a backdoor session **cannot be force-killed mid-session** — only disabled for *new* sign-ins via the flag. Acceptable for an internal, non-governance tool.

### 5.3 Sessions — signed cookie
Starlette `SessionMiddleware`, one signing secret, `HttpOnly`/`Secure`/`SameSite=Lax`. The cookie carries **identity only** (user id + session metadata). **Authorization is never cached in the cookie** — roles and customer access are read **live from the DB on each request** (see §6, and adversarial item A4). This keeps admin changes to access effective immediately despite stateless sessions.

### 5.4 Idle timeout & the HTMX redirect gotcha
Sliding idle timeout + absolute cap, tracked via `last_activity`. On expiry, redirect to sign-in and remember the intended URL for post-auth return. **Gotcha:** an expired session hit by an HTMX request must **not** swap a login page into a fragment — detect the expired-session-on-HTMX case and respond with the **`HX-Redirect`** header to force a full-page navigation. One auth-failure handler covers every page. **CSRF** protection on all state-changing requests.

### 5.5 Identity vs authorization
Stated as a hard rule because it recurs: **identity** comes from Entra (or the backdoor); **authorization** (roles + customer scope) comes from the app's own tables, evaluated live. The two are never conflated.

---

## 6. Feature: Authorization & row-level security

### 6.1 Roles
**Global roles** (not per-customer) in v1. Role set TBD with the team; an `admin`/superuser role bypasses customer scoping (see §6.2). Roles gate manifest nodes and actions (RBAC), checked server-side.

### 6.2 Customer-access scoping (app-level RLS)
Not multi-tenant; instead, users are limited to customers. A `user_customer_access(user_id, customer_id)` table defines each user's allowed customer set. **All data access funnels through a repository layer** whose every list/detail query calls a single `apply_scope()` helper that injects `WHERE customer_id IN (allowed set)`. `customer_id` is **denormalized onto every major table** (submission, workflow, job, artifact, result) so scoping is a single-column predicate everywhere, never a multi-join.

`apply_scope()` honors the **admin bypass** (superusers see all). Native SQL Server RLS is a **documented later hardening layer** (defense-in-depth), not v1.

### 6.3 Admin maintenance
Admin screens (rail: Administration → Users, Settings) maintain users and `user_customer_access`. Building this early makes RLS testable end-to-end.

---

## 7. Feature: Domain model (Customer → Program → Submission)

Hierarchy: **Customer → Program → Submission**. A Submission anchors: **directories** (§8), **artifacts** (§8), **workflows** (§9), and indirectly **jobs/results** (§11/§13). A **Job** ties an EDM/RDM artifact + a Workflow; **Results** hang off the Job. Every major entity carries `customer_id` (§6.2) so all of it traces to a Customer and is scope-enforced.

Submission UI follows the **master-detail** pattern (§15.5): filterable list (Customer/Program filters) + "Details of Selected Item" panel. List ergonomics per §15.4.

---

## 8. Feature: File inventory & artifacts

### 8.1 Directory association
`submission_directory` links a submission to one or more paths, **UNIQUE on path** (a directory cannot belong to two submissions). Stores both the Windows UNC path (human-facing) and the Linux mount path (for reading). **Hard dependency:** the broker shared drive must be **mounted read-only** into the Linux host (CIFS/SMB, least-privilege service account; on WSL2 a drvfs mount). The app **only reads** — never writes/moves/deletes broker files (§18 dependency: mount + service-account access).

### 8.2 Immutable artifact model
A `file_artifact` row = one **version** of a file. Fields (indicative): `id`, `source` (`shared_drive` | `upload` | `workflow_output`), `submission_id`, `customer_id`, `directory_id` (nullable), `relative_path`, `filename`, `size`, `fs_modified_at`, `first_seen_at`, `status` (`present` | `changed` | `missing`), `tag` (`edm` | `rdm` | none), plus produced-output metadata for `workflow_output` (§10.7). Identity is a **cheap metadata signature** — `(relative_path, size, fs_modified_at)` — **not a content hash** (decision: hashing a 1.4 GB MDF is too expensive to ever run on a request path, so it is dropped entirely). **Append-only:** files are never updated in place; a detected change retains the old row and inserts a new one. **Known limitation:** without a content hash, identity/change-detection is *best-effort* — a content change that preserves size and mtime is invisible, and a touch that bumps mtime without changing content reads as a change. For an internal tool against a controlled broker drive this is an accepted trade; the discrepancy/severity model (§8.6) is the safety net, and a hash could be reintroduced later as an optional background job if a real case demands authoritative identity.

### 8.3 Reconciliation scanner & triggers
A **background worker job** (not a request). **Triggers are bounded** (per decision): when a directory is **added/removed** (removal sends that directory's files to `missing`), when a **workflow task attempts to use** a file, when a user **opens the submission page**, and on an explicit **"Refresh inventory"** button. Per file on disk, compared to the currently-tracked artifact for that path:
- not tracked → insert new `present`, untagged;
- tracked & unchanged → no-op;
- tracked & changed → mark old `changed`, insert new `present`;
- tracked & gone → mark `missing`.

### 8.4 Change detection
**Cheap detector** = filename + `fs_modified_at` + size, every scan — this *is* the identity signature (§8.2), so a trip both detects and characterizes the change; no confirmation hash. A **settle window** (ignore files modified within the last N seconds) prevents fingerprinting a file mid-copy.

### 8.5 Tagging
Users tag artifacts as **EDM** or **RDM** on the submission detail page. Tagged artifacts are what a workflow's EDM Upload stage / reference step can select (§10.2).

### 8.6 Discrepancies
Raised when a tracked file **changes** or goes **missing**. **Severity escalates** when the changed artifact was **tagged**, and escalates further when it had been **referenced/pinned by a workflow** (a result's input provenance is now in question). Surfaced as: a discrepancies count in the status bar, a marker on the submission, and a dedicated list. (Latency is bounded by trigger frequency — adversarial item A3.)

### 8.7 Upload storage
Upload tasks (§9) produce artifacts with `source=upload`, stored under a **server-managed upload location** (filesystem path; not the read-only broker mount). Uploads are immutable by nature — a re-upload is a new artifact. Same `file_artifact` model, different `source` (one store, two sources — adversarial item A11).

---

## 9. Feature: Workflow model

### 9.1 Definition vs instance
Two clean layers:
- **Definition** (template, code manifest §9.2): Workflow → ordered Stages (mode + skippable; per-instance auto_complete) → Task-templates (typed ports). Currently one definition: **EDM analysis**.
- **Instance** (runtime, SQL): workflow-instance → stage-instances → task-instances, each with status, counts, and resolved I/O. **Reference chaining is wired on the instance** (§10.7), referencing other task instances' declared output handles.

An instance **pins** the definition + registry version it used (§2.1).

### 9.1a Manifest is canonical; the DB definition is a generated projection
The **code manifest (§9.2) is the single source of truth** for workflow definitions. The projected definition tables — `workflow_definition`, `definition_stage`, `task_template`, `port_template` — are a **derived build artifact**, generated *from* the manifest, that exists only so instance rows can FK to stable codes and so SQL can join for labels/reporting. The relationship is exactly the event-sourcing pattern used elsewhere (§11/data model): one canonical source, one maintained cache.

Three rules make this safe and non-drifting:
- **Never hand-edit the projection.** The projected tables are written *only* by the projection generator. Editing a workflow definition means editing the manifest and re-running the generator — never `UPDATE`-ing a `definition_stage` row.
- **Fail-fast consistency check at startup.** The projection records the **content hash** of the manifest it was built from (stages, order, modes, ports, types, version). At startup the app recomputes the hash of the live manifest and compares; on mismatch the app **refuses to start** with a clear message ("workflow manifest changed since last projection — run the projection step"). The check is total and cannot be half-satisfied — there is no field-by-field diff to get wrong.
- **Projection is append-only / version-retained.** Generating a new version **inserts** new `(definition_id, version)` rows; it **never deletes or overwrites** prior versions. Old versions are retained as long as **any `workflow` instance pins them** (garbage-collected only once no instance references a version). This is the definition-side analogue of "artifacts are append-only": because an instance pins, say, definition v1, the v1 stage/port rows it references must remain readable even after the manifest moves to v2 — otherwise an in-flight or historical workflow would be orphaned (its `stage_instance` rows pointing at a definition that no longer exists). Pinning declares intent; retention honors it.
Declarative. A **workflow definition** declares: `type`, `version`, the **allowed input(s)**, and an **ordered list of stages**. The **type drives the stage set and the allowed inputs** — resolved in **code** per definition (a `WHEN type THEN stages/inputs` lookup), **not** dynamically generated. Currently one type, **EDM analysis**, which declares **exactly one input: a file tagged `EDM`**. Each **stage** declares: `key`, `label`, `mode` (`singleton` | `parallel` | `sequential`), `skippable` (bool), and its **task templates**. Each **task template** declares its **input ports** and **output ports** (§10.2). Stages are **constant and fixed-order** — skippable but **never reorderable**. There is **no special review-stage type**: review is a per-instance property (`auto_complete=false` ⇒ the stage parks in `review`). EDM-analysis seed (in order): **EDM Upload** (singleton, **skippable** — only needed if the EDM isn't already in IRP), **Portfolio Summary Extract** (singleton), **Sub-Portfolio Creation** (sequential), Geo-coding (parallel), Hazard lookup (parallel), Analysis (parallel), Grouping (sequential), Export (parallel). The portfolio-before-sub-portfolio dependency is enforced by the review model, not a special construct: Portfolio Summary Extract typically runs with `auto_complete=false`, so it parks in `review`; Sub-Portfolio Creation cannot start until it is `complete`. These may change later via manifest edit.

### 9.2a Stage review & status model
Every stage has an **execution status** — `not_started` → `blocked` → `running` → `review` → `complete`/`canceled` — and a per-instance **`auto_complete`** toggle the user sets at compose time (default **false**).
- When a stage's tasks finish: `auto_complete=true` ⇒ the stage goes `complete` and the workflow proceeds; `auto_complete=false` ⇒ the stage goes **`review`** and waits for a human.
- **`ERROR` is not a stored status** — it's a **dynamic rollup** (any task `failed`). It overlays any status, so a stage can read "complete · with errors" or "canceled · with errors". `error` is therefore *informational*, never a gate.
- **`blocked`** is a gate raised by a (future) runtime validation; it carries a **validation result** (severity + message) surfaced in the same review panel. We build the status + the display slot now, not the checks.
- **Review** = a user reading a stage's tasks' output/error (or the validation result) and choosing **Complete** or **Cancel**. **Complete** advances the workflow (a stage with errors is still completed — *completed-with-errors*, which is **audited**). **Cancel halts the whole workflow** (`execution_status → canceled`). There is **no retry/rerun** — the escape hatch is cancel-and-create-new.
- **Active gates** (`review` + `blocked`) are what the **Review** queue counts: home **Review** card, the Workflows **Review** sidebar item, the workflows-table indicator (amber when any stage is `review`/`blocked`/`error`), and the workflow-detail surfacing. Counts include only active gates — *completed-with-errors* is shown but **not** counted (it isn't waiting on anyone).
- **Composition is per-stage.** A stage whose execution status is `not_started` is **editable** (task add/remove/edit); it locks once it leaves `not_started`. Editing a not-started stage while other stages run is allowed; the future validation editor guards cross-stage references.

### 9.3 Stages & execution modes
- **Singleton** — exactly one task; no intra-stage dependency.
- **Parallel** — sibling tasks, **no ordering, no intra-stage edges**; may consume only prior-stage / external inputs; all dispatchable at once.
- **Sequential** — ordered tasks; **may** chain to earlier tasks *in the same stage*.

Skipping a stage marks its tasks `skipped` and passes inputs through; skipping is **blocked** when a downstream task references a handle the stage would have produced (adversarial item A2).

### 9.4 Tasks & typed ports
A task declares typed **input ports** (name + accepted type set) and **output ports** (name + emitted/derived type). A task can begin only when every required input port is **bound and resolved**. See §10.

### 9.5 Workflow states
`draft → validated → runnable` (then execution states per §11):
- **draft** — being authored; only cheap compose-time checks applied (§10.4).
- **validated** — passed the whole-graph save-time pass, **including external/IRP checks**; this transition (not every keystroke/save) is where IRP is called, so an IRP outage can't block authoring.
- **runnable** — validated and ready to execute.

---

## 10. Feature: Type registry, reference chaining & validation

This is the heart of the product's adaptability. **No engine code enumerates handle "kinds".** Kinds are data; the validator asks compatibility questions answered by lookups.

### 10.1 Handle-type registry (data)
Each producible/consumable type is a **row**: `id`, `label`, optional `parent` (single-parent inheritance for compatibility). Seeded with `edm`, `rdm`, `analysis`, `group`, `dlm`, `hd` — but nothing in code treats these as special. New chaining needs add rows, not branches. Lives in the **code manifest** (versioned, pinned by instances). Flat registry + optional single parent — **no** coercion/transformation graph (deliberately deferred; adversarial item A16).

### 10.2 Typed ports & input sources
Every task input resolves to one of three sources, presented uniformly as an **input reference**:
1. **Inventory item** → pins an immutable `artifact_id` (tagged EDM/RDM).
2. **Upstream output (handle)** → references a specific upstream `task_instance` output port. *Prior stage freely; prior task only within a sequential stage.*
3. **Literal / reference-table row / parameter** → a user value or a pinned reference-table/parameter version.

A consumer port declares the **type set** it accepts; the UI offers the matching handles as a dropdown.

### 10.3 Type propagation
An output port's emitted type is either **literal** (`analysis` emits `analysis`) or **derived** (`group` emits "same as my inputs' type"). Derivation is what carries DLM-ness / HD-ness through group-of-groups and gives the homogeneity invariant (§10.6) something to check. Derived types are known **structurally** at authoring time from the wired inputs' declared types (runtime data not required).

### 10.4 Two-phase validation
- **Compose-time (local, instant, per-edge):** does this consumer port accept this producer's emitted type, and does the structural rule (§10.5) hold. Runs as the user wires; rejects obvious mistakes immediately.
- **Save-time / validate (whole-graph, may call IRP):** graph invariants (§10.6) over the assembled workflow, gating the `draft → validated` transition (§9.5).

### 10.5 Structural rule (written once, generic)
An edge from producer port P to consumer port C is legal **iff** C's accepted-type set is compatible with P's emitted type (registry lookup, incl. parent inheritance) **and** one of: (a) P's task is in an **earlier stage**, or (b) same stage, that stage is **sequential**, and P's task **precedes** C's. Same-stage edges in parallel stages and any later→earlier (backward) edge are rejected. A **cycle check** runs regardless. This function does **not** grow as requirements change — the invariant that protects maintainability.

### 10.6 Graph invariants (registered named validators)
Custom predicates over the whole graph, each a **small, isolated, independently-testable function** with a declared **scope** (which tasks/stages it applies to), run by a generic save-time pass. Seeded:
- **Homogeneity** — all inputs to a group must resolve to the same type (no HD + DLM in one group). Uses propagated types (§10.3); may require an **IRP call** to resolve a file's family.
- **Uniqueness** — no duplicate analysis names; no duplicate group names (scope-defined).
- **External validity** — IRP confirms the configuration is runnable.

New constraint = one registered validator + register it. These are **code** (genuinely custom) — explicitly *not* a business-user rule DSL (adversarial item A16).

### 10.7 Reference chaining vs data lineage
Two distinct concerns, only the first touches workflow definition and the validator:
- **Reference chaining** (user-wired, validated) — a named **handle** produced by a task (uploaded EDM/RDM, analysis, group) becomes a selectable input downstream. Confirmed cases: EDM/RDM handle from the EDM Upload stage reusable in later stages; analysis names reusable in Grouping; group names reusable in **subsequent** Grouping tasks (legal only because Grouping is sequential). Group-of-groups: a group task consumes `{analysis, group}` and produces `group`.
- **Data lineage** (implicit, automatic) — produced outputs (geocode tags on EDM data, etc.) are themselves **artifacts** in the §8 model, giving one end-to-end provenance graph. Not user-wired; not validated as chaining.

**Handle re-run semantics:** when an upstream task is edited and re-run, downstream tasks that pinned its prior output are marked **stale (needs review)** — not silently re-pointed (consistent with version pinning; adversarial item A1).

---

## 11. Feature: Execution engine & job monitoring

### 11.1 Task as job (SQL Server table *is* the queue)
A task-instance is a **job row** in SQL Server; that table is the queue — there is no separate queue technology, and no broker (no Celery/Redis).

**Default: a single worker, plain dequeue.** Because IRP already has its own queues (§12), the worker's role for IRP-backed stages is **submit-then-poll/mirror**, not local compute — it is I/O-light and naturally sequential. So the default is **one worker process** doing a plain `SELECT TOP (1) ... WHERE status='ready' ORDER BY priority, id` then `UPDATE ... SET status='running'`. No locking hints needed (no concurrent claimers), which is the simplest readable form.

**Documented upgrade (do not build until needed):** to run multiple concurrent workers, swap the dequeue for the concurrency-safe claim query — `SELECT TOP (n) ... WITH (READPAST, UPDLOCK, ROWLOCK)` + `OUTPUT` so workers pull disjoint rows. This is a one-statement change: no schema change, no architecture change. (See companion job-runner spec for the claim SQL.)

**Boundary:** *our* table sequences work and enforces the readiness gate (§11.2) — which IRP cannot, since only we know when a task's pinned inputs have resolved; *IRP's* queues do the actual execution. The two are complementary, not redundant.

**Retained regardless of worker count:** the **reclaim-stuck sweep** (§11.3) — a periodic reset of rows stuck in `running` past a timeout back to `ready` — so a worker dying mid-job never strands a task.

### 11.2 Readiness gate & state machine
Per-task: `blocked → ready → running → succeeded | failed | skipped`. **blocked→ready** is computed purely from whether all bound inputs have **resolved** (upstream artifacts produced, present, and unchanged per §8.6). The claim query gates on a `ready` predicate. *(Authoring-time validation §10 is distinct from execution-time readiness — adversarial item A14.)*

### 11.3 Worker process
Separate from the web process; APScheduler drives the poll loop + the reclaim-stuck sweep. **Single worker by default** (§11.1). For IRP-backed tasks the worker's role is **submit-then-poll/mirror** (§12), so concurrency is naturally bounded; IRP-side dispatch still honors IRP's **rate limits/concurrency caps** (§12, adversarial item A7). Local-compute tasks (if any) and the multi-worker upgrade follow §11.1.

### 11.4 Stage / workflow rollups
Task statuses are leaves. A stage's **execution status** is event-sourced (§9.2a); its Task/Completed counts and its **`error` overlay** (any task `failed`) roll up from its tasks. The **workflow** current stage = earliest stage not `complete`; overall execution status rolls up from stages, with one override: a **canceled stage forces the workflow to `canceled`** and stops progression. `error` never gates — only `review` and `blocked` do.

### 11.5 Monitoring
**HTMX polling by default** — a self-polling status element that stops when terminal (no long-lived connections). The workflow-detail stage list and the status-bar activity zone both consume this. **SSE** only where sub-second push or high fan-out justifies it (with nginx buffering off).

---

## 12. Feature: Moody's IRP integration — interface abstraction

> **This section is an interface contract, not an implementation.** The actual IRP API is an **external dependency to be filled in** (§18). The architecture does not change once it is known.

**Abstraction:** IRP-backed work follows the same model as local work — **jobs are rows; something advances them; a sync mirrors external state into our tables; the UI monitors our local mirror.** The integration surface is a small interface with these capabilities (names indicative):
- `submit(job) → external_ref` — hand a task to IRP.
- `poll(external_ref) → status/result` — mirror state back (worker sync loop).
- `validate(workflow_config) → ok/errors` — used by the save-time external-validity invariant (§10.6).
- `resolve_model_family(artifact) → dlm|hd` — used by the homogeneity invariant (§10.6).
- `metadata_sync()` — the rail's "Sync metadata" action.

**Constraints to honor when the API is known:** rate limits / max concurrency (§11.3); IRP availability is a **hard runtime dependency** for IRP-backed stages and for the `validated` transition when external checks are required (adversarial item A8); auth/credentials to IRP via server-side config (never client).

---

## 13. Feature: Results & export
Results hang off Jobs (§7). The Results rail destination lists results and Reports; Export is a workflow stage (parallel) that produces result artifacts (§8 model). Review uses master-detail (§15.5). Export destinations/formats TBD with the team.

---

## 14. Feature: Global search
**Ctrl/Cmd-J** opens a modal (Alpine.js: open/close, keyboard nav, focus trap). Search-as-you-type via HTMX (`hx-trigger="keyup changed delay:200ms"`). A **provider registry** fans out across result groups, each a registered provider: **navigation** (reads the nav manifest — new nav items are searchable automatically), **submissions**, **workflows**, **templates**. Adding a searchable type = register one provider. **All providers apply `apply_scope()`** so results are customer-scoped and cannot leak across customers (adversarial item A8/A9). Start with SQL `LIKE`/`CONTAINS`; move to Full-Text indexes only if volume demands. Providers are added **incrementally** as their entities land (adversarial item A1-search).

---

## 15. Cross-cutting concerns
### 15.1 Audit logging
Who did what, when. Mandatory for backdoor actions (§5.2); generally applied to state-changing actions.
### 15.2 Flash / toast
Standard server-set notification surfaced after actions (status bar / toast) for consistent feedback.
### 15.3 Error / empty / loading states
Consistent, **HTMX-aware** 403/404/500 responses (fragment-safe; `HX-Redirect` where a full nav is needed).
### 15.4 List ergonomics
Reusable **server-side pagination, filtering, sorting** for the Customer/Program-filtered tables. One pattern, reused.
### 15.5 Master-detail layout
The list + "Details of Selected Item" panel recurs (Submissions, Workflows). Built **once** as a reusable layout; row click `hx-get`s detail into the right-panel block.
### 15.6 Feature flags / config
Centralized. First flags: `ENFORCE_SSO`, backdoor enablement; more will accrue.

---

## 16. Build plan

Each iteration ends **runnable and demonstrable**. Sequencing is deliberate: dependencies first (inventory before workflows, because workflows pin inventory artifacts; the validator ships *with* authoring, never after).

### Iteration 0 — Foundation & shell
**In:** §2, §3, §4 (shell, **nav manifest**, context-based breadcrumbs + `hx-push-url`, status-bar shell, local icons), §15.5 master-detail skeleton, CSS framework integration.
**Out:** auth (open access in dev), all domain data, search providers (framework only), live status-bar activity.
**Exit:** can add a page via one manifest node + handler + template and get rail/sidebar/breadcrumb/active-state for free.

### Iteration 1 — Auth, sessions, RLS scaffold, admin
**In:** §5 (Entra SSO, backdoor with fail-safe, signed-cookie sessions, idle timeout + `HX-Redirect`, CSRF), §6 (roles, `apply_scope`, admin bypass), §6.3 admin Users + `user_customer_access`.
**Out:** native SQL RLS (later hardening), per-customer roles.
**Exit:** sign in via SSO or backdoor; access live-scoped from DB; admin can edit customer access and see it take effect immediately.

### Iteration 2 — Domain & search
**In:** §7 (Customer/Program/Submission, master-detail, list ergonomics §15.4), §14 search **framework** + navigation & submission providers (scoped).
**Out:** workflow/template/inventory search providers (added in their iterations).
**Exit:** browse scoped submissions; Ctrl/Cmd-J finds nav items and submissions.

### Iteration 3 — File inventory subsystem
**In:** §8 (directory association + read-only mount, immutable artifacts, bounded-trigger scanner, cheap-detector (path+size+mtime, no hash), tagging, discrepancies, upload storage).
**Out:** workflow references to artifacts (consumed next iteration).
**Exit:** associate a directory, scan, see inventory, tag EDM/RDM, detect a changed/missing file as a discrepancy.

### Iteration 4 — Workflow authoring, type registry & validation
**In:** §9 (definition manifest, instance, stages/modes, **draft→validated→runnable**), §10 (type registry, typed ports, propagation, **two-phase validation**, structural rule, registered invariants incl. homogeneity/uniqueness; external-validity behind the §12 interface), §10.7 reference chaining, workflow search provider.
**Out:** actual execution (next iteration); real IRP calls (interface stubbed/mocked).
**Exit:** author an EDM-analysis workflow, wire reference chaining, get instant compose-time rejection of illegal edges and a save-time validate pass (incl. mocked external checks); illegal HD+DLM grouping and duplicate names are caught.

### Iteration 5 — Execution, IRP sync, monitoring, results
**In:** §11 (task-as-job claim, readiness gate, worker, rollups, monitoring), §12 (real IRP integration via the interface), §13 results/export, live status-bar activity, template/parameter/reference-table search providers.
**Out:** —
**Exit:** run a workflow locally and/or via IRP; watch stage Task/Completed/Error counts update live; review and export results; discrepancies on pinned inputs surface.

---

## 17. Adversarial review

Deliberate attempt to break the plan. Each item: the attack, then the resolution (folded into the sections above).

- **A1 — Stale handles on re-run.** *Attack:* re-running an upstream task silently changes a downstream task's input, corrupting results. *Resolution:* downstream pins a specific produced-output version; a re-run marks dependents **stale/needs-review**, never auto-repoints (§10.7). Consistent with universal version pinning.
- **A2 — Skipping a referenced stage.** *Attack:* Grouping references an Analysis handle; Analysis is skipped → unsatisfiable reference. *Resolution:* skipping is **blocked** when downstream references the stage's handles, with a clear reason (§9.3).
- **A3 — Discrepancy latency.** *Attack:* bounded scan triggers mean a changed file may go undetected. *Resolution:* accepted by design; critically, **"workflow task attempts to use a file" is a trigger**, so the execution-critical path always re-scans before use (§8.3). Latency elsewhere is acceptable for an internal tool.
- **A4 — Cookie sessions vs. live access changes.** *Attack:* signed-cookie sessions can't revoke, so an admin's customer-access change wouldn't take effect until expiry. *Resolution:* the cookie carries **identity only**; roles + scope are read **live from DB every request** (§5.3, §6.2). Access changes are immediate; only *session existence* can't be revoked (accepted, A5).
- **A5 — Backdoor can't be killed mid-session.** *Attack:* a live backdoor session can't be force-terminated. *Resolution:* explicitly accepted for a non-governance internal tool; mitigated by default-off, dual-gate enablement, server-side enforcement, audit, and a loud active banner (§5.2). Flagged, not hidden.
- **A6 — Definition version identity.** *Attack:* "instance pins definition version" is meaningless if a code manifest has no version. *Resolution:* each manifest/registry carries an explicit `version`; instances record it (§2.1, §9.1).
- **A7 — IRP concurrency.** *Attack:* a parallel Export stage fires many concurrent IRP calls and trips rate limits. *Resolution:* IRP-backed dispatch is **rate-limited/concurrency-capped** per the §12 interface (§11.3).
- **A8 — IRP outage blocks everything.** *Attack:* save-time validation and execution both depend on IRP. *Resolution:* authoring stays in **draft** without IRP; only the `validated` transition and **IRP-backed execution** require it — a stated hard runtime dependency, not a hidden coupling (§9.5, §12).
- **A9 — Search leaks across customers.** *Attack:* global search returns names from customers the user can't access. *Resolution:* **every** search provider applies `apply_scope()` (§14).
- **A10 — Admin can't see all customers under RLS.** *Attack:* `apply_scope` would hide everything from admins maintaining access. *Resolution:* `apply_scope` honors an **admin bypass** (§6.2).
- **A11 — Upload vs shared-drive store split.** *Attack:* two file stores fragment chaining/lineage. *Resolution:* **one** `file_artifact` model, `source` discriminator (`shared_drive`/`upload`/`workflow_output`) (§8.2, §8.7).
- **A12 — Detail pages have no manifest node.** *Attack:* context-based breadcrumbs need a node, but `SUB-123` isn't one. *Resolution:* detail routes **declare a home node**; breadcrumb walks up from it + appends entity label (§4.2, §4.3).
- **A13 — Directory path overlap.** *Attack:* `UNIQUE(path)` blocks exact duplicates but not nested paths (`/share/a` vs `/share/a/b`) across submissions. *Resolution:* accepted limitation v1; documented. Revisit if nesting causes double-counting.
- **A14 — Authoring validation vs execution readiness conflated.** *Attack:* treating them as one thing breaks either the editor or the runner. *Resolution:* explicitly separated — §10 (authoring, graph rules) vs §11.2 (execution, input-resolved gate).
- **A15 — Status-bar activity before execution exists.** *Attack:* the status bar (Iter 0) shows "jobs running" before jobs exist (Iter 5). *Resolution:* status-bar **shell** in Iter 0; **live wiring** in Iter 5 (§4.4, §16).
- **A16 — Over-generalizing the rule engine.** *Attack:* a data-driven type system invites a full coercion graph / business-user rule DSL — its own maintenance nightmare. *Resolution:* hard line — flat registry + single-parent inheritance only; invariants are **registered code validators**, not a DSL; coercion deferred as a documented extension point (§10.1, §10.6).
- **A17 — Icon assets.** *Attack:* "local SVGs" assumes a committed icon set that doesn't exist yet. *Resolution:* asset-gathering **dependency** logged (§18); not a code blocker.
- **A18 — `customer_id` denormalization drift.** *Attack:* a denormalized `customer_id` could disagree with the parent chain. *Resolution:* set once at creation from the parent, never user-editable; treat as derived/immutable. (Implementation note for the data model.)

**No unresolved contradictions identified.** Open *decisions* (vs. contradictions) are in §18.

---

## 18. Assumptions, decisions & external dependencies

### Locked decisions
- Three declarative sources of truth as **code manifests**, versioned, instance-pinned (§2.1).
- **Signed-cookie** sessions; cookie = identity only; auth read live from DB (§5.3).
- Backdoor login, fail-safe, **cannot be force-killed mid-session** — accepted (§5.2).
- **App-level RLS** via `apply_scope` + `user_customer_access`; **global roles**; native RLS later (§6).
- One **immutable artifact** model, two input sources + workflow outputs; **cheap metadata signature (path+size+mtime), no content hash** (hashing dropped — too expensive; identity is best-effort, §8.2); **bounded scan triggers** (§8).
- Workflows **pin immutable artifact versions**; reference chaining distinct from data lineage (§8/§10.7).
- **Definition versioning + instance pinning** (§9.1).
- **Workflow definition: manifest is canonical, DB tables are a generated projection** — never hand-edited; fail-fast startup consistency check (content-hash) + append-only version retention while instances pin (§9.1a).
- **Outputs are first-class artifacts** (§10.7).
- Workflow states **draft → validated → runnable**; external/IRP checks gate `validated` (§9.5).
- Graph invariants are **registered code validators** (custom by nature), not a DSL (§10.6).
- IRP written as an **interface abstraction** (§12).
- **SQL Server table is the queue**; **single worker + plain dequeue** by default (IRP already queues/executes); concurrency-safe claim query is a documented one-statement upgrade; reclaim-stuck sweep retained regardless (§11.1).
- **Top-level navigation uses `hx-boost`** (progressive AJAX nav, no SPA), composing with `hx-push-url` (§4.2).
- **Styling extends the ITCSS design system via tokens** (`--surface-rail`, `--surface-sidebar`, `--color-danger`, …) layered into the proper ITCSS layers — never hardcoded hex or a flat append-sheet (§2.2).

### Open decisions (need team input; do not block early iterations)
- Concrete **role set** and the admin/superuser definition (§6.1).
- **Reference tables & parameters** scope — global vs customer/program-scoped (affects §10.2 schema, dropdowns).
- **Export** destinations/formats (§13).
- Idle-timeout durations (sliding + absolute) (§5.4).
- Admin screen scope in v1 confirmed in Iter 1 — confirm depth (just Users + access, or more).

### External dependencies
- **Moody's IRP API** contract — submit/poll/validate/resolve-family/metadata-sync, rate limits, auth (§12).
- **Shared-drive mount** — read-only CIFS/SMB into the Linux host + least-privilege **service account** (security sign-off) (§8.1).
- **Icon SVG source set** committed to `static/icons/` (§4.5).
- **SQL Server Express** on WSL2 — setup to be guided separately (§3).
