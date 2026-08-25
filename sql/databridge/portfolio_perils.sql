-- Per-portfolio perils for every portfolio in a Moody's RMS EDM, with the
-- number of accounts carrying each peril (spec 005 follow-on P-19 — the
-- custom-grouping pane's per-value account count). The value is loccvg.PERIL,
-- a numeric RMS peril code (smallint — W-21): there is no in-EDM code→name
-- lookup, so the code is its own display (label null, P-12 — never
-- synthesized). Sub-peril detail (fire-following, flood) rides its parent
-- peril's coverage rows (W-21), so loccvg alone is the enumeration source.
-- Read-only SELECT; the target EDM database is selected at the connection
-- level (no USE here). Joins mirror breakout_peril_accounts.sql.

SELECT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    lc.PERIL AS Peril,
    COUNT(DISTINCT pa.ACCGRPID) AS AccountCount
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.loccvg AS lc
    ON lc.LOCID = p.LOCID
WHERE lc.PERIL IS NOT NULL
GROUP BY pa.PORTINFOID, pi.PORTNAME, lc.PERIL
ORDER BY PortfolioId, Peril;
