"""Analysts archive submissions to hide them from the Submissions list (issue 206).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.mssql import DATETIME2

from alembic import op

revision: str = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("submission", sa.Column("archived_at", DATETIME2, nullable=True))
    op.add_column("submission", sa.Column("archived_by", sa.Uuid, nullable=True))
    op.create_foreign_key("fk_submission_archived_by", "submission", "app_user",
                          ["archived_by"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_submission_archived_by", "submission", type_="foreignkey")
    op.drop_column("submission", "archived_by")
    op.drop_column("submission", "archived_at")
