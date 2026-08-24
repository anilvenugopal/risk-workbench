-- Per-portfolio peril coverage for every portfolio in a Moody's RMS EDM — the
-- two account counts the disclosure lines need (spec 005 FR-007 / P-21): how
-- many accounts carry at least one peril, and how many carry MORE THAN ONE.
-- Neither is derivable from portfolio_perils.sql's per-value counts: summing
-- those counts memberships, and an account with three perils adds three.
--
-- The joins mirror portfolio_perils.sql exactly, so these counts describe the
-- same account population as the per-value counts. Read-only SELECT; the
-- target EDM database is selected at the connection level (no USE here).

WITH pairs AS (
    SELECT DISTINCT
        pa.PORTINFOID AS PortfolioId,
        pa.ACCGRPID AS AccountId,
        lc.PERIL AS Value
    FROM dbo.portacct AS pa
    INNER JOIN dbo.Property AS p
        ON p.ACCGRPID = pa.ACCGRPID
    INNER JOIN dbo.loccvg AS lc
        ON lc.LOCID = p.LOCID
    WHERE lc.PERIL IS NOT NULL
),
per_account AS (
    SELECT PortfolioId, AccountId, COUNT(*) AS ValueCount
    FROM pairs
    GROUP BY PortfolioId, AccountId
)
SELECT
    pa.PortfolioId,
    pi.PORTNAME AS PortfolioName,
    COUNT(*) AS CoveredAccounts,
    SUM(CASE WHEN pa.ValueCount > 1 THEN 1 ELSE 0 END) AS MultiValueAccounts
FROM per_account AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PortfolioId
GROUP BY pa.PortfolioId, pi.PORTNAME
ORDER BY pa.PortfolioId;
