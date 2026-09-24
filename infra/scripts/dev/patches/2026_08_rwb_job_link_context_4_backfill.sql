-- CR-04c step 4 of 6: backfill link_type/link_id/context_type/context_id on
-- every existing rwb_job row.
-- Run after step 3 has succeeded. Safe to re-run: every UPDATE is guarded
-- with `link_type IS NULL`, so a row already resolved is skipped.
--
-- link_type/link_id and context_type/context_id are derived together, from
-- what each job's own worker body acts on (docs/CR/CR_04c__RWB_JOB_LINK_AND_
-- CONTEXT.md §6) — never copied from requestor_type/requestor_id directly.

-- irp_job-triggered EDM sites: backfill_edm_detail via import-terminal or
-- geohaz-terminal chaining (poller). Context/link both resolve to the EDM
-- irp_job carries, never to irp_job.id itself.
UPDATE rj SET link_type = 'edm', link_id = ij.irp_edm_id,
    context_type = 'edm', context_id = ij.irp_edm_id
FROM rwb_job rj JOIN irp_job ij ON rj.requestor_type = 'irp_job'
    AND rj.requestor_id = ij.id
WHERE rj.rwb_job_type = 'backfill_edm_detail' AND rj.link_type IS NULL
    AND ij.irp_edm_id IS NOT NULL;

-- irp_job-triggered RDM site: backfill_rdm_analyses via import-terminal chaining.
UPDATE rj SET link_type = 'rdm', link_id = ij.irp_rdm_id,
    context_type = 'rdm', context_id = ij.irp_rdm_id
FROM rwb_job rj JOIN irp_job ij ON rj.requestor_type = 'irp_job'
    AND rj.requestor_id = ij.id
WHERE rj.rwb_job_type = 'backfill_rdm_analyses' AND rj.link_type IS NULL
    AND ij.irp_rdm_id IS NOT NULL;

-- irp_job-triggered finalize_analysis: context is the irp_analysis row named
-- in the job's own input_data, NOT irp_job.id. link is the EDM irp_job carries.
UPDATE rj SET link_type = 'edm', link_id = ij.irp_edm_id,
    context_type = 'irp_analysis',
    context_id = TRY_CAST(JSON_VALUE(rj.input_data, '$.analysis_id') AS UNIQUEIDENTIFIER)
FROM rwb_job rj JOIN irp_job ij ON rj.requestor_type = 'irp_job'
    AND rj.requestor_id = ij.id
WHERE rj.rwb_job_type = 'finalize_analysis' AND rj.link_type IS NULL;

-- irp_analysis-requested rows (retrieve_analysis_results, both chaining
-- paths): context IS the irp_analysis row (== requestor_id at this site).
-- link prefers edm_id, else rdm_id.
UPDATE rj SET
    link_type = CASE WHEN ia.edm_id IS NOT NULL THEN 'edm' ELSE 'rdm' END,
    link_id = COALESCE(ia.edm_id, ia.rdm_id),
    context_type = 'irp_analysis', context_id = ia.id
FROM rwb_job rj JOIN irp_analysis ia ON rj.requestor_type = 'irp_analysis'
    AND rj.requestor_id = ia.id
WHERE rj.rwb_job_type = 'retrieve_analysis_results' AND rj.link_type IS NULL;

-- breakout_group-requested rows (run_breakout_custom): link resolves the EDM
-- via source_portfolio_id -> edm_id; context is the breakout_group row itself
-- (== requestor_id at this site).
UPDATE rj SET link_type = 'edm', link_id = p.edm_id,
    context_type = 'breakout_group', context_id = bg.id
FROM rwb_job rj
    JOIN breakout_group bg ON rj.requestor_type = 'breakout_group'
        AND rj.requestor_id = bg.id
    JOIN irp_portfolio p ON p.id = bg.source_portfolio_id
WHERE rj.rwb_job_type = 'run_breakout_custom' AND rj.link_type IS NULL;

-- portfolio-requested rows (run_geohaz, run_breakout_lob/state/country/peril):
-- link resolves the EDM via irp_portfolio.edm_id; context is the source
-- portfolio itself (== requestor_id at this site).
UPDATE rj SET link_type = 'edm', link_id = p.edm_id,
    context_type = 'portfolio', context_id = p.id
