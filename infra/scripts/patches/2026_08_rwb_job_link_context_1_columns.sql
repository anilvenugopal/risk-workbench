-- CR-04c step 1 of 6: add the 4 new nullable columns to rwb_job.
-- Run this file alone, confirm it succeeds, then run step 2.
-- Safe to re-run: every ALTER is guarded.

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('rwb_job') AND name = 'link_type')
    ALTER TABLE rwb_job ADD link_type NVARCHAR(50) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('rwb_job') AND name = 'link_id')
    ALTER TABLE rwb_job ADD link_id UNIQUEIDENTIFIER NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('rwb_job') AND name = 'context_type')
    ALTER TABLE rwb_job ADD context_type NVARCHAR(50) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('rwb_job') AND name = 'context_id')
    ALTER TABLE rwb_job ADD context_id UNIQUEIDENTIFIER NULL;
