# Feature Specification: Loss Results Export (Iteration 11)

**Branch**: `014-results-export` | **Created**: 2026-09-09

## Status

**Phase:** Tasks generated · **Blocking:** Nothing for the spec. End-to-end verification waits on CIC loading the repository tables and creating the Workbench login (plan O-05).

## Outcome

An analyst exports the event loss table of one or more finished analyses into CIC's loss repository from the Workbench, with the loss rows split into stochastic and historical tables and two known data defects corrected automatically. Today this needs RiskLink, the workflow tool, and a hand check of the loaded rows.

## In scope

- Export form on a submission's analyses page: one or more finished analyses (own, broker, or group), one financial perspective from a configured set (first set GU, GR, RL, RP), a client, treaty inception, CRM ID, optional data name per analysis, optional data vintage.
- Automatic processing per analysis with no review step: request the loss table from Risk Modeler, download it, stage it, classify each event as historical or stochastic, correct exposure below loss and negative standard deviation, load the header row and the two loss tables. Every analysis's historical rows go to the historical table, even when there are none.
- An exports section on the submission page, one row per export, and an export detail page showing each analysis's progress, the loaded data ID, row counts, how many rows each correction changed, and Retry for a failed analysis (O-06).
- The downloaded archive kept permanently on a shared drive as the record of what was loaded.

## Out of scope

- HD analyses and period loss tables (PLT); the TY treaty perspective (P-08). Fetching loss tables before the analyst clicks Export.
- Editing or deleting a loaded data set; exporting the same analysis and perspective a second time (P-09); cancelling an accepted export or one of its analyses (P-14). The workflow tool edits data name and vintage.

## Non-negotiable behavior

