-- Account ids per peril for ONE portfolio in a Moody's RMS EDM — the peril
-- selection read for custom grouping (spec 005 follow-on P-19). Value is
-- loccvg.PERIL, the numeric RMS peril code (W-21): the joins and the NULL
-- filter mirror portfolio_perils.sql exactly, so the values this query
-- filters on match the stored summary the group was approved from. ACCGRPID
-- is the id Risk Modeler's account operations accept as accountId; DISTINCT
-- pairs give the one-matching-coverage-admits-the-whole-account bucketing
-- (W-3/W-11) by construction. Read-only SELECT; the target EDM database is
-- selected at the connection level (no USE here). {{ portfolio_id }} is
-- substituted by the irp-integration DataBridge executor with injection-safe
-- escaping.

SELECT DISTINCT
    lc.PERIL AS Value,
    pa.ACCGRPID AS AccountId
FROM dbo.portacct AS pa
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.loccvg AS lc
    ON lc.LOCID = p.LOCID
WHERE pa.PORTINFOID = {{ portfolio_id }}
    AND lc.PERIL IS NOT NULL
ORDER BY Value, AccountId;
