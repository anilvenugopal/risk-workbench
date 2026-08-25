-- Member-account count of ONE portfolio in a Moody's RMS EDM — the breakout
-- composition's read-back verification (spec 005 R1, revised 2026-08-05). One
-- scalar replaces the paginated REST enumeration, which cannot verify a
-- portfolio past 100,000 accounts (the wheel's 1,000-page proof-of-completeness
-- ceiling, W-20). Read-only SELECT; the target EDM database is selected at the
-- connection level. {{ portfolio_id }} is substituted by the irp-integration
-- DataBridge executor with injection-safe escaping.

SELECT COUNT(DISTINCT pa.ACCGRPID) AS AccountCount
FROM dbo.portacct AS pa
WHERE pa.PORTINFOID = {{ portfolio_id }};
