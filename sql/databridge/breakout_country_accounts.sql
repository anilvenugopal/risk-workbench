-- Account ids per country for ONE portfolio in a Moody's RMS EDM — the
-- country breakout selection read. Value is the country code (P-12): the
-- joins, the code filter, and the Caribbean branch (D5, rationale in
-- portfolio_countries.sql) mirror portfolio_countries.sql exactly, so the
-- values this query filters on are byte-identical to the stored summary the
-- analyst approved. ACCGRPID is the id Risk Modeler's account operations
-- accept as accountId; DISTINCT pairs give the
-- one-matching-location-admits-the-whole-account bucketing (W-3/W-11) by
-- construction. Read-only SELECT; the target EDM database is selected at the
-- connection level (no USE here). {{ portfolio_id }} is substituted by the
-- irp-integration DataBridge executor with injection-safe escaping.

SELECT DISTINCT
    CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryRMSCode
         ELSE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) END AS Value,
    pa.ACCGRPID AS AccountId
FROM dbo.portacct AS pa
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
WHERE pa.PORTINFOID = {{ portfolio_id }}
    AND CASE WHEN a.CountryRMSCode = 'CB' THEN a.CountryRMSCode
             ELSE COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) END IS NOT NULL
ORDER BY Value, AccountId;
