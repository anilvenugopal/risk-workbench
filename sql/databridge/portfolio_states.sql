-- Per-portfolio states (first-level administrative divisions) for every
-- portfolio in a Moody's RMS EDM, with the number of accounts carrying each
-- division (spec 005 FR-005/FR-007 — the breakout preview's per-value account
-- count). Grouped and filtered on Admin1Code (P-12): the code arrives with the
-- exposure import and is the selection filter value; Admin1Name is a display
-- label only — absent until geocoding — so it is never a grouping key and
-- never synthesized from the code. Read-only SELECT; the target EDM database
-- is selected at the connection level (no USE here).
-- Adapted (set-based) from knowledge/sql scripts/portfolio_states.sql.
--
-- Caribbean addresses take their island's ISO3A CountryCode as the state value
-- instead of Admin1Code, because Moody's files the whole region as country CB
-- and Admin1 sits BELOW the island there: US Virgin Islands splits into 010 St
-- Croix / 020 St John / 030 St Thomas, so Admin1Code offers districts where the
-- analyst asks for islands (D5, measured 2026-08-11). Their label is NULL —
-- Admin1Name names the district, so MAX() over the VIR group would label the
-- whole territory "St Thomas", and the EDM carries no country-name column to
-- take a real name from (P-12: never synthesized).

SELECT
    pa.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryCode
         ELSE a.Admin1Code END AS Admin1Code,
    MAX(CASE WHEN a.CountryRMSCode = 'CB' THEN NULL
             ELSE NULLIF(LTRIM(RTRIM(a.Admin1Name)), '') END) AS Admin1Name,
    COUNT(DISTINCT pa.ACCGRPID) AS AccountCount
FROM dbo.portacct AS pa
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = pa.PORTINFOID
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
WHERE NULLIF(LTRIM(RTRIM(CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryCode
                              ELSE a.Admin1Code END)), '') IS NOT NULL
GROUP BY pa.PORTINFOID, pi.PORTNAME,
    CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryCode
         ELSE a.Admin1Code END
-- The CASE repeats here rather than ordering by the Admin1Code alias: the alias
-- now shadows a base column that is no longer in the GROUP BY.
ORDER BY PortfolioId,
    CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryCode
         ELSE a.Admin1Code END;
