"""Seed the cedant list from db/bootstrap/seed/cedants.xlsx (issue 129).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01

The file is the client's cedant list. Names already in cedant (case ignored)
are skipped, so the cedants 0004 created from existing submissions stay as
they are. infra/scripts/load_cedants.py adds names from a later file.
"""

from __future__ import annotations

from alembic import op
from db.cedant_seed import SEED_FILE, load_cedant_names, read_cedant_names

revision: str = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    load_cedant_names(op.get_bind(), read_cedant_names(SEED_FILE))


def downgrade() -> None:
    # The seeded cedants cannot be told apart from ones analysts added;
    # 0004's downgrade drops the table.
    pass
