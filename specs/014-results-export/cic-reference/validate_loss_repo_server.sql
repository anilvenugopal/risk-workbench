/* ============================================================================
   Loss repository server validation — spec 014 (Loss Results Export)

   Run in SQL Server Management Studio, connected to the server CIC gave you,
   with Results to Grid. Every statement is read-only. Run section by section
   (highlight + F5) so a failure in one does not hide the others.

   Each section says:  WHAT it checks · GOOD result · ASK when it is not good.
   The asks are collected again at the bottom (section 9).
   ============================================================================ */


/* ---------------------------------------------------------------------------
   1. WHERE AM I, WHO AM I
   GOOD: the server is CIC's single SQL Server (plan T-29): it will hold
         CRE_Trial_ELT_Repository and the Workbench application database;
         the login is the one CIC created for the Workbench or for you.
   ASK : if the server name is not the one CIC gave: "Is <server> the SQL
         Server that will host CRE_Trial_ELT_Repository and the Workbench
         database?"
   --------------------------------------------------------------------------- */
SELECT @@SERVERNAME            AS server_name,
       SERVERPROPERTY('MachineName')   AS machine_name,
       SERVERPROPERTY('InstanceName')  AS instance_name,
       SERVERPROPERTY('ProductVersion') AS sql_version,
       SERVERPROPERTY('Edition')       AS edition,
       SUSER_SNAME()           AS login_name,
       ORIGINAL_LOGIN()        AS original_login,
       HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW ANY DATABASE') AS can_view_any_database;
-- can_view_any_database = 0 means section 2 lists ONLY databases you can enter;
-- others may exist on the server without appearing.


/* ---------------------------------------------------------------------------
   2. DATABASES ON THIS SERVER, WITH SIZE AND ISOLATION SETTINGS
   GOOD: CRE_Trial_ELT_Repository present with data (hundreds of MB or more,
         see section 3 for the exact figure). The Workbench application
         database may not exist yet (CIC has not named it, plan O-05).
   ASK : if RCSI is 0 on the repository, nothing — the Workbench reads the
         manifest with READUNCOMMITTED (plan T-25). "What will the Workbench
         application database on this server be called?"
   --------------------------------------------------------------------------- */
SELECT d.name,
       d.state_desc,
       d.recovery_model_desc,
       d.is_read_committed_snapshot_on,
       d.snapshot_isolation_state_desc,
       d.compatibility_level,
       HAS_DBACCESS(d.name)  AS i_can_enter,
       SUSER_SNAME(d.owner_sid) AS owner_login,
       CONVERT(decimal(12,1), SUM(mf.size) * 8.0 / 1024) AS allocated_mb
FROM sys.databases d
LEFT JOIN sys.master_files mf ON mf.database_id = d.database_id
GROUP BY d.name, d.state_desc, d.recovery_model_desc, d.is_read_committed_snapshot_on,
         d.snapshot_isolation_state_desc, d.compatibility_level, d.owner_sid
ORDER BY d.name;


/* ---------------------------------------------------------------------------
   3. THE REPOSITORY: HOW MUCH DATA IT ACTUALLY HOLDS
   GOOD: reserved/data in the hundreds of MB or GB. Wendy: tens of thousands
         of Data rows a year, millions of loss rows.
   ASK : if data is a few MB: "CRE_Trial_ELT_Repository is still empty. When
         will the load of Client, Data, RMSELT, RMS_HistoricalRDS, and
         Lookup_RMS_HistoricalRDS be re-run?" (the 2026-09-09 load failed)
   --------------------------------------------------------------------------- */
USE CRE_Trial_ELT_Repository;
EXEC sp_spaceused;                       -- database_size vs reserved/data
SELECT USER_NAME() AS db_user,
       IS_MEMBER('db_owner')      AS is_db_owner,
       IS_MEMBER('db_datareader') AS is_datareader,
       IS_MEMBER('db_datawriter') AS is_datawriter,
       IS_MEMBER('db_ddladmin')   AS is_ddladmin;


