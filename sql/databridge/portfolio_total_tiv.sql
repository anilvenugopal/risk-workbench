-- Per-portfolio total TIV for every portfolio in a Moody's RMS EDM.
-- exposure_metrics.totalTIV is Moody's precomputed account-level rollup
-- (exposuretype 8019 = Account), summed per portfolio; a portfolio with no
-- accounts/metrics reports 0. Read-only SELECT; the target EDM database is
-- selected at the connection level (no USE here).
-- Adapted (set-based) from knowledge/sql scripts/portfolio_total_tiv.sql.

SELECT
    p.PORTINFOID AS PortfolioId,
    p.PORTNAME AS PortfolioName,
    COALESCE(SUM(m.totalTIV), 0) AS TotalTIV
FROM dbo.portinfo AS p
LEFT JOIN dbo.portacct AS pa
    ON pa.PORTINFOID = p.PORTINFOID
LEFT JOIN dbo.exposure_metrics AS m
    ON  m.exposureid = pa.ACCGRPID
    AND m.exposuretype = 8019 -- Account
GROUP BY p.PORTINFOID, p.PORTNAME
ORDER BY p.PORTINFOID;
