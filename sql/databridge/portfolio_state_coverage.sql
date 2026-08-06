-- Per-portfolio geography coverage for every portfolio in a Moody's RMS EDM —
-- the two account counts the breakout preview's overlap statement needs
-- (spec 005 FR-007 / P-13, revised 2026-08-05): how many accounts carry at
-- least one state, and how many carry MORE THAN ONE. Neither is derivable from
-- portfolio_states.sql's per-value counts: summing those counts memberships,
-- and an account with three states adds three.
--
-- The joins and the code filter mirror portfolio_states.sql exactly, so these
-- counts describe the same account population as the per-value counts the
-- analyst reads. Admin1Code is the grouping value (P-12); Admin1Name is never
-- a grouping key. Read-only SELECT; the target EDM database is selected at the
-- connection level (no USE here).
--
-- CoveredAccounts is the SC-002 coverage figure: portfolio_account_total.sql's
-- AccountTotal minus this is the number of accounts that carry no state at all
-- and therefore land in no sub-portfolio.

WITH pairs AS (
    SELECT DISTINCT
        pa.PORTINFOID AS PortfolioId,
        pa.ACCGRPID AS AccountId,
        a.Admin1Code AS Value
    FROM dbo.portacct AS pa
    INNER JOIN dbo.Property AS p
        ON p.ACCGRPID = pa.ACCGRPID
    INNER JOIN dbo.Address AS a
        ON a.AddressID = p.ADDRESSID
    WHERE NULLIF(LTRIM(RTRIM(a.Admin1Code)), '') IS NOT NULL
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
