-- Change 1: stage.rwb_loss_result_manifest.engine_type VARCHAR(4) -> VARCHAR(5),
-- and its CHECK constraint accepts 'GROUP' (a group's archive names that engine
-- type). Re-runnable over a repository at any version; stamps version 1.
-- Run db/bootstrap/loss_schema.sql first (contracts/load-procedure.md §4).

IF EXISTS (SELECT 1 FROM sys.check_constraints
           WHERE name = 'ck_rwb_loss_result_manifest_engine_type'
             AND parent_object_id = OBJECT_ID('stage.rwb_loss_result_manifest'))
    ALTER TABLE stage.rwb_loss_result_manifest
        DROP CONSTRAINT ck_rwb_loss_result_manifest_engine_type;
GO

IF COL_LENGTH('stage.rwb_loss_result_manifest', 'engine_type') <> 5
    ALTER TABLE stage.rwb_loss_result_manifest ALTER COLUMN engine_type VARCHAR(5) NULL;
GO

ALTER TABLE stage.rwb_loss_result_manifest
    ADD CONSTRAINT ck_rwb_loss_result_manifest_engine_type
    CHECK (engine_type IN ('DLM', 'HD', 'GROUP'));
GO

IF NOT EXISTS (SELECT 1 FROM stage.rwb_loss_schema_version WHERE version = 1)
    INSERT INTO stage.rwb_loss_schema_version (version, description)
    VALUES (1, 'engine_type VARCHAR(5) with GROUP');
GO
