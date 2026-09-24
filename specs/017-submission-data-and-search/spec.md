# Feature Specification: Submission Data and Cross-Entity Search (Iteration 12)

**Branch**: `017-submission-data-and-search` | **Created**: 2026-09-17 | **Amended**: 2026-09-23 (the bulk Contract status update script, note 32 D25; its source table and Open, note 34 D9–D12)

## Status

**Phase:** Amended — the contract grain replaces the submission-level deal status and dates built on 2026-09-18
**Blocking:** Nothing

## Outcome

An analyst records what CRM knows about a deal at the grain CRM holds it: each **contract** (a CRM ID) carries its own treaty type, inception, expiration and Won / Lost / Open status, while the submission keeps the modeling work, the cedant, the client and one data vintage. The analyst finds EDMs and RDMs by those contract and deal attributes, including "in force as of a date" and a pasted list of CRM IDs. One modeling package with eight CRM IDs, three of them bound, is one submission with eight contracts.

## In scope

- **Contract** as the entity behind a CRM ID: CRM ID, treaty type, inception, expiration, contract status. A submission carries none, one or many, managed in full on the create form and on the submission page.
- **Modeling status** (Active, Completed, Cancelled) stays the submission's own status with its history; **Contract status** (Won, Lost, Open) is per contract, a plain value.
- The submission loses its treaty type, inception, expiration and deal status; it gains an optional **data vintage**. Treaty year stays on the submission.
- A **Client** from CIC's repository client list on the submission, optional, labelled "Client ID"; CIC's eleven treaty types read from the maintained list.
- The export form pre-fills client and data vintage from the submission and offers the submission's contracts for CRM ID and treaty inception.
- Submission-attribute filters on the EDM and RDM libraries; multi-CRM-ID, contract status, client and "in force as of" filters on the submissions list and both libraries; the list sorted by the latest contract inception.
- The **bulk Contract status update**: the SQL script CIC runs against `contract` after CRM closes out a renewal date, reading one status per CRM ID from `dbo.CRMContractStatus` in CIC's loss repository (note 32 D25; note 34 D10–D12).

## Out of scope

- Peril as a search dimension; tag search in Risk Modeler (tagging stays CRM ID only, note 29 D25); user-authored SQL in the Workbench (note 30 O30-10).
- A CRM sync: the Workbench never reads CRM; CIC's loss repository holds the CRM status table the bulk update script reads (note 34 D11, D12). The nightly schedule for that script (note 34 D13, O34-3). The reason a deal was lost; retention and archiving policy (note 18 D13); an "expired more than N years ago" filter (note 32 D26).
- Client creation or edits to the repository client list; a cross-submission analyses list, an Analyses group in Ctrl-J and a per-analysis page (P-08); searching Risk Modeler's own EDMs, RDMs and analyses, and filters for EDMs linked to no submission or excluding industry databases (note 32 D28–D30, held).
- Ordering contracts within a submission, or designating one as primary (P-17).

## Non-negotiable behavior

1. Modeling status belongs to the submission and keeps its reason-per-transition trail and its "only Active accepts changes" rule; contract status belongs to the contract and is a plain value. The two never share a label, a filter, a control or a history.
2. "In force" is computed per contract from contract status = Won plus that contract's inception and expiration at query time. It is never stored.
3. CRM data is linked, not copied: the CRM ID identifies a contract and is the key CIC's extracts and bulk updates use. The Workbench never writes to the repository client list.
4. Every filter ORs its own values and ANDs against every other filter. A submission matches its contract-level filters when at least one of its contracts satisfies all of them together; a library row matches when at least one of its linked submissions satisfies every submission filter together.

## Open product decisions

Status words appear here only. "Confirmed with client on" is the date a built screen was shown and accepted; blank means derived from the cited notes and not yet demoed.

