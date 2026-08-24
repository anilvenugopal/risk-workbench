"""Seed the WORKBENCH database after Alembic migrations.

Idempotent: safe to run multiple times. Uses MERGE / IF NOT EXISTS patterns.

Inserts:
  - role_kind rows (analyst, admin) — these are also seeded in the migration,
    but this script handles the case where the migration already ran them.
  - One dev fixture admin user: admin@example.com / password: Admin1234567!
    (bcrypt cost 12, must_change_password=False, role=admin)
    Only inserted in development (APP_ENV=development).

Run via Makefile (preferred):
    make wsl-db-rebuild     # WSL2 native
    make db-rebuild         # Docker
"""

from __future__ import annotations

import os
import sys

import bcrypt
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def _workbench_engine() -> Engine:
    server = os.environ["MSSQL_WORKBENCH_SERVER"]
    port = os.environ.get("MSSQL_WORKBENCH_PORT", "1433")
    user = os.environ.get("MSSQL_WORKBENCH_USER", "sa")
    password = os.environ["MSSQL_WORKBENCH_PASSWORD"]
    database = os.environ.get("MSSQL_WORKBENCH_DATABASE", "rwb_workbench")
    driver = os.environ.get("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    trust = os.environ.get("MSSQL_TRUST_CERT", "yes")

    import urllib.parse
    odbc = (
        f"DRIVER={{{driver}}};SERVER={server},{port};DATABASE={database};"
        f"UID={user};PWD={password};TrustServerCertificate={trust};"
    )
    url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
    return create_engine(url)


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def main() -> int:
    print("Seed: connecting to rwb_workbench...")
    engine = _workbench_engine()
    try:
        with engine.begin() as conn:
            # role_kind seeds (idempotent via MERGE)
            conn.execute(text("""
                MERGE role_kind AS target
                USING (VALUES
                    ('analyst', 'Analyst', 10, 0),
                    ('admin',   'Administrator', 20, 1)
                ) AS src (code, label, sort_order, is_admin)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order, is_admin)
                    VALUES (src.code, src.label, src.sort_order, src.is_admin);
            """))
            print("  [role_kind] seeds OK")

            # submission_status_kind seeds (idempotent via MERGE) — FR-010
            conn.execute(text("""
                MERGE submission_status_kind AS target
                USING (VALUES
                    ('ACTIVE',    'Active',    10),
                    ('COMPLETED', 'Completed', 20),
                    ('CANCELLED', 'Cancelled', 30)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            print("  [submission_status_kind] seeds OK")

            # treaty_type_kind seeds (idempotent via MERGE) — FR-030 (provisional)
            conn.execute(text("""
                MERGE treaty_type_kind AS target
                USING (VALUES
                    ('cat_xol',       'Cat XoL',      10),
                    ('quota_share',   'Quota Share',  20),
                    ('surplus',       'Surplus',      30),
                    ('per_risk_xol',  'Per-Risk XoL', 40),
                    ('aggregate_xol', 'Aggregate XoL', 50),
                    ('stop_loss',     'Stop Loss',    60)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            print("  [treaty_type_kind] seeds OK")

            # ── Iteration-2 kind seeds (idempotent MERGE) — data-model §13 ──────
            # irp_job_type_kind
            conn.execute(text("""
                MERGE irp_job_type_kind AS target
                USING (VALUES
                    ('import_edm', 'Import EDM', 10),
                    ('import_rdm', 'Import RDM', 20),
                    ('geohaz',     'Geohazard', 40),
                    ('analysis',   'Analysis',  50),
                    ('grouping',   'Grouping',  60),
                    ('export',     'Export',    70)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            conn.execute(text("""
                MERGE irp_job_resource_type_kind AS target
                USING (VALUES
                    ('portfolio', 'Portfolio', 10)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            conn.execute(text("""
                MERGE rwb_job_type_kind AS target
                USING (VALUES
                    ('upload_edm',                'Upload EDM',                10),
                    ('upload_rdm',                'Upload RDM',                20),
                    ('backfill_rdm_analyses',     'Backfill RDM Analyses',     25),
                    ('backfill_edm_detail',       'Backfill EDM Detail',       27),
                    ('run_geohaz',                'Run GeoHaz',                28),
                    ('execute_analysis_batch',    'Execute Analysis Batch',    28),
                    ('backfill_analysis_detail',  'Backfill Analysis Detail',  29),
                    ('retrieve_analysis_results', 'Retrieve Analysis Results', 30),
                    ('download_export_file',      'Download Export File',      40),
                    ('push_results_to_loss_repo', 'Push Results to Loss Repo', 50),
                    ('notify_analyst',            'Notify Analyst',            60),
                    ('run_breakout_lob',   'Portfolio breakout by line of business', 90),
                    ('run_breakout_state', 'Portfolio breakout by geography (state)', 100),
                    ('run_breakout_country', 'Portfolio breakout by country', 105),
                    ('run_breakout_peril', 'Portfolio breakout by peril', 107),
                    ('run_breakout_custom', 'Portfolio breakout by custom group', 110),
                    ('sync_irp_metadata',         'Sync IRP metadata',         90)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            conn.execute(text("""
                MERGE rwb_job_requestor_type_kind AS target
                USING (VALUES
                    ('irp_job',         'IRP Job',          10),
                    ('analyst_request', 'Analyst Request',  20),
                    ('rwb_job',         'RWB Job',          30),
                    ('breakout_group',  'Breakout Group',   40)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            conn.execute(text("""
                MERGE rwb_job_status_kind AS target
                USING (VALUES
                    ('pending',   'Pending',   10),
                    ('running',   'Running',   20),
                    ('succeeded', 'Succeeded', 30),
                    ('failed',    'Failed',    40)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            # irp_analysis_status_kind — captured-analysis lifecycle (D2).
            conn.execute(text("""
                MERGE irp_analysis_status_kind AS target
                USING (VALUES
                    ('pending', 'Pending', 10),
                    ('running', 'Running', 20),
                    ('ready',   'Ready',   30),
                    ('error',   'Error',   40)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            # breakout_dimension_kind — the three quick-mode breakout
            # dimensions (spec 005 data-model §2) plus peril, grouping-only
            # (P-19), plus custom — the grouping lineage code (T-12).
            conn.execute(text("""
                MERGE breakout_dimension_kind AS target
                USING (VALUES
                    ('lob',     'Line of business',  10),
                    ('state',   'Geography - State', 20),
                    ('country', 'Geography - Country', 25),
                    ('peril',   'Peril',             30),
                    ('custom',  'Custom group',      40)
                ) AS src (code, label, sort_order)
                ON target.code = src.code
                WHEN NOT MATCHED THEN
                    INSERT (code, label, sort_order)
                    VALUES (src.code, src.label, src.sort_order);
            """))
            print("  [irp_job/rwb_job/breakout kind tables] seeds OK")

            app_env = os.environ.get("APP_ENV", "development")
            if app_env == "development":
                # Dev fixture: admin@example.com — admin role, no forced change
                existing = conn.execute(
                    text("SELECT id FROM app_user WHERE email = 'admin@example.com'")
                ).fetchone()
                if existing is None:
                    pw_hash = _hash("Admin1234567!")
                    conn.execute(text("""
                        INSERT INTO app_user
                            (email, display_name, password_hash, must_change_password, is_active)
                        VALUES
                            ('admin@example.com', 'Dev Admin', :pw, 0, 1)
                    """), {"pw": pw_hash})
                    user_id = conn.execute(
                        text("SELECT id FROM app_user WHERE email = 'admin@example.com'")
                    ).scalar()
                    conn.execute(text("""
                        INSERT INTO user_role (user_id, role_code)
                        VALUES (:uid, 'admin')
                    """), {"uid": user_id})
                    print("  [app_user] dev fixture admin@example.com created")
                else:
                    print("  [app_user] dev fixture already exists — skipped")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print("Seed complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
