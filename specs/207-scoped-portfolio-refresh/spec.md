# Feature Specification: Scoped Portfolio Refresh

**Branch**: `207-scoped-portfolio-refresh` | **Created**: 2026-10-07 | **Issue**: #207 (supersedes #180)

## Status

**Phase:** Approved · **Blocking:** Nothing

## Outcome

After a breakout or a hazard lookup, the Workbench refreshes only the portfolios that action changed, instead of re-syncing the whole EDM. An analyst can start a breakout on another portfolio of the same EDM while that refresh runs, and the refresh reads figures and the exposure summary only for the portfolios changed. The Sync button on an EDM page inside a submission syncs that EDM only, as its place beside the EDM's "synced" time says.

## In scope

- The breakout follow-up (spec 005 FR-013) refreshes the run's generated portfolios only.
- The hazard lookup follow-up refreshes the looked-up portfolio only, and the breakout gate refuses only that portfolio while the refresh runs; a cancelled lookup refreshes nothing.
- The Sync on an EDM page inside a submission stops also syncing every RDM in that submission's context for the EDM.
- During a breakout, the Portfolios section poll keeps the ticked boxes and each table's sideways scroll. A failed custom group's line clears when the next cart finishes and names the group by its label (spec 005 FR-012).

## Out of scope

- EDM import and the analyst's Sync on an EDM stay full syncs of the EDM, and RDM import and the Sync on an RDM's own page stay full syncs of the RDM. Exports, grouping and analyses start no EDM or RDM refresh, so they are unchanged.

## Non-negotiable behavior

1. A scoped refresh changes only the portfolios it covers. It never removes a portfolio, never touches treaties, and never changes the EDM's "synced" time, which keeps meaning "last full sync".
2. The breakout confirm's freshness checks (spec 005 FR-002a, FR-002b) stay as they are; a scoped refresh writes the same freshness stamp a full sync writes.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| P-01 | A breakout follow-up covers every portfolio the run created, adopted, or found already existing | Approved | issue #207; user 2026-10-07 |
| P-02 | A hazard lookup refreshes its portfolio when it ends finished or failed, and nothing when it ends cancelled — including one cancelled after it started running; the analyst's Sync covers that case | Approved | issue #207 |
| P-03 | While a hazard lookup's refresh is pending or running, the breakout gate refuses that portfolio, with a reason saying the portfolio is refreshing; other portfolios in the EDM stay available | Approved | user 2026-10-07 |
| P-04 | A breakout follow-up does not hold the gate on its portfolios: a new one has no figures until the refresh lands, so the gate already refuses it, and FR-002b refuses a confirm whose summary was rewritten mid-preview | Approved | issue #207; user 2026-10-07 |
| P-05 | A scoped refresh neither shows the EDM as syncing nor holds back the analyst's Sync | Approved | issue #207; user 2026-10-07 |
| P-06 | A failed scoped refresh keeps each portfolio's prior figures and adds no new on-screen error; the Jobs monitor shows the failure and the analyst's Sync recovers | Approved | issue #207; user 2026-10-07 |
| P-07 | The Sync on an EDM page inside a submission syncs that EDM only. An RDM syncs when its import finishes and from the Sync on its own page | Approved | user 2026-10-07 |

---

## User Stories

### 1. A breakout refreshes only its generated portfolios (P1)

An analyst breaks out a portfolio in an EDM that holds hundreds of portfolios. When the run finishes, the generated portfolios get their exposure figures and summary, and nothing else in the EDM is re-read. The "figures are filling in" banner shows until those figures land. Meanwhile the analyst can start a breakout on any other portfolio in the same EDM.

**Acceptance**

1. **Given** an EDM with many portfolios, **When** a breakout run creating 4 portfolios finishes, **Then** the follow-up refresh reads figures for those 4 portfolios only, and its job record lists those 4 portfolios.
2. **Given** that follow-up refresh is pending or running, **When** the analyst opens the breakout on another portfolio of the same EDM, **Then** the breakout is available.
3. **Given** that follow-up refresh is pending or running, **When** the analyst views the EDM, **Then** the "figures are filling in" banner shows and the EDM does not show as syncing.
4. **Given** the follow-up refresh finished, **When** the analyst views the EDM, **Then** every other portfolio's figures, the treaties, and the EDM's "synced" time are as they were before the breakout.
5. **Given** a breakout run in which every entry failed, **When** the run finishes, **Then** no refresh starts.

### 2. A hazard lookup refreshes only its portfolio (P1)

An analyst runs a hazard lookup on one portfolio. When the lookup finishes or fails, that portfolio's figures, summary and freshness stamp are refreshed, so a breakout on it afterwards is not refused as stale. A lookup cancelled in Risk Modeler starts no refresh.

**Acceptance**

