"""Analysis templates reference model profile, output profile and event rate
scheme by Risk Modeler id (issue 68).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29

Event rate scheme names repeat across peril/region in Risk Modeler, so the
name columns stay as display labels and the new id columns carry the
reference.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_template",
        sa.Column("model_profile_irp_id", sa.Integer, nullable=False),
    )
    op.add_column(
        "analysis_template",
        sa.Column("output_profile_irp_id", sa.Integer, nullable=False),
    )
    op.add_column(
        "analysis_template",
        sa.Column("event_rate_scheme_irp_id", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_template", "event_rate_scheme_irp_id")
    op.drop_column("analysis_template", "output_profile_irp_id")
    op.drop_column("analysis_template", "model_profile_irp_id")
