-- Development mirror of CIC's five loss repository tables (specs/014-results-export/
-- cic-reference/). Applied to rwb_loss by infra/scripts/bootstrap_loss.py; never
-- run at CIC, where these tables already exist. Idempotent; names no database.

IF OBJECT_ID('dbo.Client') IS NULL
CREATE TABLE dbo.Client (
    ClientID   INT           NOT NULL,
    ClientName NVARCHAR(150) NULL,
    ActiveFlag VARCHAR(1)    NULL,
    CONSTRAINT PK_Client_new PRIMARY KEY CLUSTERED (ClientID ASC)
);
GO

IF OBJECT_ID('dbo.Data') IS NULL
CREATE TABLE dbo.Data (
    DataID           INT IDENTITY(1,1) NOT NULL,
    ClientID         INT           NULL,
    TreatyIncept     DATE          NULL,
    DataVintage      DATE          NULL,
    DataName         NVARCHAR(150) NULL,
    DataModelVendor  NVARCHAR(10)  NULL,
    DataModelVersion NVARCHAR(10)  NULL,
    DataCurrency     NVARCHAR(5)   NULL,
    [Server]         VARCHAR(MAX)  NULL,
    [Database]       VARCHAR(MAX)  NULL,
    AnalysisID       INT           NULL,
    [Name]           VARCHAR(MAX)  NULL,
    [Description]    VARCHAR(MAX)  NULL,
    Perspective      VARCHAR(50)   NULL,
    ArchiveFile      VARCHAR(100)  NULL,
    AReLossSet       NVARCHAR(36)  NULL,
    LOB              NVARCHAR(500) NULL,
    Geography        NVARCHAR(500) NULL,
    CRMID            VARCHAR(30)   NULL
);
GO

IF OBJECT_ID('dbo.RMSELT') IS NULL
CREATE TABLE dbo.RMSELT (
    DataID   INT   NULL,
    EventID  INT   NULL,
    Loss     FLOAT NULL,
    StdDevI  FLOAT NULL,
    StdDevC  FLOAT NULL,
    ExpValue FLOAT NULL
);
GO

IF OBJECT_ID('dbo.RMS_HistoricalRDS') IS NULL
CREATE TABLE dbo.RMS_HistoricalRDS (
    DataID       INT          NULL,
    ClientID     INT          NULL,
    Peril        VARCHAR(5)   NULL,
    ModelVersion VARCHAR(10)  NULL,
    TreatyYear   VARCHAR(4)   NULL,
    TreatyIncept DATETIME     NULL,
    DataInforce  VARCHAR(15)  NULL,
    EventID      INT          NULL,
    [Type]       VARCHAR(5)   NULL,
    Event_Name   VARCHAR(MAX) NULL,
    Loss         FLOAT        NULL,
    PCS          VARCHAR(5)   NULL,
    Perspective  VARCHAR(5)   NULL,
    AReLossSet   NVARCHAR(36) NULL
);
GO

IF OBJECT_ID('dbo.Lookup_RMS_HistoricalRDS') IS NULL
CREATE TABLE dbo.Lookup_RMS_HistoricalRDS (
    EventID      INT           NULL,
    CatYear      INT           NULL,
    Peril        NVARCHAR(10)  NULL,
    [Type]       NVARCHAR(10)  NULL,
    [Name]       NVARCHAR(MAX) NULL,
    [PCS#]       NVARCHAR(225) NULL,
    ModelVersion NVARCHAR(10)  NULL
);
GO
