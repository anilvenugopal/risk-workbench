-- Per-portfolio states (first-level administrative areas) for every portfolio
-- in a Moody's RMS EDM. Read-only SELECT; the target EDM database is selected
-- at the connection level (no USE here).
-- Adapted (set-based) from knowledge/sql scripts/portfolio_states.sql.

SELECT DISTINCT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    COALESCE(NULLIF(a.Admin1Name, ''), a.Admin1Code) AS State
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
WHERE COALESCE(NULLIF(a.Admin1Name, ''), a.Admin1Code) IS NOT NULL
ORDER BY PortfolioId, State;
