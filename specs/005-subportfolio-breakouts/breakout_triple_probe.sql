-- Breakout option-filtering probe.
-- Read-only against the EDM. Select the EDM database in the session first
-- (no USE here, matching the sql/databridge convention).
--
-- Q1: how many distinct (country, state, peril) location combinations does a
--     source portfolio carry? That set is what would be stored in
--     irp_portfolio.exposure_detail to drive option filtering with no live query.
-- Q2: does an Admin1Code ever belong to more than one country? If it does, the
--     stored state value needs a country qualifier.
--
-- The CASE expressions are copied verbatim from portfolio_countries.sql and
-- portfolio_states.sql so the counts match what the feature would store.
--
-- Run the batches in order. #loc and #triples stay available afterwards, so you
-- can ask follow-up questions without rescanning.

USE [TY2607_SampleCo_0726_25EDM_4];

SET NOCOUNT ON;
GO

IF OBJECT_ID('tempdb..#loc') IS NOT NULL DROP TABLE #loc;
IF OBJECT_ID('tempdb..#triples') IS NOT NULL DROP TABLE #triples;
GO


-- Batch 1: one row per (portfolio, account, location).
-- To scope to a single portfolio, uncomment the WHERE clause.

SELECT
    pa.PORTINFOID,
    pa.ACCGRPID,
    p.LOCID,
    CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryRMSCode
         ELSE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) END AS Country,
    NULLIF(LTRIM(RTRIM(
        CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryCode
             ELSE a.Admin1Code END)), '') AS Admin1Code
INTO #loc
FROM dbo.portacct AS pa
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
-- WHERE pa.PORTINFOID = 1
;
GO


-- Batch 2: the distinct triples themselves.
-- A blank state is kept as NULL rather than dropped, because the location still
-- carries a country and a peril and dropping it would hide those values.

SELECT DISTINCT
    l.PORTINFOID,
    l.Country,
    l.Admin1Code,
    lc.PERIL
INTO #triples
FROM #loc AS l
INNER JOIN dbo.loccvg AS lc
    ON lc.LOCID = l.LOCID
WHERE lc.PERIL IS NOT NULL;
GO


-- Q1: the answer. TripleCount is the number to report.

SELECT
    pi.PORTINFOID,
    pi.PORTNAME,
    s.Accounts,
    s.Locations,
    COUNT(*) AS TripleCount,
    COUNT(DISTINCT t.Country) AS Countries,
    COUNT(DISTINCT t.Admin1Code) AS States,
    COUNT(DISTINCT t.PERIL) AS Perils
FROM #triples AS t
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = t.PORTINFOID
INNER JOIN (
    SELECT PORTINFOID,
           COUNT(DISTINCT ACCGRPID) AS Accounts,
           COUNT(DISTINCT LOCID) AS Locations
    FROM #loc
    GROUP BY PORTINFOID
) AS s
    ON s.PORTINFOID = t.PORTINFOID
GROUP BY pi.PORTINFOID, pi.PORTNAME, s.Accounts, s.Locations
ORDER BY TripleCount DESC;
GO


-- Q2: state codes that span more than one country. Zero rows is the answer we
-- want. Any rows list the offending code with each country it appears under.

SELECT d.Admin1Code, d.Country
FROM (SELECT DISTINCT Country, Admin1Code FROM #loc WHERE Admin1Code IS NOT NULL) AS d
INNER JOIN (
    SELECT Admin1Code
    FROM (SELECT DISTINCT Country, Admin1Code FROM #loc WHERE Admin1Code IS NOT NULL) AS x
    GROUP BY Admin1Code
    HAVING COUNT(*) > 1
) AS bad
    ON bad.Admin1Code = d.Admin1Code
ORDER BY d.Admin1Code, d.Country;
GO
