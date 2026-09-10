-- The Workbench's stage schema in CIC's loss repository (CRE_Trial_ELT_Repository;
-- dev mirror rwb_loss). Installed by a db_owner login; the Workbench never runs DDL
-- against CIC. Re-running the file is safe: it creates the tables only when they
-- are absent and replaces the procedure. A later release that changes a table
-- ships its own ALTER TABLE block. Names no database, so the same file installs in
-- dev and at CIC.
-- Contract: specs/014-results-export/contracts/load-procedure.md.
-- Schema:   specs/014-results-export/data-model.md §4.

IF SCHEMA_ID('stage') IS NULL EXEC('CREATE SCHEMA stage AUTHORIZATION dbo');
GO

IF OBJECT_ID('stage.rwb_loss_result_manifest') IS NULL
CREATE TABLE stage.rwb_loss_result_manifest (
    manifest_id                  INT IDENTITY(1,1) NOT NULL
        CONSTRAINT pk_rwb_loss_result_manifest PRIMARY KEY,
    export_id                    UNIQUEIDENTIFIER NOT NULL,
    requested_by_email           NVARCHAR(255)    NOT NULL,
    requested_at                 DATETIME2        NOT NULL,
    requested_from_submission_id UNIQUEIDENTIFIER NOT NULL,
    irp_analysis_id              UNIQUEIDENTIFIER NOT NULL,
    irp_analysis_irp_id          NVARCHAR(64)     NULL,
    irp_app_analysis_id          INT              NOT NULL,
    analysis_name                NVARCHAR(256)    NULL,
    analysis_description         NVARCHAR(512)    NULL,
    perspective_code             VARCHAR(5)       NOT NULL,
    client_id                    INT              NOT NULL,
    treaty_incept                DATE             NOT NULL,
    treaty_year                  INT              NULL,
    crm_id                       VARCHAR(30)      NULL,
    data_name                    NVARCHAR(150)    NULL,
    data_vintage                 DATE             NULL,
    data_currency                NVARCHAR(5)      NOT NULL,
    data_model_vendor            NVARCHAR(10)     NOT NULL,
    [server]                     VARCHAR(255)     NULL,
    irp_export_job_id            NVARCHAR(64)     NULL,
    loss_table_type              VARCHAR(3)       NULL
        CONSTRAINT ck_rwb_loss_result_manifest_loss_table_type
        CHECK (loss_table_type IN ('ELT', 'PLT')),
    engine_type                  VARCHAR(3)       NULL
        CONSTRAINT ck_rwb_loss_result_manifest_engine_type
        CHECK (engine_type IN ('DLM', 'HD')),
    data_model_version           NVARCHAR(10)     NULL,
    peril_code                   NVARCHAR(10)     NULL,
    region_code                  NVARCHAR(10)     NULL,
    zip_file                     NVARCHAR(1024)   NULL,
    stage_status                 VARCHAR(10)      NOT NULL
        CONSTRAINT ck_rwb_loss_result_manifest_stage_status
        CHECK (stage_status IN ('pending', 'failed', 'staged')),
    staged_at                    DATETIME2        NULL,
    load_status                  VARCHAR(10)      NOT NULL
        CONSTRAINT ck_rwb_loss_result_manifest_load_status
        CHECK (load_status IN ('pending', 'loading', 'loaded', 'failed')),
    loaded_at                    DATETIME2        NULL,
    error_message                NVARCHAR(MAX)    NULL,
    data_id                      INT              NULL,
    staged_row_count             INT              NULL,
    stochastic_row_count         INT              NULL,
    historical_row_count         INT              NULL,
    exp_value_raised_count       INT              NULL,
    std_dev_zeroed_count         INT              NULL,
    inserted_at                  DATETIME2        NOT NULL
        CONSTRAINT df_rwb_loss_result_manifest_inserted_at DEFAULT SYSUTCDATETIME(),
    updated_at                   DATETIME2        NOT NULL
        CONSTRAINT df_rwb_loss_result_manifest_updated_at DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_rwb_loss_result_manifest_analysis_perspective
        UNIQUE (irp_app_analysis_id, perspective_code),
    CONSTRAINT uq_rwb_loss_result_manifest_export_analysis
        UNIQUE (export_id, irp_analysis_id)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'ix_rwb_loss_result_manifest_export_id'
                 AND object_id = OBJECT_ID('stage.rwb_loss_result_manifest'))
    CREATE INDEX ix_rwb_loss_result_manifest_export_id
        ON stage.rwb_loss_result_manifest (export_id);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'ix_rwb_loss_result_manifest_requested_from_submission_id'
                 AND object_id = OBJECT_ID('stage.rwb_loss_result_manifest'))
    CREATE INDEX ix_rwb_loss_result_manifest_requested_from_submission_id
        ON stage.rwb_loss_result_manifest (requested_from_submission_id);
