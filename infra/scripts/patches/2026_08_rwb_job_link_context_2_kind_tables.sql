-- CR-04c step 2 of 6: create the 2 new kind tables.
-- Run after step 1 has succeeded. Safe to re-run: guarded by IF OBJECT_ID.

IF OBJECT_ID('rwb_job_link_type_kind') IS NULL
BEGIN
    CREATE TABLE rwb_job_link_type_kind (
        code NVARCHAR(50) NOT NULL PRIMARY KEY,
        label NVARCHAR(255) NOT NULL,
        sort_order INT NOT NULL,
        inserted_at DATETIME2 NOT NULL DEFAULT (GETUTCDATE())
    );
END

IF OBJECT_ID('rwb_job_context_type_kind') IS NULL
BEGIN
    CREATE TABLE rwb_job_context_type_kind (
        code NVARCHAR(50) NOT NULL PRIMARY KEY,
        label NVARCHAR(255) NOT NULL,
        sort_order INT NOT NULL,
        inserted_at DATETIME2 NOT NULL DEFAULT (GETUTCDATE())
    );
END
