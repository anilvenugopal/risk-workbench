-- Per-portfolio countries for every portfolio in a Moody's RMS EDM, with the
-- number of accounts carrying each country (spec 005 FR-005/FR-007 — the
-- breakout preview's per-value account count). dbo.Address carries country
-- only as codes — CountryCode (per CountryScheme) with CountryRMSCode as
-- Moody's internal code; the table has no country-name column
-- (knowledge/inventory/edm-schema.json, RMS_EDM.Address) — so the code is its
-- own display and the label is never synthesized (P-12). Read-only SELECT; the
-- target EDM database is selected at the connection level (no USE here).

SELECT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) AS Country,
    COUNT(DISTINCT pa.ACCGRPID) AS AccountCount
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
WHERE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) IS NOT NULL
GROUP BY pa.PORTINFOID, pi.PORTNAME,
    COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode)
ORDER BY PortfolioId, Country;