1. **Given** a hazard lookup that finished, **When** its refresh lands, **Then** the analyst can break out that portfolio without a Sync, and the confirm's freshness check passes.
2. **Given** a hazard lookup's refresh is pending or running, **When** the analyst opens the breakout on that portfolio, **Then** the breakout is refused with a reason saying the portfolio is refreshing.
3. **Given** a hazard lookup's refresh is pending or running, **When** the analyst opens the breakout on another portfolio of the same EDM, **Then** the breakout is available.
4. **Given** a hazard lookup that ended cancelled, **When** the Workbench records the cancellation, **Then** no refresh starts.
5. **Given** a hazard lookup that ended failed, **When** the Workbench records the failure, **Then** the portfolio's refresh starts.
6. **Given** the hazard lookup's refresh finished, **When** the analyst views the EDM, **Then** every other portfolio's figures and the EDM's "synced" time are unchanged.

### 3. The Sync on an EDM page syncs only that EDM (P2)

An analyst on an EDM page inside a submission clicks Sync beside the EDM's "synced" time. The EDM syncs. The RDMs listed on that page do not: an imported RDM's analyses do not change, so re-syncing them only adds jobs. An analyst who wants an RDM re-synced uses the Sync on that RDM's page.

**Acceptance**

1. **Given** an EDM page inside a submission that lists RDMs, **When** the analyst clicks Sync, **Then** the EDM's sync starts and no RDM sync starts.
2. **Given** one of those RDMs, **When** the analyst clicks Sync on the RDM's page, **Then** that RDM's sync starts, as before.

## Edge Cases

- **A covered portfolio no longer exists in Risk Modeler**: the refresh skips it and leaves its row in place; the next full sync removes it.
- **One covered portfolio's read fails**: the other covered portfolios still refresh; the failed one keeps its prior figures.
- **A full sync and a scoped refresh run at the same time**: both write current figures; neither waits for the other.
- **The analyst clicks Sync while a scoped refresh runs**: the Sync starts.

## Requirements

- **FR-001**: When a breakout run finishes with at least one successful entry, the system MUST refresh the exposure figures, exposure summary and freshness stamp of the portfolios that run produced, and of no other portfolio (P-01).
- **FR-002**: When a hazard lookup ends finished or failed, the system MUST refresh that portfolio's exposure figures, exposure summary and freshness stamp, and no other portfolio's (P-02).
- **FR-003**: When a hazard lookup ends cancelled, the system MUST NOT start a refresh (P-02).
- **FR-004**: A scoped refresh MUST read Risk Modeler figures once per covered portfolio and MUST compute the exposure summary for the covered portfolios only. The portfolio list read (`list_portfolios`, research R3) and the `hdsteppolicy` scan (research R4) still cover the whole EDM; the floor of about 20 s that [R4](research.md#r4) measured is accepted.
- **FR-005**: A scoped refresh MUST NOT remove portfolios, change treaties, or change the EDM's "synced" time.
- **FR-006**: A scoped refresh MUST skip a covered portfolio Risk Modeler no longer returns, and MUST continue past a covered portfolio whose read fails, keeping that portfolio's prior figures (P-06).
- **FR-007**: EDM import completion and the analyst's Sync MUST still refresh the whole EDM, as before.
- **FR-008**: A scoped refresh MUST NOT make the breakout gate refuse a portfolio other than the one P-03 names, MUST NOT show the EDM as syncing, and MUST NOT hold back the analyst's Sync (P-04, P-05).
- **FR-009**: While a hazard lookup's refresh is pending or running, the breakout gate MUST refuse that portfolio with a reason saying the portfolio is refreshing (P-03).
- **FR-010**: The "figures are filling in" banner MUST show while a breakout's scoped refresh is pending or running.
- **FR-011**: A scoped refresh's job record MUST list the portfolios it covered and the ones whose read failed.
- **FR-012**: The Sync on an EDM page inside a submission MUST sync that EDM only and MUST NOT start an RDM sync (P-07).
- **FR-013**: The Portfolios section poll MUST keep the analyst's ticked portfolio boxes, except a box the response renders disabled, and each table's horizontal scroll.
- **FR-014**: A failed custom group's error line MUST clear when the next custom cart on that source portfolio finishes, and MUST name the group by its label (spec 005 FR-012).

## Key Entities

- **Scoped refresh**: a refresh of a named set of portfolios in one EDM, started by a breakout run or a hazard lookup. Its job record names the portfolios it covered.
- **Full sync**: the refresh of a whole EDM — every portfolio, the treaties, removal of portfolios Risk Modeler no longer returns, and the EDM's "synced" time. Started by EDM import and the analyst's Sync.

## Success Criteria

- **SC-001**: After a breakout or hazard lookup, the number of portfolios re-read equals the number of portfolios the action changed, whatever the EDM's size.
- **SC-002**: While one portfolio's scoped refresh runs, an analyst can start a breakout on any other portfolio of the same EDM.
- **SC-003**: After a finished hazard lookup's refresh lands, a breakout on that portfolio is confirmed without the analyst clicking Sync.
- **SC-004**: A cancelled hazard lookup starts zero refreshes.
- **SC-005**: Portfolios outside a scoped refresh, the treaties, and the EDM's "synced" time are unchanged after it.
