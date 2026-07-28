-- Per-portfolio currencies used by location coverage values for every
-- portfolio in a Moody's RMS EDM. Read-only SELECT; the target EDM database
-- is selected at the connection level (no USE here).
-- Adapted (set-based) from knowledge/sql scripts/portfolio_currencies.sql.

SELECT DISTINCT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    c.VALUECUR AS Currency
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.loccvg AS c
    ON c.LOCID = p.LOCID
WHERE NULLIF(LTRIM(RTRIM(c.VALUECUR)), '') IS NOT NULL
ORDER BY PortfolioId, Currency;
