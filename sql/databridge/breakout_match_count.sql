-- How many accounts of ONE portfolio match a custom breakout's member set
-- (P-29): OR within a dimension, AND across dimensions, at account grain.
-- Each value expression mirrors its selection script, Caribbean branch
-- included, so the strings compared are the stored summary values the analyst
-- ticked.
--
-- Each values parameter is that dimension's selections joined on CHAR(31), or
-- NULL when the breakout does not filter the dimension, which drops its clause.
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
    -- Compared as FLOAT because pandas may render the smallint PERIL as "3.0",
    -- which no text comparison would match.
    AND ({{ peril_values }} IS NULL OR EXISTS (
        SELECT 1
        FROM dbo.Property AS p
        INNER JOIN dbo.loccvg AS lc
            ON lc.LOCID = p.LOCID
        WHERE p.ACCGRPID = pa.ACCGRPID
            AND CAST(lc.PERIL AS FLOAT) IN (
                SELECT TRY_CAST(value AS FLOAT)
                FROM STRING_SPLIT({{ peril_values }}, CHAR(31)))));
