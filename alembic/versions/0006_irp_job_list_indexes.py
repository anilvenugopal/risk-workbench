"""Index irp_job for the filtered, paged IRP jobs list.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02

/workflows/irp-jobs opens on the current analyst's jobs, newest first, and
filters on Submitted from and Completed by.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_irp_job_inserted_by_submitted_at", "irp_job",
                    ["inserted_by", "submitted_at"])
    op.create_index("ix_irp_job_submitted_at", "irp_job", ["submitted_at"])
    op.create_index("ix_irp_job_completed_at", "irp_job", ["completed_at"])


def downgrade() -> None:
    op.drop_index("ix_irp_job_completed_at", table_name="irp_job")
    op.drop_index("ix_irp_job_submitted_at", table_name="irp_job")
    op.drop_index("ix_irp_job_inserted_by_submitted_at", table_name="irp_job")
