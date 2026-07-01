"""Initial schema — WORKBENCH database.

Revision ID: 0001
Revises: (none)
Create Date: 2026-07-01

Dev strategy: drop-create-seed. This is the ONLY revision until production
cutover. Run `make db-rebuild` to drop and recreate from scratch rather than
accumulating incremental migrations.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TODO (Iteration 0): create all WORKBENCH tables here per DATA_MODEL.md.
    # Each iteration that touches the schema will amend this single revision
    # (after running `make db-rebuild`) until production cutover.
    pass


def downgrade() -> None:
    pass
