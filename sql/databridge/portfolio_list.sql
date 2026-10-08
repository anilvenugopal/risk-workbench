-- Every portfolio in a Moody's RMS EDM, straight from portinfo — so a
-- portfolio with no accounts or locations is still returned. Seeds the
-- per-portfolio DataBridge summary; the DISTINCT list scripts only add to
-- entries that already exist. Read-only SELECT; the target EDM database is
-- selected at the connection level (no USE here).
--
-- portfolio_ids holds the Risk Modeler portfolio ids to read, joined on CHAR(31),
-- or NULL for every portfolio (spec 207).

SELECT
    p.PORTINFOID AS PortfolioId,
    p.PORTNAME AS PortfolioName
FROM dbo.portinfo AS p
WHERE ({{ portfolio_ids }} IS NULL OR p.PORTINFOID IN (
          SELECT CAST(value AS INT) FROM STRING_SPLIT({{ portfolio_ids }}, CHAR(31))))
ORDER BY p.PORTINFOID;
