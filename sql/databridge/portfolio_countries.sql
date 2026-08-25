-- Per-portfolio countries for every portfolio in a Moody's RMS EDM, with the
-- number of accounts carrying each country (spec 005 FR-005/FR-007 — the
-- breakout preview's per-value account count). dbo.Address carries country
-- only as codes — CountryCode (per CountryScheme) with CountryRMSCode as
-- Moody's internal code; the table has no country-name column
-- (knowledge/inventory/edm-schema.json, RMS_EDM.Address) — so the code is its
-- own display and the label is never synthesized (P-12). Read-only SELECT; the
-- target EDM database is selected at the connection level (no USE here).
--
-- The Caribbean is the one region Moody's files under a country code that is
-- not a country: every island carries CountryRMSCode 'CB' with the island's own
-- ISO3A code in CountryCode (measured 2026-08-11 — PRI 2,709 addresses, VIR 154
-- across three Admin1 districts). Taking CountryCode there would split the
-- region into ~30 single-island countries and leave nothing to select for the
-- whole-Caribbean breakout CIC actually runs, so CB wins over the ISO code and
-- portfolio_states.sql takes the island as the state value (D5).

SELECT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryRMSCode
         ELSE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) END AS Country,
    COUNT(DISTINCT pa.ACCGRPID) AS AccountCount
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
WHERE CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryRMSCode
           ELSE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) END IS NOT NULL
GROUP BY pa.PORTINFOID, pi.PORTNAME,
    CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryRMSCode
         ELSE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) END
ORDER BY PortfolioId, Country;
