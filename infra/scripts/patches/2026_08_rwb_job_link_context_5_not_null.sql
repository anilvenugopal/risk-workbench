-- CR-04c step 5 of 6: tighten link_type to NOT NULL.
-- Run only after step 4's verify query (in that file) shows zero rows with
-- link_type IS NULL. Safe to re-run: guarded, and throws a clear error
-- instead of silently succeeding if a NULL row still exists.

IF EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('rwb_job') AND name = 'link_type' AND is_nullable = 1
)
BEGIN
    IF EXISTS (SELECT 1 FROM rwb_job WHERE link_type IS NULL)
        THROW 50000, 'rwb_job.link_type has NULL rows — resolve them (see step 4''s verify query) before tightening to NOT NULL.', 1;
    ALTER TABLE rwb_job ALTER COLUMN link_type NVARCHAR(50) NOT NULL;
END
