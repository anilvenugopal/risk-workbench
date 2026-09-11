"""SQLite mirror of the loss repository for the unit tier (spec 014, T-27).

Two attached in-memory databases stand in for SQL Server schemas: ``stage``
holds the Workbench's three tables from db/bootstrap/loss_schema.sql, ``dbo``
holds CIC's five tables from db/bootstrap/loss_dev_mirror.sql. Application SQL
names both by two-part name, so it runs unchanged over this mirror. The load
procedure is not mirrored: the unit tier fakes ``db.execute_procedure`` and the
SQL Server tier runs the real one.
"""

from __future__ import annotations

LOSS_STAGE_SCHEMA = [
    """CREATE TABLE stage.rwb_loss_result_manifest (
        manifest_id INTEGER PRIMARY KEY AUTOINCREMENT,
        export_id TEXT NOT NULL,
        requested_by_email TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        requested_from_submission_id TEXT NOT NULL,
        irp_analysis_id TEXT NOT NULL,
        irp_analysis_irp_id TEXT,
        irp_app_analysis_id INTEGER NOT NULL,
        analysis_name TEXT,
        analysis_description TEXT,
        perspective_code TEXT NOT NULL,
        client_id INTEGER NOT NULL,
        treaty_incept TEXT NOT NULL,
        treaty_year INTEGER,
        crm_id TEXT,
        data_name TEXT,
        data_vintage TEXT,
        data_currency TEXT NOT NULL,
        data_model_vendor TEXT NOT NULL,
        server TEXT,
        irp_export_job_id TEXT,
        loss_table_type TEXT CHECK (loss_table_type IN ('ELT', 'PLT')),
        engine_type TEXT CHECK (engine_type IN ('DLM', 'HD', 'GROUP')),
        data_model_version TEXT,
        peril_code TEXT,
        region_code TEXT,
        zip_file TEXT,
        stage_status TEXT NOT NULL CHECK (stage_status IN ('pending', 'failed', 'staged')),
        staged_at TEXT,
        load_status TEXT NOT NULL
            CHECK (load_status IN ('pending', 'loading', 'loaded', 'failed')),
        loaded_at TEXT,
        error_message TEXT,
        data_id INTEGER,
        staged_row_count INTEGER,
        stochastic_row_count INTEGER,
        historical_row_count INTEGER,
        exp_value_raised_count INTEGER,
        std_dev_zeroed_count INTEGER,
        inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (irp_app_analysis_id, perspective_code),
        UNIQUE (export_id, irp_analysis_id)
    )""",
    """CREATE TABLE stage.rwb_loss_result_file (
        result_file_id INTEGER PRIMARY KEY AUTOINCREMENT,
        manifest_id INTEGER NOT NULL REFERENCES rwb_loss_result_manifest (manifest_id),
        result_file TEXT NOT NULL,
        output_level TEXT,
        perspective_code TEXT,
        chunk_index INTEGER,
        row_count INTEGER,
        staged_at TEXT,
        UNIQUE (manifest_id, result_file)
    )""",
    """CREATE TABLE stage.rwb_loss_result_elt_data (
        manifest_id INTEGER NOT NULL REFERENCES rwb_loss_result_manifest (manifest_id),
        result_file_id INTEGER NOT NULL REFERENCES rwb_loss_result_file (result_file_id),
        port_info_id INTEGER,
        port_info_name TEXT,
        port_info_num TEXT,
        event_id INTEGER NOT NULL,
        rate REAL,
        loss REAL,
        std_dev_i REAL,
        std_dev_c REAL,
        exp_value REAL,
        event_type TEXT CHECK (event_type IN ('stochastic', 'historical')),
        exp_value_raised INTEGER NOT NULL DEFAULT 0,
        std_dev_zeroed INTEGER NOT NULL DEFAULT 0,
        inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
]

LOSS_DBO_SCHEMA = [
    """CREATE TABLE dbo.Client (
        ClientID INTEGER PRIMARY KEY, ClientName TEXT, ActiveFlag TEXT
    )""",
    """CREATE TABLE dbo.Data (
        DataID INTEGER PRIMARY KEY AUTOINCREMENT, ClientID INTEGER, TreatyIncept TEXT,
        DataVintage TEXT, DataName TEXT, DataModelVendor TEXT, DataModelVersion TEXT,
        DataCurrency TEXT, Server TEXT, [Database] TEXT, AnalysisID INTEGER, Name TEXT,
        Description TEXT, Perspective TEXT, ArchiveFile TEXT, AReLossSet TEXT, LOB TEXT,
        Geography TEXT, CRMID TEXT
    )""",
    """CREATE TABLE dbo.RMSELT (
        DataID INTEGER, EventID INTEGER, Loss REAL, StdDevI REAL, StdDevC REAL, ExpValue REAL
    )""",
    """CREATE TABLE dbo.RMS_HistoricalRDS (
        DataID INTEGER, ClientID INTEGER, Peril TEXT, ModelVersion TEXT, TreatyYear TEXT,
        TreatyIncept TEXT, DataInforce TEXT, EventID INTEGER, Type TEXT, Event_Name TEXT,
        Loss REAL, PCS TEXT, Perspective TEXT, AReLossSet TEXT
    )""",
    """CREATE TABLE dbo.Lookup_RMS_HistoricalRDS (
        EventID INTEGER, CatYear INTEGER, Peril TEXT, Type TEXT, Name TEXT, [PCS#] TEXT,
        ModelVersion TEXT
    )""",
]
