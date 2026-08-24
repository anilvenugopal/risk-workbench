-- Per-portfolio account total for every portfolio in a Moody's RMS EDM — the
-- account_total denominator of the breakout preview's overlap statement
-- (spec 005 FR-007); the overlap counts themselves are measured per account
-- by the portfolio_*_coverage.sql scripts.
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