GO

IF OBJECT_ID('stage.rwb_loss_result_file') IS NULL
CREATE TABLE stage.rwb_loss_result_file (
    result_file_id   INT IDENTITY(1,1) NOT NULL
        CONSTRAINT pk_rwb_loss_result_file PRIMARY KEY,
    manifest_id      INT            NOT NULL
        CONSTRAINT fk_rwb_loss_result_file_manifest
        REFERENCES stage.rwb_loss_result_manifest (manifest_id),
    result_file      NVARCHAR(1024) NOT NULL,
    output_level     VARCHAR(20)    NULL,
    perspective_code VARCHAR(5)     NULL,
    chunk_index      INT            NULL,
    row_count        INT            NULL,
    staged_at        DATETIME2      NULL,
    CONSTRAINT uq_rwb_loss_result_file_manifest_file UNIQUE (manifest_id, result_file)
);
GO

IF OBJECT_ID('stage.rwb_loss_result_elt_data') IS NULL
CREATE TABLE stage.rwb_loss_result_elt_data (
    manifest_id      INT           NOT NULL
        CONSTRAINT fk_rwb_loss_result_elt_data_manifest
        REFERENCES stage.rwb_loss_result_manifest (manifest_id),
    result_file_id   INT           NOT NULL
        CONSTRAINT fk_rwb_loss_result_elt_data_file
        REFERENCES stage.rwb_loss_result_file (result_file_id),
    port_info_id     INT           NULL,
    port_info_name   NVARCHAR(256) NULL,
    port_info_num    NVARCHAR(64)  NULL,
    event_id         INT           NOT NULL,
    rate             FLOAT         NULL,
    loss             FLOAT         NULL,
    std_dev_i        FLOAT         NULL,
    std_dev_c        FLOAT         NULL,
    exp_value        FLOAT         NULL,
    event_type       VARCHAR(10)   NULL
        CONSTRAINT ck_rwb_loss_result_elt_data_event_type
        CHECK (event_type IN ('stochastic', 'historical')),
    exp_value_raised BIT           NOT NULL
        CONSTRAINT df_rwb_loss_result_elt_data_exp_value_raised DEFAULT 0,
    std_dev_zeroed   BIT           NOT NULL
        CONSTRAINT df_rwb_loss_result_elt_data_std_dev_zeroed DEFAULT 0,
    inserted_at      DATETIME2     NOT NULL
        CONSTRAINT df_rwb_loss_result_elt_data_inserted_at DEFAULT SYSUTCDATETIME(),
    INDEX cix_rwb_loss_result_elt_data CLUSTERED (manifest_id, event_id)
);
GO

-- Classify, correct, and load one staged analysis into dbo.Data, dbo.RMSELT, and
-- dbo.RMS_HistoricalRDS. Callable without the Workbench:
--     EXEC stage.usp_load_elt_result @manifest_id = <id>;
-- Errors: 50000 called inside a transaction; 50001 the manifest row cannot be
-- claimed (the message says why); 50002 the lookup has no rows for the model
-- version; 50003 an event matches more than one lookup row. Every error leaves
-- zero target rows and load_status = 'failed' with the message.
CREATE OR ALTER PROCEDURE stage.usp_load_elt_result
    @manifest_id INT
