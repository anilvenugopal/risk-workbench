# Feature Specification: Submission Data and Cross-Entity Search (Iteration 12)

**Branch**: `017-submission-data-and-search` | **Created**: 2026-09-17

## Status

**Phase:** Draft
**Blocking:** Nothing

## Outcome

An analyst records what CRM knows about a deal — Won / Lost / In Process, a client from CIC's repository, and inception and expiration per CRM ID — and finds EDMs and RDMs by those deal attributes, including "in force as of a date" and a list of CRM IDs. Today Risk Modeler's tag search ORs tags together and the libraries filter on name and import status only.

## In scope

- Two statuses on a submission, named apart: **Modeling status** (Active, Completed, Cancelled) and **Submission status** (Won, Lost, In Process).
- Inception and expiration per CRM ID, shown on the submission page per CRM ID, with a "make them all the same" control; expiration is new.
- The submission page's metadata section and its controls for Modeling status, Submission status, CRM IDs and dates are redesigned as one screen; the EDM, RDM, analyses and exports tables on the page are unchanged.
- A **Client** from CIC's repository client list on the submission, optional, pre-filled on the export form; CIC's eleven modeling treaty types replace the provisional seed, still seeded by the Workbench and read from the maintained list.
- Submission-attribute filters on the EDM library and RDM library; multi-CRM-ID and "in force as of" filters on the submissions list and both libraries; the functional requirements document corrected.

## Out of scope

