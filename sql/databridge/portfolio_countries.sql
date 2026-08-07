-- Per-portfolio countries for every portfolio in a Moody's RMS EDM.
-- dbo.Address carries country only as codes — CountryCode (per CountryScheme)
-- with CountryRMSCode as Moody's internal code; the table has no country-name
-- column (knowledge/inventory/edm-schema.json, RMS_EDM.Address). Read-only
-- SELECT; the target EDM database is selected at the connection level (no USE
-- here).

SELECT DISTINCT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) AS Country
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
WHERE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) IS NOT NULL
ORDER BY PortfolioId, Country;
