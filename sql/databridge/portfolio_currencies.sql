-- Per-portfolio set of currencies the exposure is expressed in, for every
-- portfolio in a Moody's RMS EDM. Read-only SELECT; the target EDM database
-- is selected at the connection level (no USE here).
--
-- 8/7 D5: currency is defined in many places, so the previous single-column
-- read of loccvg.VALUECUR under-reported. This scans all 39 currency columns
-- across the 10 tables that hang off the portfolio's accounts and locations —
-- CIC's own currency check (Wendy's queries, 8/9) less reinsinf, plus
-- hdsteppolicy. Three currency columns are deliberately out:
--   reinsinf.LAYERCUR/EXCESSCUR  cession currency — what the cedent reinsured
--                                in, not what the exposure is valued in
--   trtydesc.TREATYCUR           treaty currency — its own column on the
--                                treaty table
--   agport.EXPOSCUR              the aggregate-exposure model keys on its own
--                                AGPORTID and never joins to portinfo
--
-- Each UNION ALL leg folds case/whitespace and groups itself down to its own
-- distinct portfolio/currency pairs. Without the per-leg GROUP BY, a large EDM
-- pushes 15-25M rows through the UNION ALL and the portinfo join to reach a
-- result of a few currencies per portfolio; with it, each leg hands over tens
-- of rows. The table scans are the same either way.

WITH acct AS (
    SELECT pa.PORTINFOID, pa.ACCGRPID
    FROM dbo.portacct AS pa
),
loc AS (
    SELECT a.PORTINFOID, p.LOCID
    FROM acct AS a
    INNER JOIN dbo.Property AS p
        ON p.ACCGRPID = a.ACCGRPID
),
stated AS (
    -- cedent-populated char columns hold both "usd" and "USD"; without the
    -- fold they render as two currencies
    SELECT a.PORTINFOID, f.Currency
    FROM acct AS a
    INNER JOIN dbo.policy AS po
        ON po.ACCGRPID = a.ACCGRPID
    CROSS APPLY (VALUES (po.UNDCOVCUR), (po.PARTOFCUR), (po.MINDEDCUR),
                        (po.MAXDEDCUR), (po.BLANLIMCUR), (po.BLANDEDCUR),
                        (po.BLANPRECUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY a.PORTINFOID, f.Currency

    UNION ALL SELECT a.PORTINFOID, f.Currency
    FROM acct AS a
    INNER JOIN dbo.policy AS po
        ON po.ACCGRPID = a.ACCGRPID
    INNER JOIN dbo.polcvg AS pc
        ON pc.POLICYID = po.POLICYID
    CROSS APPLY (VALUES (pc.LIMITCUR), (pc.DEDUCTCUR),
                        (pc.PREMCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY a.PORTINFOID, f.Currency

    UNION ALL SELECT a.PORTINFOID, f.Currency
    FROM acct AS a
    INNER JOIN dbo.hdsteppolicy AS sp
        ON sp.ACCGRPID = a.ACCGRPID
    CROSS APPLY (VALUES (sp.PAYOUTCUR), (sp.EXCESSCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY a.PORTINFOID, f.Currency

    UNION ALL SELECT l.PORTINFOID, f.Currency
    FROM loc AS l
    INNER JOIN dbo.loccvg AS lc
        ON lc.LOCID = l.LOCID
    CROSS APPLY (VALUES (lc.VALUECUR), (lc.LIMITCUR),
                        (lc.DEDUCTCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY l.PORTINFOID, f.Currency

    UNION ALL SELECT l.PORTINFOID, f.Currency
    FROM loc AS l
    INNER JOIN dbo.eqdet AS d
        ON d.LOCID = l.LOCID
    CROSS APPLY (VALUES (d.SITELIMCUR), (d.SITEDEDCUR), (d.COMBINEDLIMCUR),
                        (d.COMBINEDDEDCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY l.PORTINFOID, f.Currency

    UNION ALL SELECT l.PORTINFOID, f.Currency
    FROM loc AS l
    INNER JOIN dbo.hudet AS d
        ON d.LOCID = l.LOCID
    CROSS APPLY (VALUES (d.SITELIMCUR), (d.SITEDEDCUR), (d.COMBINEDLIMCUR),
                        (d.COMBINEDDEDCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY l.PORTINFOID, f.Currency

    UNION ALL SELECT l.PORTINFOID, f.Currency
    FROM loc AS l
    INNER JOIN dbo.fldet AS d
        ON d.LOCID = l.LOCID
    CROSS APPLY (VALUES (d.SITELIMCUR), (d.SITEDEDCUR), (d.COMBINEDLIMCUR),
                        (d.COMBINEDDEDCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY l.PORTINFOID, f.Currency

    UNION ALL SELECT l.PORTINFOID, f.Currency
    FROM loc AS l
    INNER JOIN dbo.frdet AS d
        ON d.LOCID = l.LOCID
    CROSS APPLY (VALUES (d.SITELIMCUR), (d.SITEDEDCUR), (d.COMBINEDLIMCUR),
                        (d.COMBINEDDEDCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY l.PORTINFOID, f.Currency

    UNION ALL SELECT l.PORTINFOID, f.Currency
    FROM loc AS l
    INNER JOIN dbo.todet AS d
        ON d.LOCID = l.LOCID
    CROSS APPLY (VALUES (d.SITELIMCUR), (d.SITEDEDCUR), (d.COMBINEDLIMCUR),
                        (d.COMBINEDDEDCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY l.PORTINFOID, f.Currency

    UNION ALL SELECT l.PORTINFOID, f.Currency
    FROM loc AS l
    INNER JOIN dbo.trdet AS d
        ON d.LOCID = l.LOCID
    CROSS APPLY (VALUES (d.SITELIMCUR), (d.SITEDEDCUR), (d.COMBINEDLIMCUR),
                        (d.COMBINEDDEDCUR)) AS v(Currency)
    CROSS APPLY (VALUES (UPPER(LTRIM(RTRIM(v.Currency))))) AS f(Currency)
    GROUP BY l.PORTINFOID, f.Currency
)
SELECT DISTINCT
    s.PORTINFOID AS PortfolioId,
    pi.PORTNAME AS PortfolioName,
    s.Currency AS Currency
FROM stated AS s
INNER JOIN dbo.portinfo AS pi
    ON pi.PORTINFOID = s.PORTINFOID
WHERE s.Currency IS NOT NULL
  AND s.Currency <> ''
ORDER BY PortfolioId, Currency;
