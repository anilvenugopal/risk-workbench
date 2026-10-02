"""Submissions reference a cedant from a shared list (issue 129).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30

The backfill creates one cedant per distinct submission.cedant_name. Spelling
variants stay separate cedants; an analyst moves the submissions off the
unwanted one and deletes it.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.mssql import DATETIME2

from alembic import op

revision: str = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_V_CONTRACT_SELECT = """SELECT s.id            AS submission_id,
       s.name          AS submission_name,
       {cedant_name}, s.client_id, s.treaty_year, s.data_vintage,
       s.status_code   AS modeling_status_code,
       c.id            AS contract_id,
       c.crm_id, c.treaty_type_code, c.inception_date, c.expiration_date,
       c.contract_status_code
FROM contract c
JOIN submission s ON s.id = c.submission_id"""


def upgrade() -> None:
    op.create_table(
        "cedant",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("NEWID()")),
        sa.Column("name", sa.NVARCHAR(255), nullable=False),
        sa.Column("inserted_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at", DATETIME2, nullable=False,
                  server_default=sa.text("GETUTCDATE()")),
        sa.Column("inserted_by", sa.Uuid, nullable=True),
        sa.Column("updated_by", sa.Uuid, nullable=True),
        sa.ForeignKeyConstraint(["inserted_by"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"]),
    )
    # The database's default collation is case-insensitive, so the index refuses
    # "ACME" next to "Acme" (P-03).
    op.create_index("uq_cedant_name", "cedant", ["name"], unique=True)
    op.execute(sa.text(
        "INSERT INTO cedant (name) SELECT DISTINCT cedant_name FROM submission"))

    op.add_column("submission", sa.Column("cedant_id", sa.Uuid, nullable=True))
    op.execute(sa.text(
        "UPDATE s SET cedant_id = c.id "
        "FROM submission s JOIN cedant c ON c.name = s.cedant_name"))
    op.alter_column("submission", "cedant_id", existing_type=sa.Uuid, nullable=False)
    op.create_foreign_key("fk_submission_cedant", "submission", "cedant",
                          ["cedant_id"], ["id"])
    op.create_index("ix_submission_cedant_id", "submission", ["cedant_id"])

    op.execute(sa.text("DROP VIEW v_contract"))
    op.drop_index("ix_submission_cedant_name", table_name="submission")
    op.drop_column("submission", "cedant_name")
    op.execute(sa.text("CREATE VIEW v_contract AS\n" + _V_CONTRACT_SELECT.format(
        cedant_name="ced.name        AS cedant_name")
        + "\nJOIN cedant ced ON ced.id = s.cedant_id"))


def downgrade() -> None:
    op.execute(sa.text("DROP VIEW v_contract"))
    op.add_column("submission",
                  sa.Column("cedant_name", sa.NVARCHAR(255), nullable=True))
    op.execute(sa.text(
        "UPDATE s SET cedant_name = c.name "
        "FROM submission s JOIN cedant c ON c.id = s.cedant_id"))
    op.alter_column("submission", "cedant_name", existing_type=sa.NVARCHAR(255),
                    nullable=False)
    op.create_index("ix_submission_cedant_name", "submission", ["cedant_name"])
    op.execute(sa.text("CREATE VIEW v_contract AS\n" + _V_CONTRACT_SELECT.format(
        cedant_name="s.cedant_name")))

    op.drop_index("ix_submission_cedant_id", table_name="submission")
    op.drop_constraint("fk_submission_cedant", "submission", type_="foreignkey")
    op.drop_column("submission", "cedant_id")
    op.drop_index("uq_cedant_name", table_name="cedant")
    op.drop_table("cedant")
