-- Every portfolio in a Moody's RMS EDM, straight from portinfo — so a
-- portfolio with no accounts or locations is still returned. Seeds the
-- per-portfolio DataBridge summary; the DISTINCT list scripts only add to
-- entries that already exist. Read-only SELECT; the target EDM database is
-- selected at the connection level (no USE here).

SELECT
    p.PORTINFOID AS PortfolioId,
    p.PORTNAME AS PortfolioName
FROM dbo.portinfo AS p
ORDER BY p.PORTINFOID;
