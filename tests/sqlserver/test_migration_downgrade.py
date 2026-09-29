"""Every revision's downgrade() must undo its upgrade() on SQL Server.

The unit tier runs on a SQLite mirror and never executes the Alembic
revisions, so a downgrade that skips a table only shows up here. Revision
0001 once left two kind tables behind, which made the next
``alembic upgrade head`` fail on CREATE TABLE.
"""

from __future__ import annotations

import pytest

from tests.sqlserver.scratch import list_tables_and_views, run_alembic

pytestmark = pytest.mark.sqlserver


def test_downgrade_to_base_leaves_only_alembic_version(scratch_database):
    run_alembic("upgrade", "head", scratch_database)
    run_alembic("downgrade", "base", scratch_database)

    assert list_tables_and_views(scratch_database) == ["alembic_version"]

    run_alembic("upgrade", "head", scratch_database)
