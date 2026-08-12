-- How many accounts of ONE portfolio match a custom breakout's member set —
-- the Add-time emptiness check (spec 005 follow-on P-29 / FR-021 as revised
-- 2026-08-12). One row, one integer, on the request path: the constitution's
-- Article 11 request-path exception (v3.2.0) admits exactly this shape.
--
-- OR within a dimension, AND across dimensions (P-20), at ACCOUNT grain: each
-- EXISTS asks whether the account carries at least one selected value of that
-- dimension, which is the same whole-account semantics the run gets by
-- intersecting the per-dimension selections app-side (T-14 — this query counts,
-- it does not replace that set algebra). Every value expression here mirrors
-- its selection script exactly, Caribbean branch included (D5/P-28), so the
-- strings compared are byte-identical to the stored summary values the analyst
-- ticked. The selection scripts' blank/NULL guards are omitted: a NULL or blank
-- value can never equal a listed one, and the lists come from the summary,
-- which is already scrubbed.
--
-- Each {{ *_values }} parameter is the dimension's selected values joined on
-- CHAR(31) (ASCII unit separator — no EDM descriptor contains it), or NULL when
-- the breakout does not filter that dimension, which makes its clause
-- `NULL IS NULL` and drops it. Every placeholder sits in a bare value position,
-- never inside a string literal: the irp-integration templater substitutes a
-- placeholder found inside quotes RAW, and only a value-context placeholder
-- gets its quoting and its injection-safe escaping.
--
-- Read-only SELECT; the target EDM database is selected at the connection level
-- (no USE here).

SELECT COUNT(DISTINCT pa.ACCGRPID) AS AccountCount
FROM dbo.portacct AS pa
WHERE pa.PORTINFOID = {{ portfolio_id }}
    AND ({{ lob_values }} IS NULL OR EXISTS (
        SELECT 1
        FROM dbo.policy AS pol
        INNER JOIN dbo.lobdet AS l
            ON l.LOBDETID = pol.LOBDETID
        WHERE pol.ACCGRPID = pa.ACCGRPID
            AND l.LOBNAME IN (
                SELECT value FROM STRING_SPLIT({{ lob_values }}, CHAR(31)))))
    AND ({{ state_values }} IS NULL OR EXISTS (
        SELECT 1
        FROM dbo.Property AS p
        INNER JOIN dbo.Address AS a
            ON a.AddressID = p.ADDRESSID
        WHERE p.ACCGRPID = pa.ACCGRPID
            AND CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryCode
                     ELSE a.Admin1Code END IN (
                SELECT value FROM STRING_SPLIT({{ state_values }}, CHAR(31)))))
    AND ({{ country_values }} IS NULL OR EXISTS (
        SELECT 1
        FROM dbo.Property AS p
        INNER JOIN dbo.Address AS a
            ON a.AddressID = p.ADDRESSID
        WHERE p.ACCGRPID = pa.ACCGRPID
            AND CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryRMSCode
                     ELSE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode)
                END IN (
                SELECT value FROM STRING_SPLIT({{ country_values }}, CHAR(31)))))
    -- loccvg.PERIL is a smallint (W-21) and the summary renders it through
    -- pandas, so the stored value is "3" — or "3.0" if a NULL ever upcasts the
    -- column to float. Comparing as FLOAT matches either spelling; comparing as
    -- text would read "3.0" as no match and refuse a breakout that has accounts.
    AND ({{ peril_values }} IS NULL OR EXISTS (
        SELECT 1
        FROM dbo.Property AS p
        INNER JOIN dbo.loccvg AS lc
            ON lc.LOCID = p.LOCID
        WHERE p.ACCGRPID = pa.ACCGRPID
            AND CAST(lc.PERIL AS FLOAT) IN (
                SELECT TRY_CAST(value AS FLOAT)
                FROM STRING_SPLIT({{ peril_values }}, CHAR(31)))));
