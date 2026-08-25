-- Account ids per line of business for ONE portfolio in a Moody's RMS EDM —
-- the LOB breakout selection read (spec 005 R1, revised 2026-08-05 after the
-- REST selection failed on a 248,000-account portfolio, W-20). ACCGRPID is the
-- id Risk Modeler's account operations accept as accountId. The joins mirror
-- portfolio_lines_of_business.sql exactly, so the values this query filters on
-- are byte-identical to the stored summary the analyst approved, and the
-- one-matching-policy-admits-the-whole-account bucketing (W-3/W-11) holds by
-- construction. Read-only SELECT; the target EDM database is selected at the
-- connection level (no USE here). {{ portfolio_id }} is substituted by the
-- irp-integration DataBridge executor with injection-safe escaping.

SELECT DISTINCT
    l.LOBNAME AS Value,
    pa.ACCGRPID AS AccountId
FROM dbo.portacct AS pa
INNER JOIN dbo.policy AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.lobdet AS l
    ON l.LOBDETID = p.LOBDETID
WHERE pa.PORTINFOID = {{ portfolio_id }}
    AND NULLIF(LTRIM(RTRIM(l.LOBNAME)), '') IS NOT NULL
ORDER BY Value, AccountId;
