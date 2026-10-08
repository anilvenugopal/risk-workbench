-- Per-portfolio lines of business for every portfolio in a Moody's RMS EDM,
-- with the number of accounts carrying each LOB (spec 005 FR-005/FR-007 —
-- the breakout preview's per-value account count). Read-only SELECT; the
-- target EDM database is selected at the connection level (no USE here).
-- Adapted (set-based) from knowledge/sql scripts/portfolio_lines_of_business.sql.
--
-- portfolio_ids holds the Risk Modeler portfolio ids to read, joined on CHAR(31),
-- or NULL for every portfolio (spec 207).

SELECT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    l.LOBNAME AS LineOfBusiness,
    COUNT(DISTINCT pa.ACCGRPID) AS AccountCount
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.policy AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.lobdet AS l
    ON l.LOBDETID = p.LOBDETID
WHERE NULLIF(LTRIM(RTRIM(l.LOBNAME)), '') IS NOT NULL
  AND ({{ portfolio_ids }} IS NULL OR pa.PORTINFOID IN (
          SELECT CAST(value AS INT) FROM STRING_SPLIT({{ portfolio_ids }}, CHAR(31))))
GROUP BY pa.PORTINFOID, pi.PORTNAME, l.LOBNAME
ORDER BY PortfolioId, LineOfBusiness;