| ID | Decision | Status | Derived from | Confirmed with client on |
|---|---|---|---|---|
| P-01 | The existing status is **Modeling status**, Active / Completed / Cancelled, with the only history trail; the 8/5 Hold request is closed because Open carries the paused-deal meaning | Approved | note 30 D17, O30-6; research.md 2026-09-17 | 2026-09-18 (note 32 D16) |
| P-02 | **Contract status** is Won / Lost / Open on each contract, replacing the submission-level deal status built 9/18; Open on creation; edited in place with no reason and no history | Approved | note 32 D17, D18; user 2026-09-21 | 2026-09-18 (the client's own decision on the call) |
| P-03 | Inception and expiration live on the contract only; both required; expiration defaults to inception plus one year minus one day; the submission carries no dates. A new contract row pre-fills its dates from the row entered before it; the 9/15 "make them all the same" control is removed | Approved | note 32 D15, D20, D22; user 2026-09-21 | |
| P-04 | Client is optional at creation and afterwards; a blank client matches no client filter | Approved | note 31 D9, O31-7; research.md 2026-09-17 | 2026-09-18 (note 32 D14) |
| P-05 | Two labels: **Cedant** (Workbench free text) and **Client ID** (repository row, shown as "ID - name", matched on ID or name). The treaty grid column is "Cedant"; "cedent" is gone from templates and the FR doc | Approved | note 31 D8; note 32 D14; user 2026-09-21 | 2026-09-18 |
| P-06 | The export form pre-fills client and data vintage from the submission and offers the submission's contracts: with exactly one it is picked on open; picking one fills CRM ID and treaty inception. Every value stays editable and an edit is recorded on the export only | Approved | note 31 D7; note 32 D21; spec 014 P-15; user 2026-09-21 | |
| P-07 | Treaty types are the eleven labels of FR-012, seeded by the Workbench; the six provisional codes are dropped, not migrated | Approved | note 30 D28, O30-12; user 2026-09-17 | 2026-09-18 (note 32 D13) |
| P-08 | Analysis search is out of this spec | Approved | note 30 O30-11; research.md 2026-09-17 | |
| P-09 | In force as of date D: a contract whose status is Won and whose inception ≤ D ≤ expiration; a submission with no contract is never in force | Approved | note 30 D19; note 32 D24; user 2026-09-21 | 2026-09-18 (the missing Won leg found live) |
| P-10 | Multi-value filters cap at twenty; each CRM ID value matches a whole CRM ID exactly, case-insensitive and trimmed | Approved | FR doc line 114; research.md 2026-09-18 | |
| P-11 | The CRM-ID-grain extract is one row per contract carrying the contract's attributes, both statuses, the cedant, the client and the data vintage; a submission with no contract emits no row | Approved | note 30 D22, O30-9; note 32 D25 | |
| P-12 | Contract status is editable in every Modeling status; it is the one attribute exempt from "only Active accepts changes", because the Won / Lost answer arrives after modeling is Completed | Approved | research.md 2026-09-18 | |
| P-13 | The EDM and RDM libraries carry no Modeling status filter | Approved | research.md 2026-09-18 | |
| P-14 | The submission page above the tables is one deal card; with this amendment its Treaty and Term groups and the CRM band become one **contract table** with headers, and a new rendered preview is approved before it is built | Approved | user 2026-09-18; note 32 D13–D16; user 2026-09-21 | 2026-09-18 (the card as built that morning) |
| P-15 | Treaty type is a contract attribute; the submission has none. One submission may hold contracts of different types; CIC still opens a second submission when the modeling differs | Approved | note 32 D19; user 2026-09-21 | 2026-09-18 |
| P-16 | A submission carries zero or more contracts. The create form manages them in full (none, one or many, every attribute); name and cedant are the only fields required at creation. A contract requires its CRM ID, unique across the Workbench case-insensitively | Approved | note 32 O32-7; user 2026-09-21; note 33 D12–D14 | 2026-09-22 (the client's own decision on the call) |
| P-17 | No contract is primary. The list's default sort is the latest contract inception descending, then name; a submission with no contract sorts by its creation date in the same key. Any place that needs one contract's value asks the analyst or aggregates | Approved | user 2026-09-21 | |
| P-18 | Contract-level filters (CRM ID, treaty type, inception, contract status, in force) are evaluated against one contract row together; submission-level filters (owner, cedant, client, treaty year, Modeling status, name) against the submission | Approved | user 2026-09-21; FR-016 one level up | |
| P-19 | **Data vintage** is one optional date on the submission, the in-force as-of date of the data CIC received in the EDM; the export's required data vintage pre-fills from it | Approved | note 32 D23; user 2026-09-21 | 2026-09-18 |
| P-20 | Treaty year stays on the submission; the form fills it from the data vintage and the server from the earliest contract inception when blank; it may be blank on a submission with no contract | Approved | FR doc line 49; user 2026-09-21 | |
| P-21 | The January bulk update is a SQL script CIC runs against `rwb_workbench`, not a screen. Its source is `dbo.CRMContractStatus` in CIC's loss repository, one row per CRM ID with the status CRM holds (Open, Won, Lost): Cheryl's table now, Ross's view over the linked CRM copy in production. Every Workbench contract whose CRM ID appears in the source is set to the source's status; a dry run first; a status the Workbench lacks or a CRM ID spelled twice writes nothing; CRM IDs with no contract are counted and skipped; contracts with no source row are left alone | Approved | note 32 D25; note 33 §10; note 34 D10–D13 | 2026-09-23 (note 34 D10–D12) |
| O-01 | The status words in CIC's CRM are Open, Won, Lost, and the Workbench uses the same words since D9 renamed In Process to Open; the script maps nothing | Approved | note 34 D9 (Wendy: "literally open, won, or lost") | 2026-09-23 (note 34 D9) |

---

## User Stories

### 1. Contracts on a submission (P1)

Jessica opens one submission for Allstate's modeling package and enters eight CRM IDs on the create form, each with its treaty type and dates: the cat program first, then the aggregate, then the top layer. Adding each row pre-fills the previous row's dates, so she types the dates once and changes only the three-year layer's expiration. In January three of the eight are bound; she sets those three contracts to Won and the rest to Lost without touching the Modeling status, which is already Completed. The page reads as Cheryl's own table: here are the contracts, here is the status on each.

**Acceptance**

1. **Given** the create form, **When** the analyst saves with name and cedant and no contract, **Then** the submission saves with no treaty type, inception or expiration, treaty year blank, and its page shows an empty contract table with an Add control.
2. **Given** the create form, **When** the analyst adds three contract rows, **Then** each row takes a CRM ID, treaty type, inception, expiration and status (Open by default), the second and third rows open with the first row's dates already filled, typing an inception fills that row's expiration as inception plus one year minus one day (the server does the same for a blank expiration), and saving writes the submission and its three contracts together.
3. **Given** a contract row on the create form or the page, **When** its CRM ID is blank or repeats another contract's on the same submission, **Then** the save is refused with a message under that row; **When** it repeats a contract's on another submission, **Then** the save is refused and the message under that row names and links that submission.
4. **Given** any submission, **When** the analyst opens its page, **Then** the deal card shows **Modeling status** with its history and the contract table shows each contract's CRM ID, treaty type, inception, expiration and **Contract status** under those headers; no control shows both statuses.
5. **Given** a submission whose Modeling status is Completed or Cancelled, **When** the analyst sets one contract to Won and another to Lost, **Then** both save with no reason asked, the Modeling status and its history are unchanged, and every other field stays locked.
6. **Given** an Active submission, **When** the analyst edits one contract's expiration in place, **Then** only that contract changes; **When** the analyst removes a contract, **Then** its status and dates go with it.
7. **Given** the Modeling status control and filter, **Then** neither offers a Hold value.
8. **Given** the submissions list, **Then** each row shows the submission's CRM IDs and distinct treaty types as "first + N more" and its latest contract inception, and the default order is latest inception descending, a submission with no contract placed by its creation date.

### 2. Client, data vintage and treaty type from CIC's lists (P1)

Creating a submission, the analyst picks the client from CIC's repository client list under the label Client ID (typing "27" or "Travelers" finds "27 - Travelers Corporate Cat"), picks each contract's treaty type from CIC's own modeling list, and enters the one data vintage the package was received at. Opening the export form from that submission, client and data vintage are already filled, the single contract is already picked, and its CRM ID and inception sit in the form ready to change for that export alone.

**Acceptance**

1. **Given** the create form, **When** the analyst types a client ID or a fragment of a client name, **Then** the menu offers matching repository clients as "ID - name" under the label **Client ID**, and saving records the chosen client on the submission.
2. **Given** the create form, **When** the analyst leaves Client ID and data vintage blank, **Then** the submission saves.
3. **Given** the repository is unreachable, **When** the analyst opens the create form, **Then** the client field says the list is unavailable and the submission still saves without one.
4. **Given** a contract row's treaty type menu and the lists' treaty type filter, **Then** both offer exactly the eleven treaty types of FR-012, and a change to the maintained list appears in both without a code change.
5. **Given** a submission with client 27, data vintage 2026-06-30 and one contract `T-100` incepting 2027-01-01, **When** the analyst opens the export form from it, **Then** Client reads 27, data vintage reads 2026-06-30, CRM ID reads `T-100` and treaty inception reads 2027-01-01; **Given** two contracts, **Then** nothing is picked until the analyst chooses one, and choosing fills CRM ID and inception; **When** the analyst changes any of them and exports, **Then** the export records the changed values and the submission is unchanged.
6. **Given** the submission page, **Then** the free-text field is labelled Cedant and the repository field Client ID; the EDM detail treaty grid's cedant column reads Cedant, and no page spells it "cedent".

### 3. Find EDMs and RDMs by the deals they belong to (P1)

An earthquake hits New York. The analyst arrives at the EDM library with a list of Travelers CRM IDs from the pricing database, pastes six of them into the CRM ID filter, ticks Contract status = Won and "in force as of today", and gets the EDMs behind every bound Travelers contract currently on risk, then narrows to Cheryl's by owner. A submission where one contract was bound and another lost qualifies on the bound one alone.

**Acceptance**

1. **Given** the EDM library, **When** the analyst enters six CRM IDs, **Then** every EDM linked to a submission with a contract carrying any of the six is listed once; a CRM ID that merely contains one of the six is not matched.
2. **Given** the EDM library with Contract status = Won and owner = Cheryl, **Then** an EDM is listed only if one linked submission is owned by Cheryl and has a Won contract; a Won deal of Ben's and an Open deal of Cheryl's sharing the EDM do not qualify it.
3. **Given** a submission with contract `T-100` Won and `T-200` Lost, both incepting 2026-01-01 and expiring 2026-12-31, **When** the analyst ticks "in force as of" 2026-06-01 on the submissions list, **Then** the submission is listed; **When** `T-100` is set to Lost, **Then** it is not.
4. **Given** the filters Treaty type = Per Risk XOL and Contract status = Won, **Then** a submission whose only Won contract is an Aggregate XOL is not listed even if it has a Per Risk XOL contract Open (P-18).
5. **Given** the RDM library, **When** the analyst filters on cedant, client, treaty type, treaty year, CRM ID, contract status or in force, **Then** the same filters and semantics apply as on the EDM library, together with the existing name search and import-status filter.
6. **Given** a Won contract whose expiration has passed, or a submission with no contract, **When** in force is applied, **Then** it is not listed.
7. **Given** the sync-from-Risk-Modeler screen, **Then** it is unchanged: name search and paging only.
8. **Given** any list, **When** the analyst enters twenty-one CRM IDs, **Then** the page refuses with the existing one-line message naming the filter and the cap; **Given** an EDM linked to no submission, **Then** it is listed only when no submission-attribute filter is set.

## Requirements

- **FR-001**: The existing submission status is presented as **Modeling status** everywhere the analyst sees it, with the values Active, Completed and Cancelled (P-01). Its rules are unchanged: every transition records a reason, transitions are reversible, only Active accepts changes. No Hold value.
- **FR-002**: Each contract carries a **Contract status** of Won, Lost or Open (P-02), Open on creation, editable on the submission page by any analyst in every Modeling status (P-12), with no reason and no history. The submission itself carries no deal status. Contract status is displayed, filtered and edited apart from Modeling status and never shares a control with it.
- **FR-003**: A contract carries a CRM ID (required, free text without format validation, unique across the Workbench case-insensitively; a CRM ID already on another submission refuses the save with a message naming and linking that submission, whatever either submission's status — D14), a treaty type from the maintained list (required), an inception (required), an expiration (required; when left blank it is set to inception plus one year minus one day) and a contract status (P-03, P-15, P-16). The submission carries no treaty type, inception or expiration.
- **FR-004**: A submission carries zero or more contracts. The create form lets the analyst add, edit and remove contract rows with every attribute before saving; the submission and its contracts are written together. Name and cedant are the only fields required at creation (P-16). On the submission page the analyst adds a contract, edits a contract's attributes in place and removes a contract; attribute edits other than contract status are gated on Modeling status Active like every other field.
- **FR-005**: A new contract row, on the create form or the page, pre-fills its inception and expiration from the contract row entered before it; the first row starts blank (P-03).
- **FR-006**: Treaty year stays on the submission. The form fills it from the data vintage until the analyst types a year, and the server fills it from the earliest contract inception when it is left blank; it stays editable and may be blank on a submission with no contract (P-20).
- **FR-007**: The submissions list's default order is the latest contract inception descending, then name; a submission with no contract is placed by its creation date in the same key (P-17). The inception filter matches a submission whose contract incepts on that date. Each row shows the CRM IDs and the distinct treaty types as "first + N more" and the latest inception across the submission's contracts; contract status is not a list column (the Contract status filter and the deal card carry it).
- **FR-008**: A submission may carry one **Client** chosen from the repository client list, optional at creation and afterwards (P-04); the field is labelled Client ID, displays "ID - name" and matches a typed ID or name fragment (P-05). The Workbench never creates or edits a client.
- **FR-009**: When the repository client list is unavailable, the client field says so and the submission still saves; the value stored is the client ID, shown with its name when the list is reachable.
- **FR-010**: Two labels: **Cedant** for the Workbench free-text field (unchanged, still a typeahead over existing values) and **Client ID** for the repository client (P-05). The treaty grid on the EDM detail page keeps its cedant column labelled Cedant; the spelling "cedent" appears in no template.
- **FR-011**: The export form pre-fills Client and data vintage from the submission and offers the submission's contracts; with exactly one contract it is picked on open, and picking a contract fills CRM ID and treaty inception (P-06). Every pre-filled value stays editable and an edited value is recorded on the export only (spec 014 P-15).
- **FR-012**: The treaty-type list, seeded by the Workbench, is exactly: Aggregate XOL, Aggregate Cat XOL, Risk Aggregate XOL, Per Occurrence XOL, Per Occurrence Cat XOL, Per Risk XOL, Stop Loss, Reinstatement Premium Protection, Second/Third/Fourth Event - Risk Exposed, Top & Drop, Top & Aggregate (P-07). Every treaty-type menu and filter reads the maintained list.
- **FR-013**: The CRM-ID-grain extract of submissions is one row per contract carrying the CRM ID, treaty type, inception, expiration, contract status, the submission's name, cedant, client, treaty year, data vintage and Modeling status; a submission with no contract emits no row (P-11).
- **FR-014**: The submissions list carries a multi-value CRM ID filter (each value matches a whole CRM ID exactly, case-insensitive and trimmed; P-10), a Contract status filter, a Client filter and an "in force as of" filter, each ANDed with the existing filters.
- **FR-015**: The EDM library and RDM library carry filters on the linked submissions' owner, cedant, client, treaty type, treaty year, CRM ID, Contract status and "in force as of", beside the existing name search and import-status filter. Owner has no default on the libraries. Modeling status is not a library filter (P-13).
- **FR-016**: Values OR within a filter; filters AND across. Contract-level filters (CRM ID, treaty type, inception, contract status, in force) match a submission when one of its contracts satisfies all of them together (P-18). On the libraries an EDM or RDM matches when one linked submission satisfies every submission-attribute filter together; name search and import status apply to the EDM or RDM itself. An EDM or RDM linked to no submission matches no submission-attribute filter.
- **FR-017**: The sync-from-Risk-Modeler screen is unchanged.
- **FR-018**: "In force as of" takes a date defaulting to today and lists submissions, EDMs or RDMs having a contract whose status is Won and whose inception ≤ date ≤ expiration (P-09). It is available on the submissions list and both libraries and is never stored.
- **FR-019**: Every multi-value filter caps at twenty values and refuses an over-cap request with the existing single message naming the filter (P-10).
- **FR-020**: The functional requirements document is corrected: a submission is one cedant's modeling package holding zero or more contracts, each a CRM ID with its own treaty type, dates and status (lines 44, 47, 48, 57); name and cedant are required at creation (line 52); treaty year defaults from the earliest contract inception (line 49); the status row names Modeling status and Contract status (line 62); the list filters row (line 115); the labels Cedant and Client ID (line 50); data vintage defined in Wendy's words. The 9/18 corrections to global search and the library filters stand.
- **FR-021**: The submission page's deal card is amended: its Treaty and Term groups and the CRM band become one contract table with headers and in-place row editing, and the create form gains the contract editor of FR-004 (P-14). A rendered preview covering the create form with three rows, an Active deal's contract table with mixed statuses, a deal with no contract, a Completed deal (only contract status editable) and the repository unreachable is approved before templates or routes are written. The EDM, RDM, analyses and exports tables are unchanged.
- **FR-022**: A submission carries an optional **data vintage** date, the in-force as-of date of the data CIC received in the EDM, entered on the create form and edited on the deal card; it pre-fills the export's data vintage (P-19).
- **FR-023**: The Workbench ships `infra/scripts/bulk_update_contract_status.sql`, the SQL script CIC runs against `rwb_workbench`. It reads `dbo.CRMContractStatus` in CIC's loss repository, one row per CRM ID with its status (Open, Won or Lost, any case); the one line that names that database is the only edit between environments. Each contract whose CRM ID appears in the source has its Contract status set to the source's status in place, its `updated_at` moved and `updated_by` cleared; no reason, no history, no screen. Three result sets: a summary with counts (rows read, to update, already at status, not in the Workbench, unknown status, duplicate CRM IDs), the problem rows, and the change list. A dry run reports first. The script writes nothing when a status is unknown or a CRM ID is spelled twice; a CRM ID the Workbench has no contract for is counted and skipped; a contract with no source row is untouched (P-21).

## Key Entities

- **Submission**: the modeling package for one cedant. Carries name, cedant, client, treaty year, data vintage, Modeling status, owner, and zero or more contracts. No treaty type, no dates, no deal status of its own.
- **Contract**: one CRM ID on one submission, with its treaty type, inception, expiration and contract status. The CRM ID is unique across the Workbench. The join key to CIC's systems and the row the January bulk update sets from CIC's CRM status table.
- **Client**: a row of CIC's repository client list, identified by client ID, read-only for the Workbench; distinct from the Workbench cedant string.
- **Treaty type**: CIC's eleven modeling reinsurance structures, a list the Workbench seeds and maintains; an attribute of a contract.
- **In force as of a date**: a query over a contract's status and dates, never a stored value.

## Success Criteria

- **SC-001**: Given up to twenty CRM IDs from the pricing database, an analyst lists the in-force Won contracts' submissions and their EDMs in one filter pass, in under a minute, without opening Risk Modeler.
- **SC-002**: Every submission page shows Modeling status on the deal card and Contract status per contract row; no control offers values of both.
- **SC-003**: The treaty-type menu and filter show the eleven values of FR-012, and a reference-data change reaches both with zero code changes.
- **SC-004**: On a deal whose contracts share dates, the analyst types the dates once; every further row opens with them filled.
- **SC-005**: A submission with eight contracts, three Won and five Lost, is one record, reads as one table on its page, and is in force exactly when one of the three Won contracts is on risk.
