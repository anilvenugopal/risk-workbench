-- Account ids per country for ONE portfolio in a Moody's RMS EDM — the
-- country breakout selection read. Value is the COALESCE'd country code
-- (P-12): the joins and the code filter mirror portfolio_countries.sql
-- exactly, so the values this query filters on are byte-identical to the
-- stored summary the analyst approved. ACCGRPID is the id Risk Modeler's
-- account operations accept as accountId; DISTINCT pairs give the
-- one-matching-location-admits-the-whole-account bucketing (W-3/W-11) by
-- construction. Read-only SELECT; the target EDM database is selected at the
-- connection level (no USE here). {{ portfolio_id }} is substituted by the
-- irp-integration DataBridge executor with injection-safe escaping.

SELECT DISTINCT
    COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) AS Value,
    pa.ACCGRPID AS AccountId
FROM dbo.portacct AS pa
INNER JOIN dbo.Property AS p
    ON p.ACCGRPID = pa.ACCGRPID
INNER JOIN dbo.Address AS a
    ON a.AddressID = p.ADDRESSID
WHERE pa.PORTINFOID = {{ portfolio_id }}
    AND COALESCE(NULLIF(a.CountryCode, ''), a.CountryRMSCode) IS NOT NULL
ORDER BY Value, AccountId;