FROM rwb_job rj JOIN irp_portfolio p ON p.id = rj.requestor_id
WHERE rj.requestor_type = 'analyst_request' AND rj.link_type IS NULL
    AND rj.rwb_job_type IN ('run_geohaz', 'run_breakout_lob', 'run_breakout_state',
                             'run_breakout_country', 'run_breakout_peril');

-- analyst_request rows keyed directly on the entity (upload_edm/upload_rdm/
-- backfill_edm_detail/backfill_rdm_analyses via manual sync): requestor_id
-- already IS the edm_id/rdm_id, so context and link both equal it directly.
UPDATE rj SET link_type = 'edm', link_id = rj.requestor_id,
    context_type = 'edm', context_id = rj.requestor_id
FROM rwb_job rj
WHERE rj.requestor_type = 'analyst_request' AND rj.link_type IS NULL
    AND rj.rwb_job_type IN ('upload_edm', 'backfill_edm_detail')
    AND EXISTS (SELECT 1 FROM irp_edm e WHERE e.id = rj.requestor_id);

UPDATE rj SET link_type = 'rdm', link_id = rj.requestor_id,
    context_type = 'rdm', context_id = rj.requestor_id
FROM rwb_job rj
WHERE rj.requestor_type = 'analyst_request' AND rj.link_type IS NULL
    AND rj.rwb_job_type IN ('upload_rdm', 'backfill_rdm_analyses')
    AND EXISTS (SELECT 1 FROM irp_rdm r WHERE r.id = rj.requestor_id);

-- rwb_job-requested rows (breakout-completion chaining): both link and
-- context resolve to the EDM named in the PARENT job's own input_data —
-- never to the parent job's own id (rj.requestor_id / parent.id).
UPDATE rj SET
    link_type = 'edm',
    link_id = TRY_CAST(JSON_VALUE(parent.input_data, '$.edm_id') AS UNIQUEIDENTIFIER),
    context_type = 'edm',
    context_id = TRY_CAST(JSON_VALUE(parent.input_data, '$.edm_id') AS UNIQUEIDENTIFIER)
FROM rwb_job rj JOIN rwb_job parent ON rj.requestor_type = 'rwb_job'
    AND rj.requestor_id = parent.id
WHERE rj.rwb_job_type = 'backfill_edm_detail' AND rj.link_type IS NULL
    AND TRY_CAST(JSON_VALUE(parent.input_data, '$.edm_id') AS UNIQUEIDENTIFIER) IS NOT NULL;

-- execute_analysis_batch: link is the batch's edm_id; context is the batch's
-- own execution_id (a real grouping value across many irp_analysis rows, not
-- the same value as link_id here).
UPDATE rj SET
    link_type = 'edm',
    link_id = TRY_CAST(JSON_VALUE(rj.input_data, '$.edm_id') AS UNIQUEIDENTIFIER),
    context_type = 'execution',
    context_id = TRY_CAST(JSON_VALUE(rj.input_data, '$.execution_id') AS UNIQUEIDENTIFIER)
FROM rwb_job rj
WHERE rj.rwb_job_type = 'execute_analysis_batch' AND rj.link_type IS NULL;

-- no-context rows: sync_irp_metadata acts on no single row (link is
-- not_applicable, context stays NULL); dummy_wait/dummy_fail the same.
UPDATE rwb_job SET link_type = 'not_applicable', link_id = NULL,
    context_type = NULL, context_id = NULL
WHERE rwb_job_type IN ('sync_irp_metadata', 'dummy_wait', 'dummy_fail')
    AND link_type IS NULL;

-- Verify before running step 5: every row must have a non-null link_type.
-- Anything still NULL here is a real gap (a rwb_job_type not covered above) —
-- stop and investigate; do not add a catch-all to silence this.
--   SELECT rwb_job_type, requestor_type, COUNT(*) FROM rwb_job
--   WHERE link_type IS NULL GROUP BY rwb_job_type, requestor_type;
