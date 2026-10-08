"""Add the refresh_portfolios rwb_job type (spec 207).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07
"""

from __future__ import annotations

from alembic import op

revision: str = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO rwb_job_type_kind (code, label, sort_order) "
        "VALUES ('refresh_portfolios', 'Refresh portfolios', 26)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM rwb_job_type_kind WHERE code = 'refresh_portfolios'")
