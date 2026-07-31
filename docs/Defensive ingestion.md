Below is a pragmatic “defensive ingestion” pattern you can use when you receive broker‑supplied EDM/RDM pairs whose internal consistency you cannot trust.

This is process + SQL logic that you implement around Risk Modeler / Data Bridge; there is no built‑in feature that does this for you. It aims to:

Detect broken EDM↔RDM relationships early.
Classify incoming packages as “trusted / usable” vs “quarantine / needs broker follow‑up”.
Avoid silently mis‑mapping losses to the wrong portfolios.

Where I say “EDM” and “RDM”, I’m assuming they’re imported to Data Bridge or another SQL Server instance you can query. Risk Modeler expects you to associate RDMs with an EDM during upload, but doesn’t validate that IDs actually line up. 1 2

1. High‑level workflow

For each broker package {EDM.bak, RDM.bak}:

Stage import (do not associate in Risk Modeler yet)

Restore to a staging SQL instance or Data Bridge with separate schemas (e.g., Broker1_Staging_EDM, Broker1_Staging_RDM).
Do not immediately upload/associate the RDM to your Risk Modeler tenant’s production exposure sets. 1 2

Extract key metadata

From RDM:
Distinct analyses (RMS_RDM.rdm_analysis)
The recorded source EDM identity if present (SRCEDM, SRCEDMDBGUID, etc., depending on RDM version – this is how IRP tracks source EDMs for exported losses). 3
Distinct exposure IDs used in results tables (PORTINFOID or equivalent in portfolio‑level PLT tables).
From EDM:
Portfolios: RMS_EDM.portinfo (keys, portfolio numbers, names, and any cedant identifiers). 4

Run consistency checks (see sections 2–3)

Check that the EDM identity in the RDM matches the actual EDM you received.
Check that RDM exposure IDs exist in the EDM (ID overlap / referential integrity).
Optionally, heuristic checks on portfolio counts and basic attributes.

Classify

If checks pass → mark as trusted and then:
Upload the EDM to Risk Modeler if not already there. 5 6
Upload the RDM and explicitly set Associated Database = that EDM. 1
If checks fail → mark as quarantine:
Do not associate RDM with any EDM in production.
Generate a report and send back to broker for correction.

Log everything

Maintain a mapping registry {BrokerPackageID → EDMGUID → RDMGUID → Status} in your own database so you can prove which EDM each trusted RDM is linked to.
2. Check 1 – RDM’s “source EDM” vs supplied EDM

IRP‑generated RDMs record which EDM they came from (source EDM name / GUID) to support downstream joins and exports. 3 Broker RDMs may or may not preserve this, but if they do, use it as the first gate:

Pseudo‑SQL (conceptual):

SELECT
    a.ANLSID,
    a.SRCEDM,          -- source EDM name
    a.SRCEDMDBGUID     -- source EDM GUID, if available
FROM RMS_RDM.rdm_analysis AS a;

From the staged EDM:

SELECT
    v.DBGUID,   -- or equivalent unique identifier for this EDM instance
    v.DBNAME
FROM RMS_EDM.rmsver AS v;  -- typical place where DB identity/version is stored

Logic:

If SRCEDMDBGUID is present and ≠ EDM’s DBGUID → hard fail (quarantine).
If GUID is missing but SRCEDM name obviously doesn’t correspond to the supplied EDM (different cedant, radically different naming) → strong warning; likely fail as well.
If they match (or there is no source info and nothing is contradictory), continue to Check 2.

Rationale: The entire EDM/RDM schema is designed under the assumption that RDMs are used with the EDM they were generated from, or a copy that preserved IDs. 3 7

3. Check 2 – Referential integrity between RDM results and EDM portfolios

You want to know whether the IDs used in the RDM portfolios can even exist in the EDM’s portinfo table. 4

Example (portfolio‑level PLT; adjust to your RDM’s actual table names):

-- Portfolios referenced in RDM losses
SELECT DISTINCT p.PORTINFOID
INTO #RDM_PORTS
FROM RMS_RDM.plt.rdm_port AS p;

-- Portfolios present in EDM
SELECT DISTINCT e.PORTINFOID
INTO #EDM_PORTS
FROM RMS_EDM.portinfo AS e;

-- Check for missing IDs
SELECT rp.PORTINFOID
FROM #RDM_PORTS rp
LEFT JOIN #EDM_PORTS ep ON rp.PORTINFOID = ep.PORTINFOID
WHERE ep.PORTINFOID IS NULL;

Evaluate:

If the query returns any rows → RDM references portfolios that don’t exist in this EDM → hard fail.
If all IDs are present:
Compare counts:
COUNT(DISTINCT RDM PORTINFOID) vs COUNT(DISTINCT EDM PORTINFOID):
If RDM subset of EDM: plausible (e.g., broker sent larger EDM than actually used for these analyses).
If RDM has almost all EDM portfolios, yet your domain knowledge says the broker ran this on a much smaller subset → raise a warning, but technically still consistent.

You can do analogous checks for:

Accounts: ACCGRPID (if you care about account‑level RDM results).
Locations: LOCID.
4. Check 3 – Heuristic portfolio attribute comparison (optional but useful)

