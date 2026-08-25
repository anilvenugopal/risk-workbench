# Feature Specification: Per-queue Dramatiq workers and job monitoring UI

**Branch**: `cr04-per-queue-workers-job-ui` | **Created**: 2026-08-25

## Status

**Phase:** Ready for tasks
**Blocking:** Nothing.

## Outcome

An operator can restart the workers for one job type without affecting the other three, and stop a job before it starts. An analyst can see every queued, running, and failed job in one page, and retry a failed one without finding its original entity-specific screen.

## In scope

- One Dramatiq queue and worker process per `rwb_job_type` (`upload_edm`, `upload_rdm`, `backfill_rdm_analyses`, `backfill_edm_detail`).
- A single place that lists the queue names, read by both Python and the start/stop scripts.
- A drain check that confirms no queue has outstanding work, gating a deploy.
- A read-only page listing `rwb_job` rows by type and status, including queued jobs.
- Cancel a queued job. Resubmit a failed job.

## Out of scope

- Stopping a job that is already running. The only remedy is killing its queue's worker process, done by an operator outside the app.
- A form to submit an arbitrary new job of any type.
- Triggering a queue drain from the UI.
- Changing worker process/thread counts from the UI.
- Reordering queued jobs by priority.
- Keeping a history of earlier failed attempts after a resubmit — the resubmitted job reuses the same record.
- Deploying new code to one queue while the others keep running old code.

## Non-negotiable behavior

1. A long-running job of one type never delays a job of a different type waiting behind it.
2. A job already claimed by a worker keeps running unaffected by another job type's failure or restart.
3. Cancel only ever affects a job that has not yet started.
4. Resubmitting a failed job does not create a duplicate active job for the same request.
5. No job's queue placement or restart depends on a value hand-copied into more than one file.

## Open product decisions

| ID | Decision | Status | Where |
|---|---|---|---|
| O-01 | Convert the worker's start/stop scripts to per-queue now using the existing plain-process style, rather than introducing systemd units for the worker ahead of the app's other processes | Approved | `docs/CR/CR_04_DEV_PLAN.md` Decision A, option 1 |
| O-02 | Resubmitting a failed job overwrites that job's own failure detail with no record kept | Assumed | `docs/CR/CR_04a__JOB_MONITORING_UI.md` §5.2, §7.1 |
| O-03 | A running job that will not finish has no in-app way to stop; an operator must act outside the app | Assumed | `docs/CR/CR_04__PER_QUEUE_WORKERS.md` §3.10 |

---

## User Stories

### 1. Isolate worker capacity by job type (P1)

An operator running the app today sees one worker process handling every job type. When a data-heavy job (for example, pulling every analysis for a large RDM) runs long, other job types queue up behind it even though they have nothing to do with each other. After this story, each job type has its own worker, so one type's backlog or a stuck job never blocks another type's work, and an operator who needs to recover from a stuck job can restart just that one job type.

**Acceptance**

1. **Given** a long-running `backfill_rdm_analyses` job is in progress, **When** an `upload_edm` job is submitted, **Then** the `upload_edm` job starts without waiting for the `backfill_rdm_analyses` job to finish.
2. **Given** a job type's worker is stopped, **When** the other three job types' workers are still running, **Then** their jobs continue to be picked up and completed normally.
3. **Given** a new job type is added to the codebase, **When** the app is started, **Then** that job type gets its own queue and worker automatically, with no separate list to update by hand.
4. **Given** a deploy is about to happen, **When** an operator checks whether it is safe to proceed, **Then** they get a clear answer for every job type — nothing still queued or in progress — before the deploy continues.

### 2. Monitor and manage jobs from one page (P2)

An analyst or operator today has no single place to see what background work is queued, running, or has failed — they have to know which entity's detail page might show a status for one specific job. After this story, anyone can open one page, see every job across all types grouped by status, cancel one that hasn't started yet, and resubmit one that failed, without leaving that page or knowing which original screen created the job.

**Acceptance**

1. **Given** jobs of multiple types exist in different states, **When** the monitoring page is opened, **Then** every job is listed with its type, current status, and how long it has been queued or running.
2. **Given** a job is queued but not yet started, **When** it is cancelled from the page, **Then** it never runs, and this is reflected immediately on the page.
3. **Given** a worker claims a queued job at the same moment an operator cancels it, **When** both actions race, **Then** exactly one of them takes effect and neither action errors or duplicates the job.
4. **Given** a job has failed, **When** it is resubmitted from the page, **Then** it moves back to queued and is picked up again, without creating a second, separate job record.
5. **Given** a job is currently running, **When** the page is viewed, **Then** no cancel or stop action is offered for it, and the page states plainly that stopping it requires an operator to act outside the app.

## Requirements

- **FR-001**: Each job type MUST run in its own queue, separate from every other job type.
- **FR-002**: Each job type's queue MUST run in its own worker process, so work of one type cannot occupy capacity another type needs.
- **FR-003**: The list of job types and their queues MUST be derived from the code that defines them, not maintained as a separate hand-written list.
- **FR-004**: Starting or stopping the workers, in any environment, MUST use that same derived list — no script may hardcode a job type's name.
- **FR-005**: The system MUST provide a way to check, for every job type at once, whether any job is still queued or in progress.
- **FR-006**: A deploy process MUST be able to use that check to confirm it is safe to proceed before changing running code.
- **FR-007**: Recovering a job whose worker process was stopped or killed MUST work the same regardless of how many job types exist or which one was affected.
- **FR-008**: The monitoring page MUST list every job across all job types, showing at minimum its type, current status, and elapsed queued/running time.
- **FR-009**: A job that is queued but not yet started MUST be visibly distinguishable from one that is running.
- **FR-010**: A user MUST be able to cancel a job that is queued but not yet started.
- **FR-011**: Cancelling a job that has already started running MUST NOT be possible from the page.
- **FR-012**: A cancel action racing a worker claiming the same job MUST resolve to exactly one outcome — either the job is cancelled, or it runs — never both, never an error.
- **FR-013**: A user MUST be able to resubmit a job that has failed.
- **FR-014**: Resubmitting a failed job MUST NOT create an additional, separate job record for the same request while one already exists.
- **FR-015**: The page MUST NOT offer any action that stops a job already running.
- **FR-016**: The page MUST NOT offer a way to construct and submit a brand-new job of an arbitrary type.
- **FR-017**: The page MUST NOT offer a way to drain a queue or change worker capacity.
- **FR-018**: The page MUST NOT offer a way to reorder queued jobs by priority.

## Key Entities

- **Job (`rwb_job`)**: One unit of background work of a specific job type, moving through queued, running, and a terminal state (succeeded, failed, or cancelled). Resubmitting a failed job reuses the same job record rather than creating a new one.
- **Job type**: A category of background work (e.g. uploading an EDM, backfilling RDM analyses). Each job type has its own queue and worker capacity, independent of every other job type.

## Success Criteria

- **SC-001**: A job of one type starts and completes without waiting on an unrelated, longer-running job of a different type, regardless of how long the other job runs.
- **SC-002**: Stopping the worker for one job type has no effect on the other job types' ability to pick up and complete their own jobs.
- **SC-003**: An operator can determine, in one check, whether it is safe to deploy — with no job of any type still queued or running — before changing code.
- **SC-004**: A user can see the current status of every background job, across all job types, without visiting more than one page.
- **SC-005**: A user can stop a job before it starts, or retry a job that failed, without needing to know which part of the app originally created it.
