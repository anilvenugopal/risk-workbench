"""Fixture archives in Risk Modeler's export layout (research R6) for the stage
worker tests, and a SQLite-capable stand-in for ``db.elt.upload_parquet``.

Layout of a real DLM archive::

    {job_id}_{analysis_name}_Losses/
        ELT/metadata.csv
        ELT/Portfolio/{perspective}/{job_id}_{analysis_name}_ELT_Portfolio_{perspective}_{n}.parquet

``upload_parquet`` itself sets a pyodbc-only cursor attribute, so the unit tier
replaces it with ``sqlite_upload_parquet`` (same signature, same column mapping)
to land rows in the ``loss_db`` mirror.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Any, Sequence

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text

from db import get_connection

ELT_COLUMNS = ("PortInfoId", "PortInfoName", "PortInfoNum", "EventId", "Rate", "Loss",
               "StdDevI", "StdDevC", "ExpValue")

DEFAULT_ROWS = [
    {"EventId": 1001, "Loss": 100.0, "StdDevI": -1.0, "StdDevC": 2.0, "ExpValue": 50.0},
    {"EventId": 1002, "Loss": 10.0, "StdDevI": 1.0, "StdDevC": 1.0, "ExpValue": 20.0},
    {"EventId": 3001, "Loss": 500.0, "StdDevI": 3.0, "StdDevC": 4.0, "ExpValue": 600.0},
]


def _parquet_bytes(rows: Sequence[dict], columns: Sequence[str]) -> bytes:
    n = len(rows)
    data: dict[str, Any] = {
        "PortInfoId": pa.array([1] * n, pa.int32()),
        "PortInfoName": pa.array(["Portfolio"] * n, pa.string()),
        "PortInfoNum": pa.array(["P1"] * n, pa.string()),
        "EventId": pa.array([int(r["EventId"]) for r in rows], pa.int32()),
        "Rate": pa.array([float(r.get("Rate", 0.001)) for r in rows], pa.float64()),
        "Loss": pa.array([float(r["Loss"]) for r in rows], pa.float64()),
        "StdDevI": pa.array([float(r["StdDevI"]) for r in rows], pa.float64()),
        "StdDevC": pa.array([float(r["StdDevC"]) for r in rows], pa.float64()),
        "ExpValue": pa.array([float(r["ExpValue"]) for r in rows], pa.float64()),
    }
    table = pa.table({c: data[c] for c in columns})
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def build_archive(
    dest_dir: Path, *,
    job_id: int = 25437617, analysis_name: str = "CRE_Port_Template",
    anls_id: int = 41958, currency: str = "USD", engine_type: str = "DLM",
    model_version: str = "25.0", loss_table: str = "ELT",
    perspectives: Sequence[str] = ("GR",),
    rows: Sequence[dict] | None = None, chunks: int = 1,
    metadata: bool = True, columns: Sequence[str] = ELT_COLUMNS,
) -> Path:
    """Write ``{job_id}_{analysis_name}_Losses.zip`` under ``dest_dir`` and return
    its path. Knobs: ``metadata=False`` omits metadata.csv; ``loss_table`` names
    the loss-table folder (``PLT`` or an unknown name); extra ``perspectives``
    add sibling perspective folders; ``rows=[]`` writes empty Parquet files;
    ``columns`` drops or renames Parquet columns; ``chunks`` splits the rows
    across ``_0.._n`` files."""
    rows = DEFAULT_ROWS if rows is None else list(rows)
    top = f"{job_id}_{analysis_name}_Losses"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{top}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        if metadata:
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(["AnlsId", "AnalysisName", "AnalysisCurrency", "Engine Type",
                             "ModelVersion", "Peril", "Region", "PerspCodes"])
            writer.writerow([anls_id, analysis_name, currency, engine_type, model_version,
                             "Earthquake", "NAEQ", ",".join(perspectives)])
            archive.writestr(f"{top}/{loss_table}/metadata.csv", out.getvalue())
        for perspective in perspectives:
            for chunk in range(chunks):
                chunk_rows = rows[chunk::chunks]
                archive.writestr(
                    f"{top}/{loss_table}/Portfolio/{perspective}/"
                    f"{job_id}_{analysis_name}_{loss_table}_Portfolio_{perspective}_{chunk}.parquet",
                    _parquet_bytes(chunk_rows, columns))
    return path


def sqlite_upload_parquet(
    file_path, table_name: str, schema: str = "dbo",
    extra_columns: dict | None = None, column_mapping: dict | None = None,
    drop_unmapped_columns: bool = False, batch_size: int = 50_000,
    connection: str = "WORKBENCH", database: str | None = None,
) -> int:
    """``db.elt.upload_parquet`` for the SQLite mirror: same signature, same
    mapping rules (a mapped source column missing from the file raises, unmapped
    columns are dropped when asked), rows inserted through the LOSS engine."""
    from db.errors import SQLServerQueryError  # noqa: PLC0415

    table = pq.read_table(file_path)
    col_map = column_mapping or {}
    missing = [c for c in col_map if c not in table.column_names]
    if missing:
        raise SQLServerQueryError(
            f"column_mapping references column(s) {missing} that are not present "
            f"in the source columns: {table.column_names}.")
    source_cols = list(table.column_names)
    if drop_unmapped_columns:
        source_cols = [c for c in source_cols if c in col_map]
    dest_cols = [col_map.get(c, c) for c in source_cols] + list((extra_columns or {}).keys())
    placeholders = ", ".join(f":{c}" for c in dest_cols)
    sql = (f"INSERT INTO {schema}.{table_name} ({', '.join(dest_cols)}) "
           f"VALUES ({placeholders})")
    records = table.select(source_cols).to_pylist() if source_cols else []
    rows = [{**{col_map.get(k, k): v for k, v in record.items()}, **(extra_columns or {})}
            for record in records]
    if not rows:
        return 0
    with get_connection(connection, database=database) as conn, conn.begin():
        conn.execute(text(sql), rows)
    return len(rows)
