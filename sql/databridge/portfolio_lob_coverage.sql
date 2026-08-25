-- Per-portfolio line-of-business coverage for every portfolio in a Moody's RMS
-- EDM — the two account counts the breakout preview's overlap statement needs
-- (spec 005 FR-007 / P-13, revised 2026-08-05): how many accounts carry at
-- least one LOB, and how many carry MORE THAN ONE. Neither is derivable from
-- portfolio_lines_of_business.sql's per-value counts: summing those counts
-- memberships, and an account with three LOBs adds three.
--
-- The joins and the blank filter mirror portfolio_lines_of_business.sql
-- exactly, so these counts describe the same account population as the
-- per-value counts the analyst reads. Read-only SELECT; the target EDM
-- database is selected at the connection level (no USE here).
--
-- CoveredAccounts is the SC-002 coverage figure: portfolio_account_total.sql's
-- AccountTotal minus this is the number of accounts that carry no LOB at all
-- and therefore land in no sub-portfolio.

WITH pairs AS (
    SELECT DISTINCT
        pa.PORTINFOID AS PortfolioId,
        pa.ACCGRPID AS AccountId,
        l.LOBNAME AS Value
    FROM dbo.portacct AS pa
    INNER JOIN dbo.policy AS p
        ON p.ACCGRPID = pa.ACCGRPID
    INNER JOIN dbo.lobdet AS l
        ON l.LOBDETID = p.LOBDETID
    WHERE NULLIF(LTRIM(RTRIM(l.LOBNAME)), '') IS NOT NULL
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
