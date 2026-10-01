"""irp_job stores Risk Modeler's progress from the latest status check
(issue 132).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("irp_job", sa.Column("progress", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("irp_job", "progress")