Because brokers sometimes rebuild EDMs, even if IDs “exist” they could point to different exposures. You can add a heuristic layer:

Sample a few portfolios used in the RDM (random 20–50).
For each:
Pull basic attributes from EDM: cedant, portfolio number/name, total TIV, location count.

Example:

SELECT
    p.PORTINFOID,
    p.PORTFOLIONUM,
    p.PORTFOLIONAME,
    SUM(m.TIV) AS TotalTIV,         -- using a materialized metrics table or summing property values
    COUNT(DISTINCT a.ACCGRPID) AS AccountCount
FROM RMS_EDM.portinfo p
JOIN RMS_EDM.portacct pa ON p.PORTINFOID = pa.PORTINFOID
JOIN RMS_EDM.accgrp a ON pa.ACCGRPID = a.ACCGRPID
JOIN RMS_EDM.Property m ON a.ACCGRPID = m.ACCGRPID
WHERE p.PORTINFOID IN (/*sample of RDM PORTINFOID*/)
GROUP BY p.PORTINFOID, p.PORTFOLIONUM, p.PORTFOLIONAME;

Then:

Compare the above with any metadata the broker provides (ideally a sidecar file) – if they don’t provide anything, you at least check if metrics look “reasonable” compared to what you expect for that cedant / program.

If you have historic “trusted” EDMs/RDMs for the same program, you can also:

Compare TIV distributions and counts against previous versions to flag extreme divergences as suspect.

This remains heuristic, but it gives you an extra sanity layer.

5. Risk Modeler / Data Bridge integration steps for trusted packages

Once a package passes the checks:

Upload the EDM to Risk Modeler
Use normal EDM upload (or Data Bridge loading) and create a new exposure set. 1 2
Upload the RDM and associate it with that EDM
In the “EDM and RDM Upload” dialog, set:
Database Type: RDM
Associated Database: the EDM you just uploaded. 1
This ties results to that exposure set and ensures the RDM appears on the Results tab when you open the corresponding exposure. 1
Record in your registry:
Tenant / exposure set ID
EDM database name / GUID
RDM database name / GUID
Broker package ID
Status = TrustedLinked.

This establishes a clean, auditable chain from the broker package to the production IRP datasets.

6. Handling quarantine cases

If any of the hard checks fail:

Do not associate the RDM with any production EDM in Risk Modeler.

Keep the offending EDM/RDM only in a staging SQL area or a special “quarantine” Data Bridge folder.

Generate a structured report for the broker, e.g.:

RDM vs EDM identity mismatch (SRCEDMDBGUID vs EDM GUID).
N portfolios in RDM do not exist in EDM (list some IDs).
Any other anomalies you found.

Ask them specifically either to:

Supply the original EDM used to generate the RDM, or
Re‑export their “big aggregated EDM” using “Preserve database identifiers” from the source EDM so that portfolio IDs remain stable. 7
7. Why this is necessary

The Moody’s tooling assumes:

Each exposure set contains one EDM, and any analyses / RDMs associated with that exposure set use the same database identifiers. 1
When you need to clone or share exposures, “Preserve database identifiers” exists specifically to avoid having to maintain ID mapping logic between EDM/RDM in downstream systems. 7

Once a broker breaks those assumptions (e.g., by rebuilding EDMs without preserving IDs or by aggregating multiple EDMs arbitrarily), there is no IRP feature that can recover the original mapping with certainty. Your defensive ingestion layer is there to:

Detect when the assumptions still hold (trusted path), and
Safely reject when they don’t (quarantine path), instead of silently mis‑linking data.

If you’d like, I can translate this into a concrete Python/SQL “ingestion validator” skeleton that you could drop into a pipeline (e.g., using pyodbc/sqlalchemy against your staging SQL Server / Data Bridge).

1: Step by Step; Upload Options https://support.rms.com/o/html-doc/OLH_Content/RiskModeler_Help_Center/Content/HowTo/EDMUpload.htm
2: Using Data Bridge in Risk Modeler https://support.rms.com/o/html-doc/OLH_Content/RiskModeler_Help_Center/Content/DataBridge_HowTo/UsingDBinRM.htm
3: Tables and Fields to Support the HD Financial Model https://support.rms.com/o/html-doc/OLH_Content/RiskModeler_Help_Center/Content/RL_and_RB_Schema/About_database_schema.htm
4: EDM Database Schema https://support.rms.com/o/html-doc/OLH_Content/RiskModeler_Help_Center/Content/RL_and_RB_Schema/RMS_EDM_Database_Schema.htm
5: Import and Upload your Data https://support.rms.com/o/html-doc/OLH_Content/RiskModeler_Help_Center/Content/HowTo/ImportUploadIntro.htm
6: EDM Details https://support.rms.com/o/html-doc/OLH_Content/RiskModeler_Help_Center/Content/HowTo/EDMDetails.htm
7: Exporting to EDM; Export Options https://support.rms.com/o/html-doc/OLH_Content/RiskModeler_Help_Center/Content/HowTo/ExportEDM.htm