/* ---------------------------------------------------------------------------
   4. THE FIVE CIC TABLES THE EXPORT NEEDS, AND ANY STAGE OBJECTS
   Expected in dbo: Client, Data, RMSELT, RMS_HistoricalRDS,
   Lookup_RMS_HistoricalRDS (all five in this database, plan T-21).
   GOOD: all five present with row counts > 0 (RMSELT in the millions).
   ASK : each missing table: "Please restore/copy dbo.<name> into this
         database." Any row for schema 'stage' means a previous install of
         loss_schema.sql exists — tell PremiumIQ which version.
   --------------------------------------------------------------------------- */
SELECT s.name AS [schema], t.name AS [table], SUM(p.rows) AS [rows],
       t.create_date, t.modify_date
FROM sys.tables t
JOIN sys.schemas s   ON s.schema_id = t.schema_id
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY s.name, t.name, t.create_date, t.modify_date
ORDER BY s.name, t.name;
-- Empty result while section 3 shows real data = you lack SELECT on every
-- table (metadata is hidden without it). ASK: "Grant SELECT on dbo.Client
-- and read access to the stage schema to login <login>" (full list §9).

-- Expected-vs-found, explicit:
SELECT expected.name,
       CASE WHEN OBJECT_ID('dbo.' + expected.name, 'U') IS NULL THEN 'MISSING' ELSE 'present' END AS status
FROM (VALUES ('Client'), ('Data'), ('RMSELT'), ('RMS_HistoricalRDS'),
             ('Lookup_RMS_HistoricalRDS')) AS expected(name);
-- Lookup_RMS_HistoricalRDS MISSING while the other four are present: ask CIC
-- to load it here too; the load procedure reads dbo.Lookup_RMS_HistoricalRDS
-- in this database (plan T-21).

-- Column shape of what exists, to compare with cic-reference/*.sql
SELECT t.name AS [table], c.column_id, c.name AS [column], ty.name AS [type],
       CASE WHEN ty.name IN ('nvarchar','nchar') THEN c.max_length / 2 ELSE c.max_length END AS [length],
       c.is_nullable, c.is_identity
FROM sys.columns c
JOIN sys.tables t ON t.object_id = c.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE t.name IN ('Client', 'Data', 'RMSELT', 'RMS_HistoricalRDS', 'Lookup_RMS_HistoricalRDS')
ORDER BY t.name, c.column_id;

-- Keys and indexes on the targets (design assumes NONE on Data/RMSELT/RMS_HistoricalRDS)
SELECT t.name AS [table], i.name AS [index], i.type_desc, i.is_primary_key, i.is_unique
FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
WHERE t.name IN ('Client', 'Data', 'RMSELT', 'RMS_HistoricalRDS', 'Lookup_RMS_HistoricalRDS')
  AND i.index_id > 0
ORDER BY t.name, i.index_id;

-- Existing synonyms and procedures (a prior install, or CIC's own)
SELECT 'synonym' AS kind, SCHEMA_NAME(schema_id) AS [schema], name, base_object_name AS target FROM sys.synonyms
UNION ALL
SELECT 'procedure', SCHEMA_NAME(schema_id), name, NULL FROM sys.procedures
ORDER BY 1, 2, 3;


/* ---------------------------------------------------------------------------
   5. FIND THE HISTORICAL LOOKUP ANYWHERE YOU CAN REACH ON THIS SERVER
   Only needed when section 4 shows Lookup_RMS_HistoricalRDS MISSING.
   Searches every database you can enter for a table whose name contains
   'Lookup' or 'Historical'. Also lists linked servers.
   GOOD: one hit, in CRE_Trial_ELT_Repository.dbo.
   ASK : a hit elsewhere: "Lookup_RMS_HistoricalRDS is in <database>; the
         load procedure reads it from CRE_Trial_ELT_Repository. Please load
         it there with the other four tables."
   --------------------------------------------------------------------------- */
