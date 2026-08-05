-- Per-portfolio states (first-level administrative divisions) for every
-- portfolio in a Moody's RMS EDM, with the number of accounts carrying each
-- division (spec 005 FR-005/FR-007 — the breakout preview's per-value account
-- count). Grouped and filtered on Admin1Code (P-12): the code arrives with the
-- exposure import and is the selection filter value; Admin1Name is a display
-- label only — absent until geocoding — so it is never a grouping key and
-- never synthesized from the code. Read-only SELECT; the target EDM database
-- is selected at the connection level (no USE here).
-- Adapted (set-based) from knowledge/sql scripts/portfolio_states.sql.

SELECT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    a.Admin1Code AS Admin1Code,
    MAX(NULLIF(LTRIM(RTRIM(a.Admin1Name)), '')) AS Admin1Name,
    COUNT(DISTINCT pa.ACCGRPID) AS AccountCount
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
WHERE NULLIF(LTRIM(RTRIM(a.Admin1Code)), '') IS NOT NULL
GROUP BY pa.PORTINFOID, pi.PORTNAME, a.Admin1Code
ORDER BY PortfolioId, Admin1Code;
