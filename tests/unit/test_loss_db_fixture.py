"""The unit tier's LOSS mirror resolves the app's two-part table names."""

from __future__ import annotations

from db import execute, execute_command


def test_stage_and_dbo_tables_resolve_over_the_loss_connection(loss_db):
    assert execute("SELECT 1 AS x FROM stage.rwb_loss_result_manifest",
                   {}, connection="LOSS") == []
    execute_command("INSERT INTO dbo.Client (ClientID, ClientName, ActiveFlag) "
                    "VALUES (1, 'A', 'Y')", {}, connection="LOSS")
    assert execute("SELECT ClientName FROM dbo.Client", {}, connection="LOSS") == [
        {"ClientName": "A"}]