- Peril as a search dimension (CIC's pricing database owns it); tag search in Risk Modeler (tagging stays CRM ID only, note 29 D25); user-authored SQL in the Workbench (note 30 O30-10).
- The CRM sync itself and the reason a deal was lost; retention and archiving policy (note 18 D13).
- Client creation or edits to the repository client list; a cross-submission analyses list, an Analyses group in Ctrl-J search and a per-analysis page (P-08).

## Non-negotiable behavior

1. The two statuses never share a label, a filter, or a history: Modeling status keeps its reason-per-transition trail and its "only Active accepts changes" rule; Submission status is a plain value.
2. "In force" is computed from Submission status = Won plus a CRM ID's inception and expiration at query time. It is never stored.
3. CRM data is linked, not copied: CRM ID is the join key, and any CRM-ID-grain extract emits one row per CRM ID. The Workbench never writes to the repository client list.
4. Every filter ORs its own values and ANDs against every other filter; a library row matches when at least one of its linked submissions satisfies all submission-attribute filters together.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| P-01 | The existing status is renamed **Modeling status** and keeps Active / Completed / Cancelled; the Hold request of 8/5 is closed as not needed, since Submission status = In Process carries the paused-deal meaning | Approved | note 30 D17, O30-6; FR doc line 62; research.md Clarifications 2026-09-17 |
| P-02 | **Submission status** is Won / Lost / In Process, set by hand until a CRM sync exists; no lost reason. In Process on creation, edited in place with no history trail (assumed) | Approved | note 30 D18, O30-6 |
| P-03 | Deal-level inception stays required, remains the list's sort and filter, and is the default for every CRM ID; deal-level expiration is added, optional; each CRM ID carries an optional inception and an optional expiration, each overriding the deal's date on its own, so a CRM ID may override expiration alone; "make them all the same" clears every override | Approved | note 30 D20, D21, O30-8; research.md Clarifications 2026-09-17, 2026-09-18 |
| P-04 | Client is optional at creation and afterwards — the repository row may not exist when the deal is opened; a blank client matches no client filter | Approved | note 31 D9, O31-7; research.md Clarifications 2026-09-17 |
| P-05 | Two labels: **Cedant** (Workbench free text on the submission, unchanged) and **Client** (repository). The treaty grid's cedant column is a treaty attribute and needs no qualifier; it is respelled "Cedant", and the spelling "cedent" leaves the templates and the functional requirements document. The client dropdown shows "ID - name" and matches on ID or name | Approved | note 31 D8, O31-7 (b); research.md Clarifications 2026-09-18 |
| P-06 | The export form pre-fills Client from the submission, stays overridable, and records an override on the export only; ships once spec 014 is on `main` | Approved | note 31 D7; spec 014 P-15 |
| P-07 | Treaty types are the eleven labels below (FR-012), seeded by the Workbench; Top & Drop and Top & Aggregate are separate values; the six provisional codes are dropped, not migrated | Approved | note 30 D28, O30-12; user, 2026-09-17; spec 002 FR-030 |
| P-08 | Analysis search is out of this spec: no analyses list, no Analyses group in Ctrl-J, no per-analysis page; the analysis-search need from note 30 waits for its own spec | Approved | note 30 O30-11; research.md Clarifications 2026-09-17 |
| P-09 | In force as of date D: Submission status = Won and some CRM ID's effective inception ≤ D ≤ effective expiration; a submission with no CRM ID uses its deal-level dates; a missing expiration is never in force — it is missing data, not an open-ended contract | Approved | note 30 D19, O30-7; research.md Clarifications 2026-09-17 |
| P-10 | Multi-value filters cap at twenty values, the documented cap; each CRM ID value matches a whole CRM ID exactly, case-insensitive and trimmed, not as a substring | Approved | FR doc line 114; research.md Clarifications 2026-09-18 |
| P-11 | A CRM-ID-grain extract carries that CRM ID's effective inception and expiration, both statuses and the client, one row per CRM ID | Approved | note 30 D22, O30-9 |
| P-12 | Submission status is editable in every Modeling status (Active, Completed, Cancelled); it is the one submission field exempt from the "only Active accepts changes" rule, because the cedant's Won / Lost answer usually arrives after modeling is Completed | Approved | research.md Clarifications 2026-09-18 |
| P-13 | The EDM library and RDM library carry no Modeling status filter; their eight submission-attribute filters are the ones FR-015 lists, and Modeling status is filtered on the submissions list only | Approved | research.md Clarifications 2026-09-18 |
| P-14 | The submission page from the title to the status history is redesigned rather than extended: one metadata section (name, both statuses, owner, cedant, client, treaty type, treaty year, inception, expiration, CRM IDs with their dates) and one set of controls for changing Modeling status, changing Submission status, adding and removing CRM IDs and editing dates; a rendered preview is approved before it is built; the tables below are unchanged | Approved | user, 2026-09-18; research.md R10 |

---

## User Stories

### 1. Two statuses and dates per CRM ID (P1)

An analyst opens a submission and reads two things that used to be one: where the modeling stands (Modeling status) and where the deal stands with the cedant (Submission status). Marking the deal Won records what CRM knows without a reason. Under the CRM IDs, each contract shows its own inception and expiration; the annual and the three-year treaty on the same modeling data differ in expiration, so the analyst enters both and, on the next deal where all four contracts match, presses one control to make them the same.

**Acceptance**

1. **Given** any submission, **When** the analyst opens its page, **Then** it shows a **Modeling status** (Active, Completed or Cancelled) and a **Submission status** (Won, Lost or In Process) as two separately labelled fields, and the status history trail belongs to Modeling status only.
2. **Given** a submission In Process, **When** the analyst sets Submission status to Lost, **Then** the value saves with no reason asked and Modeling status is unchanged.
3. **Given** a submission with three CRM IDs and no per-CRM dates, **When** the analyst opens its page, **Then** each CRM ID shows the deal-level inception and expiration, rendered the same as a date entered on the CRM ID.
4. **Given** a CRM ID, **When** the analyst enters an expiration three years after the deal's, **Then** only that CRM ID shows the new expiration and the other CRM IDs are unchanged.
5. **Given** four CRM IDs with differing dates, **When** the analyst chooses "make them all the same", **Then** all four read the deal-level inception and expiration after one confirmation.
6. **Given** the Modeling status control and filter, **Then** neither offers a Hold value; the values are Active, Completed and Cancelled only.
7. **Given** the submissions list, **When** the analyst filters on Submission status = Won, **Then** the Modeling status filter is a separate control and the two never appear in one menu.
8. **Given** a submission whose Modeling status is Completed or Cancelled, **When** the analyst sets Submission status to Won, **Then** the value saves without reopening the submission; every other field stays locked.

### 2. Client and treaty type from CIC's lists (P1)

Creating a submission, the analyst picks the client from CIC's repository client list — typing "27" or "Travelers" finds "27 - Travelers Corporate Cat" — and picks a treaty type from CIC's own modeling list rather than the Workbench's guess. Later, opening the export form from that submission, the client is already chosen and can be changed for that export alone.

**Acceptance**

1. **Given** the create form, **When** the analyst types a client ID or a fragment of a client name, **Then** the menu offers matching repository clients displayed as "ID - name", and saving records the chosen client on the submission.
2. **Given** the create form, **When** the analyst leaves Client blank, **Then** the submission saves (name, cedant, treaty type and inception stay the required fields).
3. **Given** the repository is unreachable, **When** the analyst opens the create form, **Then** the client field says the list is unavailable and the submission can still be saved without one.
4. **Given** the treaty type menu on the create form and the submissions list filter, **Then** both offer exactly the eleven treaty types of FR-012, and a change to the maintained list appears in both without a code change.
5. **Given** a submission with client 27, **When** the analyst opens the export form from it, **Then** Client reads 27 and treaty inception reads the effective inception of the pre-filled CRM ID; **When** the analyst changes Client to 41 and exports, **Then** the export records 41 and the submission still reads 27. Verified after spec 014 is on `main` (FR-011).
6. **Given** the submission page, **Then** the Workbench field is labelled Cedant and the repository field is labelled Client; **Given** the EDM detail treaty grid, **Then** the treaty's cedant column reads Cedant, and no page spells it "cedent".

### 3. Find EDMs and RDMs by the deals they belong to (P1)

An earthquake hits New York. The analyst arrives at the EDM library with a list of Travelers CRM IDs from the pricing database, pastes six of them into the CRM ID filter, ticks Submission status = Won and "in force as of today", and gets the EDMs behind every bound Travelers deal currently on risk — then narrows to Cheryl's by owner. Nothing in Risk Modeler's tag search could do this.

**Acceptance**

1. **Given** the EDM library, **When** the analyst enters six CRM IDs, **Then** every EDM linked to a submission carrying any of the six is listed (values OR within the filter), and an EDM linked to two matching submissions is listed once; a submission whose CRM ID merely contains one of the six ("12345" for "1234") is not matched.
2. **Given** the EDM library with Submission status = Won and owner = Cheryl, **Then** an EDM is listed only if at least one of its linked submissions is both Won and owned by Cheryl — a Won deal of Ben's and an In Process deal of Cheryl's sharing the EDM do not qualify it.
3. **Given** the RDM library, **When** the analyst filters on cedant, client, treaty type, treaty year, CRM ID or Submission status, **Then** the same filters and semantics apply as on the EDM library, together with the existing name search and import-status filter.
4. **Given** the submissions list, **When** the analyst ticks "in force as of" and leaves the date at today, **Then** only submissions Won with a CRM ID whose effective inception ≤ today ≤ effective expiration are listed; changing the date to next January 1st changes the result.
5. **Given** a Won submission whose CRM ID has no expiration, **When** in force is applied, **Then** it is not listed.
6. **Given** the sync-from-Risk-Modeler screen, **Then** it is unchanged: name search and paging only.
7. **Given** any list, **When** the analyst enters twenty-one CRM IDs, **Then** the page refuses with the existing one-line message naming the filter and the cap.
8. **Given** an EDM linked to no submission, **When** any submission-attribute filter is set, **Then** it is not listed; with none set, it is.

## Requirements

- **FR-001**: The existing submission status is presented as **Modeling status** everywhere the analyst sees it — submission page, history trail, list filter, Ctrl-J metadata — with the values Active, Completed and Cancelled (P-01). Its rules are unchanged: every transition records a reason, transitions are reversible, only Active accepts changes. No Hold value is added.
- **FR-002**: A submission carries a **Submission status** of Won, Lost or In Process (P-02), In Process on creation, editable on the submission page by any analyst in every Modeling status including Completed and Cancelled (P-12), with no reason captured (P-02). It is displayed, filtered and edited apart from Modeling status and never shares a control with it.
- **FR-003**: A submission carries a deal-level inception (required, unchanged) and a deal-level expiration (new, optional). Each CRM ID carries an optional inception and an optional expiration; each date that is blank inherits the deal's date on its own, so a CRM ID may override expiration while inheriting inception (P-03).
- **FR-004**: The submission page shows each CRM ID with its effective inception and expiration, an inherited date reading the same as an entered one. The analyst adds a CRM ID with its dates, edits a CRM ID's dates in place, and removes a CRM ID together with its dates.
- **FR-005**: A "make them all the same" control on the submission page sets every CRM ID to the deal-level dates in one confirmed action (P-03).
- **FR-006**: The submissions list's default sort, its inception filter and the treaty-year default use the deal-level inception (P-03).
- **FR-007**: A CRM ID is unique within a submission; it stays free text without format validation and a submission may have none (spec 002 FR-018 carried).
- **FR-008**: A submission may carry one **Client** chosen from the repository client list, optional at creation and afterwards (P-04); the menu displays "ID - name" and matches a typed ID or name fragment (P-05). The Workbench never creates or edits a client.
- **FR-009**: When the repository client list is unavailable, the client field says so and the submission still saves; the value stored is the client ID, shown with its name when the list is reachable.
- **FR-010**: Two labels: **Cedant** for the Workbench free-text field on the submission (unchanged, still a typeahead over existing values) and **Client** for the repository client (P-05). The treaty grid on the EDM detail page keeps its cedant column, a treaty attribute, labelled Cedant; the spelling "cedent" is removed from every template.
- **FR-011**: The export form pre-fills Client from the submission and keeps it editable; treaty inception pre-fills from the effective inception of the pre-filled CRM ID. An edited client or inception is recorded on the export only (P-06; spec 014 P-15 extended). Applies once spec 014 is on `main`.
- **FR-012**: The treaty-type list, seeded by the Workbench, is exactly: Aggregate XOL, Aggregate Cat XOL, Risk Aggregate XOL, Per Occurrence XOL, Per Occurrence Cat XOL, Per Risk XOL, Stop Loss, Reinstatement Premium Protection, Second/Third/Fourth Event - Risk Exposed, Top & Drop, Top & Aggregate (P-07). The create and edit forms and every treaty-type filter read the maintained list, so a reference-data change needs no code change (spec 002 FR-030 closed).
- **FR-013**: Any CRM-ID-grain extract of submissions emits one row per CRM ID carrying that CRM ID, its effective inception and expiration, both statuses and the client; a submission with no CRM ID emits one row with a blank CRM ID (P-11).
- **FR-014**: The submissions list gains a multi-value CRM ID filter (each value matches a whole CRM ID exactly, case-insensitive and trimmed; OR within; P-10), a Submission status filter, a Client filter and an "in force as of" filter, each ANDed with the existing filters.
- **FR-015**: The EDM library and RDM library gain filters on the linked submissions' owner, cedant, client, treaty type, treaty year, CRM ID, Submission status and "in force as of", beside the existing name search and import-status filter. Owner has no default on the libraries. Modeling status is not a library filter (P-13).
- **FR-016**: Filter semantics on every list: values OR within a filter; filters AND across. On the EDM library and RDM library an EDM or RDM matches when at least one of its linked submissions satisfies every submission-attribute filter together; name search and import status apply to the EDM or RDM itself. An EDM or RDM linked to no submission matches no submission-attribute filter.
- **FR-017**: The sync-from-Risk-Modeler screen is unchanged.
- **FR-018**: "In force as of" takes a date defaulting to today and lists submissions, EDMs or RDMs whose submission is Won and has a CRM ID whose effective inception ≤ date ≤ effective expiration; a submission with no CRM ID uses its deal-level dates; a missing expiration never qualifies (P-09). It is available on the submissions list, the EDM library and the RDM library and is never stored.
- **FR-019**: Every multi-value filter, new and existing, caps at twenty values and refuses an over-cap request with the existing single message naming the filter (P-10).
- **FR-020**: The functional requirements document is corrected: global search exists with six groups (pages, submissions, EDMs, RDMs, analysis templates, users); the EDM and RDM libraries already have a name search and an import-status filter; the parked CRM integration item lists expiration date and Submission status with their two consumers, event response and retention; "cedent" is spelled "cedant" and the line-50 remark that cedent "is also a specific EDM field" names the treaty attribute instead (P-05).
- **FR-021**: The submission page's metadata section and the controls for Modeling status, Submission status, CRM IDs and inception / expiration are redesigned as one screen (P-14). A rendered preview covering an Active deal with CRM IDs, a deal with no CRM ID, a Completed deal and an unreachable repository is approved before templates or routes are written. The EDM, RDM, analyses and exports tables are unchanged.

## Key Entities

- **Submission**: the deal. Gains a Submission status, an optional client, an optional deal-level expiration; its existing status becomes Modeling status.
- **CRM ID**: a contract on the deal and the join key to CIC's systems. Now carries its own optional inception and expiration; effective dates fall back to the deal's.
- **Client**: a row of CIC's repository client list, identified by client ID, read-only for the Workbench; distinct from the Workbench cedant string on the submission.
- **Treaty type**: CIC's eleven modeling reinsurance structures, a list the Workbench seeds and maintains.
- **In force as of a date**: a query over Submission status = Won and a CRM ID's effective dates, never a stored value.

## Success Criteria

- **SC-001**: Given up to twenty CRM IDs from the pricing database, an analyst lists the in-force Won submissions and their EDMs in one filter pass, in under a minute, without opening Risk Modeler.
- **SC-002**: Every submission page shows Modeling status and Submission status under those two labels; no control offers values of both.
- **SC-003**: The treaty-type menu and filter show the eleven values of FR-012, and a reference-data change reaches both with zero code changes.
- **SC-004**: On a deal whose CRM IDs share dates — the 85% case — the analyst enters the dates once and one action applies them to every CRM ID.
