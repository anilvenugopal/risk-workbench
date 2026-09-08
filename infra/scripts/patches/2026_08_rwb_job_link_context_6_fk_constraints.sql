-- CR-04c step 6 of 6: add the FK constraints, last.
-- Run after step 5 has succeeded. Safe to re-run: guarded by IF NOT EXISTS.

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_rwb_job_link_type')
    ALTER TABLE rwb_job ADD CONSTRAINT fk_rwb_job_link_type
        FOREIGN KEY (link_type) REFERENCES rwb_job_link_type_kind (code);

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_rwb_job_context_type')
    ALTER TABLE rwb_job ADD CONSTRAINT fk_rwb_job_context_type
        FOREIGN KEY (context_type) REFERENCES rwb_job_context_type_kind (code);