1. **One data set per analysis per perspective, ever.** An analysis that already has an export for the chosen perspective, in any status, cannot be exported again. Failures are fixed with Retry, never by a second export.
2. **Model losses are never altered.** Corrections change only exposure value and standard deviation, and every changed row is counted and shown.
3. **Historical classification is validated against CIC's historical event lookup**, never inferred from analysis settings. A model version missing from the lookup fails the load; it does not load everything as stochastic.
4. **Each analysis loads on its own, all rows or nothing.** One failing analysis never affects another, and no partial data set is ever visible in the repository.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| P-02 | One perspective per export, offered as the codes every selected analysis has results for. | Approved | note 21 |
| P-03 | Loss greater than exposure: exposure set equal to loss. Negative standard deviation: set to 0 on stochastic rows only (P-04). Both automatic, both reported with row counts. | Approved | note 24 D12–D15 |
| P-08 | ELT export for DLM analyses first. HD/PLT (O-08: PLT destination, `HDv2.1` as model version, HD classification) and TY (O-02: how Risk Modeler exports treaty results) are built later. | Approved | user, 2026-09-03 |
| P-09 | Repeat export of an analysis and perspective is blocked with no override, until O-01 (whether CIC ever needs a reload, and who clears the block) is decided. | Approved | user, 2026-09-09 |
| P-10 | Broker and group analyses export the same way as own analyses. PRD §17.4 (broker export out of MVP) is superseded and gets updated. | Approved | user, 2026-09-09 |
| P-11 | Any authenticated analyst can export and retry; no role gate. | Approved | user, 2026-09-09 |
| O-06 | Exports live in their own table on the submission page, one row per export, each opening an export detail page. The analyses grid shows no export status, no link, and no modal; the analysis status vocabulary is unchanged. | Approved | user, 2026-09-09 |
| O-07 | The per-analysis data name field (P-06: one optional field per analysis, never required) ships in story 1. | Approved | user, 2026-09-09 |
| P-12 | An analysis whose Risk Modeler export yields zero loss rows fails at stage with a message naming the perspective; nothing is loaded. A header row with no loss rows is never written. | Approved | [research.md#clarifications](research.md#clarifications), 2026-09-09 |
| P-13 | The Workbench never times out a Risk Modeler export request. An analysis stays in progress until Risk Modeler reports a terminal status; Retry is offered only after a failure. A stuck row is cleared under O-01. | Approved | [research.md#clarifications](research.md#clarifications), 2026-09-09 |
| P-14 | No cancel. Once Export is clicked each analysis processes to loaded or failed; there is no confirmation step and no cancel action. A wrong client or date is corrected by CIC under O-01, like any other reload. | Approved | [research.md#clarifications](research.md#clarifications), 2026-09-10 |
| P-15 | Treaty inception and CRM ID edited on the export form are recorded on the export only. The submission's inception date and CRM IDs are unchanged, and the form pre-fills from them the next time. | Approved | [research.md#clarifications](research.md#clarifications), 2026-09-10 |
| P-16 | An export belongs to the submission it was requested from, recorded on the export. Only that submission's exports section lists it. Another submission that reaches the same analysis shows it as exported on its export form, with a link to the export. | Approved | [research.md#clarifications](research.md#clarifications), 2026-09-10 |

---

## User Stories

### 1. Export finished analyses to the loss repository (P1)

From a submission's analyses page the analyst opens Export, ticks the finished analyses to send, picks the perspective, picks the client, confirms the treaty inception and CRM ID the form filled in from the submission, optionally types a data name per analysis, and clicks Export. The Workbench does the rest: it asks Risk Modeler for each analysis's loss table, downloads it, classifies and corrects the rows, and loads them. The analyst does not wait on the page and does not review rows.

**Acceptance**

1. **Given** a submission with two finished analyses that both have results for GR, **When** the analyst selects both, chooses GR and a client, and clicks Export, **Then** the export is accepted, the export detail page opens showing both analyses as pending, and no further input is asked of the analyst.
2. **Given** the form opened from a submission, **Then** treaty inception and CRM ID are pre-filled from the submission and editable, client is required, and data name and data vintage are optional and blank. **When** the analyst changes treaty inception or CRM ID and exports, **Then** the export records the changed values and the submission's inception date and CRM IDs are unchanged (P-15).
3. **Given** the analyst has selected analyses, **Then** the perspective choices are only the configured codes that every selected analysis has results for.
4. **Given** one selected analysis was already exported for GR, from this or any other submission, **When** the analyst picks GR, **Then** that analysis is shown as exported with the earlier export's date, requester, and status, linked to that export's detail page, and cannot be included.
5. **Given** two analysts submit the same analysis and perspective at the same moment, **Then** exactly one export is created and the other analyst is told which analysis was blocked and by which export.
6. **Given** an accepted export, **When** processing finishes, **Then** the repository holds exactly one header row per analysis, its stochastic events in the stochastic table, its historical events in the historical table, and every loss value equals the value Risk Modeler produced.
7. **Given** an analysis whose model version has no rows in the historical event lookup, **When** its load runs, **Then** that analysis fails with a message naming the model version and nothing for it reaches the repository tables; the other analyses in the export load normally.
8. **Given** an analysis whose downloaded loss table holds no loss rows for the chosen perspective, **When** its staging runs, **Then** that analysis fails with a message naming the perspective, nothing for it reaches the repository tables, and Retry is offered; the other analyses in the export load normally.
9. **Given** an accepted export whose analyses are queued or in progress, **Then** no cancel action is offered on the export or any analysis; each analysis processes to loaded or failed (P-14).

### 2. Follow an export and read the post-load summary (P1)

On the submission page, below the analyses, an exports section lists every export made from that submission, one row per export. The analyst opens one and each analysis in it shows how far it has got. Once loaded it shows the data ID CIC will use, the rows loaded, how many were stochastic and how many historical, and how many rows each correction changed. A failed analysis shows why.

**Acceptance**

1. **Given** an export in progress, **When** the analyst opens its detail page, **Then** each analysis shows one of: queued, in progress, loaded, or failed, with the time of the last change.
2. **Given** a loaded analysis, **Then** the page shows its data ID, rows staged, stochastic rows, historical rows, rows with exposure raised to loss, and rows with a standard deviation zeroed, and the stochastic plus historical counts equal the rows staged.
3. **Given** a failed analysis, **Then** the page shows the error message and a Retry action; the other analyses show their own status.
4. **Given** a loaded analysis, **Then** the page shows the path of the archive file kept on the shared drive.
5. **Given** the submission page, **Then** its exports section lists the exports requested from that submission, and no export requested from another submission, newest first with perspective, requester, request time, client, analysis count, and how many analyses are loaded or failed, and each row opens the export detail page.
6. **Given** an analysis exported for GR and again for RL, **Then** the exports section shows two rows, one per perspective, and the analyses grid row for that analysis is unchanged.

### 3. Retry a failed analysis (P2)

One analysis in an export failed: Risk Modeler rejected the request, the shared drive was not mounted, or the load hit a data problem the DBA has since fixed. The analyst clicks Retry on that analysis. The Workbench resumes from where it stopped: it does not download an archive it already has and never loads a data set twice.

**Acceptance**

1. **Given** an analysis that failed during load, **When** the analyst clicks Retry, **Then** the load runs again against the already staged rows without a new download, and on success the analysis shows loaded with its counts.
2. **Given** an analysis whose Risk Modeler export was rejected or whose download link has expired, **When** the analyst clicks Retry, **Then** a new Risk Modeler export is requested for that analysis only.
3. **Given** an analysis that already shows loaded, **Then** no Retry action is offered, and a re-run of its processing for any reason writes nothing new to the repository.
4. **Given** an analysis whose downloaded archive does not match the analysis (different analysis ID or currency), **When** the analyst retries, **Then** it fails again with the same specific message and nothing reaches the repository tables.
5. **Given** an analysis whose Risk Modeler export request was accepted but has not reached a terminal status, **Then** it shows in progress with the time of the last change, no Retry action is offered, and the Workbench keeps checking until Risk Modeler reports finished or failed.

## Requirements

- **FR-001**: The export form is reached from a submission's analyses page and offers every finished analysis related to that submission, own, broker, and group alike (P-10).
- **FR-002**: The form takes one or more analyses, exactly one perspective, a required client chosen from the repository's active clients (`ActiveFlag = 'Y'`), treaty inception and CRM ID pre-filled from the submission and editable, an optional data name per analysis (O-07), and an optional data vintage. An edited treaty inception or CRM ID is recorded on the export only and never written back to the submission (P-15).
- **FR-003**: The perspective choices are the configured exportable codes that every selected analysis has results for (P-02). The first configured set is GU, GR, RL, RP.
- **FR-004**: An analysis that has an export for the chosen perspective, in any status, is shown as exported with that export's date, requester, and status, and cannot be exported again: the Export button is off while such an analysis is selected, and a submission that names one is rejected whole, naming the analysis (P-09). Concurrent submissions of the same analysis and perspective produce exactly one export.
- **FR-005**: An analysis whose Risk Modeler application analysis ID is not a whole number cannot be exported; the form says why.
- **FR-006**: Submitting an export records, per analysis: requester email, request time, the submission it was requested from (P-16), analysis identifiers and name, perspective, client, treaty inception, treaty year, CRM ID, data name, data vintage, currency, and model vendor. The recorded values are what gets loaded; nothing is recomputed later.
- **FR-007**: For each analysis the Workbench requests a portfolio-level event loss table export for the chosen perspective from Risk Modeler, downloads the result when Risk Modeler finishes, and keeps the archive permanently on a configured shared drive under the export and analysis identifiers.
- **FR-008**: The downloaded archive is checked against the analysis: analysis ID and currency must match, the archive must carry its metadata file, and the loss table type must be known. A mismatch fails that analysis with the specific reason and loads nothing.
- **FR-009**: Loss table type, engine type, and model version are read from the archive and recorded on the export for that analysis. Model version is stored in the decimal form the repository expects (`25.0`), never the engine label.
- **FR-010**: Every loss row is staged in the repository before load, with the staged row count recorded per file and per analysis. An analysis whose archive holds zero loss rows for the chosen perspective fails at stage with a message naming the perspective and loads nothing; a header row with no loss rows is never written (P-12).
- **FR-011**: Each staged event is classified historical when the historical event lookup has a row for its event ID and the analysis's model version; otherwise stochastic. Peril is not part of the match: Risk Modeler never reuses an event ID across perils within a model version (research R4).
- **FR-012**: A load fails, writing nothing, when the lookup holds no rows for the analysis's model version, or when any event matches more than one lookup row; the error names the model version or event ID.
- **FR-013**: Where loss exceeds exposure value, exposure value is set to the loss and the row is counted (P-03). Where independent or correlated standard deviation is negative on a stochastic row, it is set to 0 and the row is counted (P-04). Loss values are never changed. Historical rows get no standard deviation correction.
- **FR-014**: The load writes one header row per analysis, all stochastic rows to the stochastic table and all historical rows to the historical table under that header's data ID, as one unit: all committed or none. The data ID is generated by the repository at load and recorded on the export.
- **FR-015**: Header row values follow the design overview §4.4: client, treaty inception, data vintage, data name, model vendor `RMS`, model version, currency, server, analysis ID, name, description, perspective, CRM ID. Historical rows also carry client, peril, model version, treaty year, treaty inception, data in-force date, event type, event name, and PCS number from the lookup.
- **FR-016**: Each analysis in an export is processed and committed independently; a failure in one leaves the others unaffected (non-negotiable 4).
- **FR-017**: The submission page has an exports section listing the exports requested from that submission (P-16) newest first with perspective, requester, request time, client, analysis count, and loaded and failed counts; each row opens the export detail page, which shows the export's ID, perspective, client, treaty inception, CRM ID, data vintage, requester, and date, and per analysis: status, last change time, archive path, data ID, and the counts in FR-018 (O-06).
- **FR-018**: After load the detail page shows, per analysis: rows staged, stochastic rows, historical rows, rows with exposure raised, rows with standard deviation zeroed. A failed analysis shows its error message.
- **FR-019**: Retry is offered per failed analysis and resumes from the last completed step: an existing archive is reused, a staged analysis is loaded without re-staging, and an analysis with no usable Risk Modeler export gets a new one. Retry is the only way to re-run an analysis (FR-004, FR-021). An analysis waiting on Risk Modeler is not failed and offers no Retry (P-13).
- **FR-020**: A processing step interrupted by a crash or restart is re-run automatically from its last completed step, with the same guarantees as Retry; an interrupted load never leaves a partial data set.
- **FR-021**: A load that has already completed is never repeated: any re-run of a loaded analysis writes nothing and reports success.
- **FR-022**: The client team can run the load step for a staged analysis themselves, from the repository, without the Workbench; a load run that way is visible to the Workbench as loaded.
- **FR-023**: Each export's analyses, Risk Modeler jobs, and Workbench jobs are traceable in the database from one export identifier (the detail page shows the analyses, not the job ids), and the request records carry requester email and time so a repository DBA can trace a data set without a Workbench login.
- **FR-024**: The Workbench never deletes a downloaded archive, a row of a staged or loaded analysis, or a loaded repository row; the file and loss rows of an interrupted stage are replaced when that stage re-runs. Retention of staged rows is decided separately (plan O-03).

## Key Entities

- **Export**: one analyst request, identified by an export ID, requested from one submission (P-16), covering one or more analyses at one perspective for one client. Has no status of its own; its analyses do.
- **Export manifest row**: one analysis within an export, one per analysis per perspective ever. Holds the values entered and recorded at request time, the archive path, stage and load status, error message, data ID, and the five row counts. It is the plan the processing executes and the record CIC and the Workbench both read.
- **Loss result file**: one Parquet file from the archive, with its perspective, chunk index, and row count.
- **Staged loss row**: one event from a loss result file with its loss, exposure value, standard deviations, classification, and correction flags.
- **Data header row** (`Data`): CIC's record of one loaded data set, identified by data ID.
- **Stochastic loss rows** (`RMSELT`) and **historical loss rows** (`RMS_HistoricalRDS`): CIC's loss tables, both keyed to a data ID.
- **Historical event lookup** (`Lookup_RMS_HistoricalRDS`): CIC's list of historical events per model version, matched on event ID and model version; read-only for the Workbench.
- **Client**: CIC's client list in the repository; read-only for the Workbench.

## Success Criteria

- **SC-001**: An analyst exports a finished analysis to the loss repository entirely from the Workbench, with under two minutes of their own time on the form and no manual steps afterwards.
- **SC-002**: 100% of loaded data sets have exactly one header row and stochastic plus historical row counts equal to the rows Risk Modeler exported.
- **SC-003**: Every automatic correction is visible: the count of rows changed by each correction is shown for every loaded analysis, and no loss value differs from the exported value.
- **SC-004**: 0 repeat data sets: no analysis and perspective is ever loaded twice, under concurrent submissions, retries, or crashes.
- **SC-005**: A failed analysis can be diagnosed from its detail page message alone and, after the cause is fixed, recovered with one Retry click and no re-download of an archive already held.
