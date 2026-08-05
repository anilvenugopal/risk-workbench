-- Per-portfolio account total for every portfolio in a Moody's RMS EDM — the
-- denominator of the breakout preview's overlap statement (spec 005 FR-007 /
-- P-13): repeats = (sum of per-value account counts) - AccountTotal.
-- Read-only SELECT; the target EDM database is selected at the connection
-- level (no USE here).

SELECT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    COUNT(DISTINCT pa.ACCGRPID) AS AccountTotal
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
GROUP BY pa.PORTINFOID, pi.PORTNAME
ORDER BY pa.PORTINFOID;