DECLARE @db sysname, @sql nvarchar(max);
DECLARE @hits TABLE (database_name sysname, [schema] sysname, [table] sysname, [rows] bigint);
DECLARE dbs CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.databases
    WHERE state_desc = 'ONLINE' AND HAS_DBACCESS(name) = 1
      AND name NOT IN ('master', 'model', 'msdb', 'tempdb');
OPEN dbs; FETCH NEXT FROM dbs INTO @db;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'SELECT ' + QUOTENAME(@db, '''') + N', s.name, t.name, SUM(p.rows)
                 FROM ' + QUOTENAME(@db) + N'.sys.tables t
                 JOIN ' + QUOTENAME(@db) + N'.sys.schemas s ON s.schema_id = t.schema_id
                 JOIN ' + QUOTENAME(@db) + N'.sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
                 WHERE t.name LIKE ''%Lookup%'' OR t.name LIKE ''%Historical%''
                 GROUP BY s.name, t.name';
    BEGIN TRY
        INSERT INTO @hits EXEC sp_executesql @sql;
    END TRY
    BEGIN CATCH
        INSERT INTO @hits VALUES (@db, N'(error)', LEFT(ERROR_MESSAGE(), 128), NULL);
    END CATCH;
    FETCH NEXT FROM dbs INTO @db;
END
CLOSE dbs; DEALLOCATE dbs;
SELECT * FROM @hits ORDER BY database_name, [schema], [table];

SELECT name AS linked_server, data_source, provider, is_linked
FROM sys.servers WHERE is_linked = 1;


/* ---------------------------------------------------------------------------
   6. WHAT YOUR LOGIN MAY DO IN THE REPOSITORY
   GOOD (for the Workbench's own LOSS login, when it exists):
         SELECT on dbo.Client; CONTROL (or SELECT/INSERT/UPDATE/DELETE) on
         schema stage; EXECUTE on stage.usp_load_elt_result. No grant on
         the lookup or on Data/RMSELT/RMS_HistoricalRDS is needed
         (ownership chaining through the procedure, same database).
   GOOD (for the person installing loss_schema.sql): db_owner.
   ASK : whatever is missing from the list above, per login.
   --------------------------------------------------------------------------- */
SELECT permission_name FROM fn_my_permissions(NULL, 'DATABASE') ORDER BY 1;
SELECT dp.class_desc, dp.permission_name, dp.state_desc,
       COALESCE(OBJECT_SCHEMA_NAME(dp.major_id) + '.' + OBJECT_NAME(dp.major_id),
                SCHEMA_NAME(dp.major_id)) AS on_object
FROM sys.database_permissions dp
WHERE dp.grantee_principal_id = DATABASE_PRINCIPAL_ID()
ORDER BY 1, 4, 2;
-- Other principals in this database (who else can reach it; the future
-- Workbench service login should appear here once created)
SELECT name, type_desc, create_date FROM sys.database_principals
WHERE type IN ('S', 'U', 'G') AND name NOT LIKE '##%' ORDER BY name;


/* ---------------------------------------------------------------------------
   7. LOOKUP CONTENT CHECKS — run only where section 5 found the table.
   Replace <DB> with the database that holds it.
   GOOD: peril_len <= 5 and pcs_len <= 5 (targets are varchar(5));
         Peril values are Risk Modeler codes (EQ, WS, TY, ...), not words;
         repeats query returns 0 rows.
   ASK : peril_len or pcs_len > 5: "PCS/Peril values wider than the
         RMS_HistoricalRDS columns — how does the workflow tool load them?"
         (plan O-04). Peril values are display names: tell PremiumIQ (O-11,
         the join needs a code mapping). Repeats > 0: tell PremiumIQ (O-11,
         group exports of those events will be refused).
   --------------------------------------------------------------------------- */
-- SELECT MAX(LEN(Peril)) AS peril_len, MAX(LEN([PCS#])) AS pcs_len,
--        COUNT(*) AS rows_total, COUNT(DISTINCT ModelVersion) AS model_versions
-- FROM <DB>.dbo.Lookup_RMS_HistoricalRDS;
--
-- SELECT ModelVersion, Peril, COUNT(*) AS events
-- FROM <DB>.dbo.Lookup_RMS_HistoricalRDS
-- GROUP BY ModelVersion, Peril ORDER BY ModelVersion, Peril;
--
-- SELECT EventID, ModelVersion, COUNT(*) AS n
-- FROM <DB>.dbo.Lookup_RMS_HistoricalRDS
-- GROUP BY EventID, ModelVersion HAVING COUNT(*) > 1;


/* ---------------------------------------------------------------------------
   8. HOW THE WORKFLOW TOOL WRITES TODAY — run only where section 4 shows rows.
   GOOD: DataInforce looks like 'yyyy-mm-dd'; Data.Perspective holds codes
         (GU/GR/RL/RP); DataModelVersion is decimal ('25.0'); PCS/Peril
         values fit their columns.
   ASK : any other form: "Which format should the Workbench write for
         DataInforce / Perspective?" (plan O-04).
   --------------------------------------------------------------------------- */
-- SELECT TOP 20 DataID, ClientID, TreatyIncept, DataVintage, DataModelVendor,
--        DataModelVersion, DataCurrency, [Server], AnalysisID, Perspective, ArchiveFile, CRMID
-- FROM dbo.Data ORDER BY DataID DESC;
--
-- SELECT TOP 20 DataID, Peril, ModelVersion, TreatyYear, TreatyIncept, DataInforce,
--        EventID, [Type], PCS, Perspective
-- FROM dbo.RMS_HistoricalRDS WHERE DataInforce IS NOT NULL ORDER BY DataID DESC;
--
-- SELECT Perspective, COUNT(*) FROM dbo.Data GROUP BY Perspective;
-- SELECT DataModelVersion, COUNT(*) FROM dbo.Data GROUP BY DataModelVersion;


/* ============================================================================
   9. THE ASKS, BY OUTCOME  (copy the lines that apply into the message to CIC)

   A. Section 3 shows an empty repository (the 2026-09-09 state)
      -> "When will the load of dbo.Client, dbo.Data, dbo.RMSELT,
          dbo.RMS_HistoricalRDS, and dbo.Lookup_RMS_HistoricalRDS into
          CRE_Trial_ELT_Repository be re-run?"
      -> "What will the Workbench application database on this server be
          called (MSSQL_WORKBENCH_DATABASE)?"

   B. Section 4 shows Lookup_RMS_HistoricalRDS MISSING with the other four present
      -> "Please load Lookup_RMS_HistoricalRDS into CRE_Trial_ELT_Repository
          as well; the load procedure reads it from inside that database."

   C. Logins and grants (always)
      -> "Create a SQL login for the Workbench service (MSSQL_LOSS_USER) with,
          in CRE_Trial_ELT_Repository: SELECT ON dbo.Client;
                                       CONTROL ON SCHEMA::stage;
                                       EXECUTE ON stage.usp_load_elt_result
          No grant on Lookup_RMS_HistoricalRDS, Data, RMSELT, or
          RMS_HistoricalRDS is required."
      -> "Who runs db/bootstrap/loss_schema.sql (must be db_owner so the
          stage schema and procedure are owned by dbo)? Does the DBA want to
          review the file before the first install?"

   D. Section 2 shows is_read_committed_snapshot_on = 0 on the repository
      -> No ask. Confirmed 2026-09-09; the Workbench reads the manifest
         with READUNCOMMITTED.

   E. Section 7/8 findings (only once data exists)
      -> Widths > 5, non-code perils, repeated event IDs, unexpected
         DataInforce or Perspective forms: quote the rows and ask how the
         workflow tool handles them.
   ============================================================================ */