AS
BEGIN
    SET NOCOUNT ON;

    -- Before XACT_ABORT: THROW honours it and would roll back the caller's own
    -- transaction along with whatever else it holds.
    IF @@TRANCOUNT > 0
        THROW 50000, 'usp_load_elt_result must be called outside a transaction', 1;

    SET XACT_ABORT ON;
    DECLARE @msg NVARCHAR(2048);
    DECLARE @claimed BIT = 0;

    BEGIN TRY
        BEGIN TRANSACTION;

        -- The claim is the duplicate guard: only a staged, not-yet-loaded row
        -- can be loaded, and a concurrent second call sees zero rows here.
        UPDATE stage.rwb_loss_result_manifest
        SET load_status = 'loading', error_message = NULL, updated_at = SYSUTCDATETIME()
        WHERE manifest_id = @manifest_id
          AND stage_status = 'staged'
          AND load_status IN ('pending', 'failed');
        IF @@ROWCOUNT = 1 SET @claimed = 1;

        IF @claimed = 0
        BEGIN
            DECLARE @stage_status VARCHAR(10), @load_status VARCHAR(10), @loaded_data_id INT;
            SELECT @stage_status = stage_status, @load_status = load_status,
                   @loaded_data_id = data_id
            FROM stage.rwb_loss_result_manifest
            WHERE manifest_id = @manifest_id;

            IF @stage_status IS NULL
                SET @msg = CONCAT('manifest ', @manifest_id, ' not found');
            ELSE IF @load_status = 'loaded'
                SET @msg = CONCAT('manifest ', @manifest_id, ' already loaded as data ID ',
                                  @loaded_data_id);
            ELSE IF @load_status = 'loading'
                SET @msg = CONCAT('manifest ', @manifest_id, ' is loading');
            ELSE
                SET @msg = CONCAT('manifest ', @manifest_id, ' is not staged (stage_status ',
                                  @stage_status, ')');
            THROW 50001, @msg, 1;
        END;

        DECLARE @model_version NVARCHAR(10);
        SELECT @model_version = data_model_version
        FROM stage.rwb_loss_result_manifest
        WHERE manifest_id = @manifest_id;

        IF NOT EXISTS (SELECT 1 FROM dbo.Lookup_RMS_HistoricalRDS
                       WHERE ModelVersion = @model_version)
        BEGIN
            SET @msg = CONCAT('Lookup_RMS_HistoricalRDS has no rows for model version ',
                              ISNULL(@model_version, '(null)'));
            THROW 50002, @msg, 1;
        END;

        DECLARE @dup_event INT, @dup_count INT;
        SELECT TOP (1) @dup_event = e.event_id, @dup_count = COUNT(*)
        FROM (SELECT DISTINCT event_id FROM stage.rwb_loss_result_elt_data
              WHERE manifest_id = @manifest_id) AS e
        JOIN dbo.Lookup_RMS_HistoricalRDS l
          ON l.EventID = e.event_id AND l.ModelVersion = @model_version
        GROUP BY e.event_id
        HAVING COUNT(*) > 1
        ORDER BY e.event_id;

        IF @dup_event IS NOT NULL
        BEGIN
            SET @msg = CONCAT('event ', @dup_event, ' matches ', @dup_count,
                              ' historical lookup rows for model version ', @model_version);
            THROW 50003, @msg, 1;
        END;

        UPDATE d
        SET event_type = CASE WHEN l.EventID IS NULL THEN 'stochastic' ELSE 'historical' END
        FROM stage.rwb_loss_result_elt_data d
        LEFT JOIN dbo.Lookup_RMS_HistoricalRDS l
          ON l.EventID = d.event_id AND l.ModelVersion = @model_version
        WHERE d.manifest_id = @manifest_id;

        UPDATE stage.rwb_loss_result_elt_data
        SET exp_value = loss, exp_value_raised = 1
        WHERE manifest_id = @manifest_id AND loss > exp_value;
        DECLARE @exp_value_raised INT = @@ROWCOUNT;

        UPDATE stage.rwb_loss_result_elt_data
        SET std_dev_i = CASE WHEN std_dev_i < 0 THEN 0 ELSE std_dev_i END,
            std_dev_c = CASE WHEN std_dev_c < 0 THEN 0 ELSE std_dev_c END,
            std_dev_zeroed = 1
        WHERE manifest_id = @manifest_id
          AND event_type = 'stochastic'
          AND (std_dev_i < 0 OR std_dev_c < 0);
        DECLARE @std_dev_zeroed INT = @@ROWCOUNT;

        DECLARE @inserted TABLE (data_id INT);
        INSERT INTO dbo.Data (ClientID, TreatyIncept, DataVintage, DataName, DataModelVendor,
                              DataModelVersion, DataCurrency, [Server], AnalysisID, [Name],
                              [Description], Perspective, CRMID)
        OUTPUT INSERTED.DataID INTO @inserted (data_id)
        SELECT client_id, treaty_incept, data_vintage, data_name, data_model_vendor,
               data_model_version, data_currency, [server], irp_app_analysis_id,
               analysis_name, analysis_description, perspective_code, crm_id
        FROM stage.rwb_loss_result_manifest
        WHERE manifest_id = @manifest_id;

        DECLARE @data_id INT = (SELECT TOP (1) data_id FROM @inserted);

        INSERT INTO dbo.RMSELT (DataID, EventID, Loss, StdDevI, StdDevC, ExpValue)
        SELECT @data_id, event_id, loss, std_dev_i, std_dev_c, exp_value
        FROM stage.rwb_loss_result_elt_data
        WHERE manifest_id = @manifest_id AND event_type = 'stochastic';
        DECLARE @stochastic INT = @@ROWCOUNT;

        -- Peril and PCS are copied without truncation: a lookup value wider than
        -- the target column fails the load rather than loading a clipped value.
        INSERT INTO dbo.RMS_HistoricalRDS (DataID, ClientID, Peril, ModelVersion, TreatyYear,
                                           TreatyIncept, DataInforce, EventID, [Type],
                                           Event_Name, Loss, PCS, Perspective)
        SELECT @data_id, m.client_id, l.Peril, l.ModelVersion,
               CONVERT(VARCHAR(4), m.treaty_year), m.treaty_incept,
               CONVERT(VARCHAR(15), m.data_vintage, 23), d.event_id, l.[Type],
               l.[Name], d.loss, l.[PCS#], m.perspective_code
        FROM stage.rwb_loss_result_elt_data d
        JOIN stage.rwb_loss_result_manifest m ON m.manifest_id = d.manifest_id
        JOIN dbo.Lookup_RMS_HistoricalRDS l
          ON l.EventID = d.event_id AND l.ModelVersion = m.data_model_version
        WHERE d.manifest_id = @manifest_id AND d.event_type = 'historical';
        DECLARE @historical INT = @@ROWCOUNT;

        UPDATE stage.rwb_loss_result_manifest
        SET load_status = 'loaded',
            loaded_at = SYSUTCDATETIME(),
            updated_at = SYSUTCDATETIME(),
            data_id = @data_id,
            stochastic_row_count = @stochastic,
            historical_row_count = @historical,
            exp_value_raised_count = @exp_value_raised,
            std_dev_zeroed_count = @std_dev_zeroed
        WHERE manifest_id = @manifest_id;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;

        -- Only the row this call claimed: a 50001 on a row that is still staging
        -- or already loaded leaves that row's status alone. The rollback has
        -- already undone the 'loading' claim, so the row itself cannot say so.
        DECLARE @error NVARCHAR(4000) = ERROR_MESSAGE();
        IF @claimed = 1
            UPDATE stage.rwb_loss_result_manifest
            SET load_status = 'failed', error_message = @error, updated_at = SYSUTCDATETIME()
            WHERE manifest_id = @manifest_id;

        THROW;
    END CATCH;
END;
GO
