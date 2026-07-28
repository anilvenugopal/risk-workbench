-- Per-portfolio lines of business for every portfolio in a Moody's RMS EDM.
-- Read-only SELECT; the target EDM database is selected at the connection
-- level (no USE here).
-- Adapted (set-based) from knowledge/sql scripts/portfolio_lines_of_business.sql.

SELECT DISTINCT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    l.LOBNAME AS LineOfBusiness
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.policy AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.lobdet AS l
    ON l.LOBDETID = p.LOBDETID
WHERE NULLIF(LTRIM(RTRIM(l.LOBNAME)), '') IS NOT NULL
ORDER BY PortfolioId, LineOfBusiness;
