-- CR-04c step 3 of 6: seed the 2 new kind tables' rows.
-- Run after step 2 has succeeded. Safe to re-run: MERGE ... WHEN NOT MATCHED.

MERGE rwb_job_link_type_kind AS target
USING (VALUES
    ('edm',            'EDM',            10),
    ('rdm',            'RDM',            20),
    ('not_applicable', 'Not applicable', 900)
) AS src (code, label, sort_order)
ON target.code = src.code
WHEN NOT MATCHED THEN
    INSERT (code, label, sort_order)
    VALUES (src.code, src.label, src.sort_order);

MERGE rwb_job_context_type_kind AS target
USING (VALUES
    ('edm',            'EDM',            10),
    ('rdm',            'RDM',            20),
    ('irp_analysis',   'IRP Analysis',   30),
    ('portfolio',      'Portfolio',      40),
    ('breakout_group', 'Breakout Group', 50),
    ('execution',      'Execution',      60)
) AS src (code, label, sort_order)
ON target.code = src.code
WHEN NOT MATCHED THEN
    INSERT (code, label, sort_order)
    VALUES (src.code, src.label, src.sort_order);
