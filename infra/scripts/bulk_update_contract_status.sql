-- Bulk update of Contract status from CIC's CRM (spec 017 FR-023, note 34 D10-D13).
--
-- CIC runs this in SQL against rwb_workbench after CRM closes out a renewal
-- date. The source is dbo.CRMContractStatus in CIC's loss repository: one row
-- per CRM ID with the status CRM holds for it, in CRM's words Open / Won / Lost
-- (the Workbench uses the same words since note 34 D9). Every Workbench
-- contract whose CRM ID appears in the source is set to the source's status.
-- The Workbench reads `contract` directly, so the lists and "in force as of"
-- see the change at once. Contract status has no history (spec 017 P-02): the
-- row's updated_at moves and updated_by is cleared, so an analyst's edit from a
-- stale page is refused.
--
-- 1. The SOURCE line below names rwb_loss, the development database. In
--    production replace it with the name of CIC's loss repository; nothing
--    else differs between environments. The login running the script needs
--    SELECT on that table.
-- 2. Leave @dry_run = 1 for the first run: it reports what would change and
--    writes nothing. Set it to 0 to apply.
-- 3. Run the whole script as one batch. It writes nothing when the source
--    carries a status the Workbench does not have or lists a CRM ID twice
--    (two spellings of the same CRM ID). A CRM ID with no contract in the
--    Workbench is counted and skipped: CRM holds deals the Workbench never
--    modeled. A Workbench contract with no source row is left alone.

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @dry_run BIT = 1;

DROP TABLE IF EXISTS #crm_status;
CREATE TABLE #crm_status (crm_id NVARCHAR(255) NOT NULL, status NVARCHAR(50) NOT NULL);

-- ==== SOURCE: the CRM status relation; change the database name per environment ====
INSERT INTO #crm_status (crm_id, status)
SELECT CRMID, Status FROM rwb_loss.dbo.CRMContractStatus;
-- ==== END SOURCE ====

DROP TABLE IF EXISTS #resolved;
CREATE TABLE #resolved (
    crm_id          NVARCHAR(255) NOT NULL,
    status          NVARCHAR(50)  NOT NULL,
    new_status      NVARCHAR(50)  NULL,   -- NULL: the Workbench has no such status
    contract_id     UNIQUEIDENTIFIER NULL, -- NULL: no contract carries this CRM ID
    old_status      NVARCHAR(50)  NULL,
    submission_name NVARCHAR(255) NULL
);

-- The CRM ID matches trimmed and case-insensitively, as the Workbench's own
-- CRM ID filter and uniqueness check do.
INSERT INTO #resolved (crm_id, status, new_status, contract_id, old_status, submission_name)
SELECT x.crm_id, x.status, k.code, c.id, c.contract_status_code, s.name
FROM #crm_status x
LEFT JOIN contract_status_kind k ON k.code = UPPER(TRIM(x.status))
LEFT JOIN contract c ON LOWER(TRIM(c.crm_id)) = LOWER(TRIM(x.crm_id))
LEFT JOIN submission s ON s.id = c.submission_id;

DECLARE @unknown_status INT = (SELECT COUNT(*) FROM #resolved WHERE new_status IS NULL);
DECLARE @duplicates INT = (
    SELECT COUNT(*) FROM (
        SELECT LOWER(TRIM(crm_id)) AS crm_key FROM #crm_status
        GROUP BY LOWER(TRIM(crm_id)) HAVING COUNT(*) > 1) d);

SELECT (SELECT COUNT(*) FROM #crm_status)                                         AS source_rows,
       (SELECT COUNT(*) FROM #resolved WHERE contract_id IS NOT NULL
                                         AND new_status IS NOT NULL
                                         AND old_status <> new_status)             AS to_update,
       (SELECT COUNT(*) FROM #resolved WHERE contract_id IS NOT NULL
                                         AND old_status = new_status)              AS already_at_status,
       (SELECT COUNT(*) FROM #resolved WHERE contract_id IS NULL)                  AS not_in_workbench,
       @unknown_status                                                             AS unknown_status,
       @duplicates                                                                 AS duplicate_crm_ids,
       @dry_run                                                                    AS dry_run;

-- Rows that stop the run.
SELECT crm_id, status, 'status not in contract_status_kind' AS problem
FROM #resolved WHERE new_status IS NULL
UNION ALL
SELECT MIN(crm_id), MIN(status), 'CRM ID listed more than once'
FROM #crm_status GROUP BY LOWER(TRIM(crm_id)) HAVING COUNT(*) > 1
ORDER BY crm_id;

-- The change list: keep this result as the record of the run.
SELECT crm_id, submission_name, old_status, new_status
FROM #resolved
WHERE contract_id IS NOT NULL AND new_status IS NOT NULL AND old_status <> new_status
ORDER BY submission_name, crm_id;

IF @unknown_status > 0 OR @duplicates > 0
BEGIN
    RAISERROR ('Nothing written: fix the rows in the problem list and run again.', 16, 1);
    RETURN;
END;

IF @dry_run = 1
BEGIN
    PRINT 'Dry run: nothing written. Set @dry_run = 0 to apply.';
    RETURN;
END;

BEGIN TRANSACTION;
UPDATE c
SET c.contract_status_code = r.new_status,
    c.updated_at = GETUTCDATE(),
    c.updated_by = NULL
FROM contract c
JOIN #resolved r ON r.contract_id = c.id
WHERE c.contract_status_code <> r.new_status;
PRINT CONCAT(@@ROWCOUNT, ' contract(s) updated.');
COMMIT TRANSACTION;
