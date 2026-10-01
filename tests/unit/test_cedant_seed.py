"""The cedant list loaded from the client's Excel file (issue 129, P-10 to P-12)."""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from db import execute, get_connection
from db.cedant_seed import SEED_FILE, load_cedant_names, read_cedant_names
from tests.unit.conftest import cedant_id


def _xlsx(tmp_path, *cells):
    workbook = Workbook()
    for value in cells:
        workbook.active.append([value])
    path = tmp_path / "cedants.xlsx"
    workbook.save(path)
    return path


def test_read_trims_skips_blanks_and_keeps_the_first_spelling(tmp_path):
    path = _xlsx(tmp_path, " cedant ", "  Acme Re ", None, "   ", "ACME RE", 1234, "Beta Mutual")

    assert read_cedant_names(path) == ["Acme Re", "1234", "Beta Mutual"]


def test_read_refuses_a_file_whose_first_cell_is_not_cedant(tmp_path):
    with pytest.raises(ValueError, match="A1 must read 'Cedant'"):
        read_cedant_names(_xlsx(tmp_path, "Name", "Acme Re"))


def test_read_names_the_row_of_a_name_longer_than_255_characters(tmp_path):
    with pytest.raises(ValueError, match="row 3"):
        read_cedant_names(_xlsx(tmp_path, "Cedant", "Acme Re", "x" * 256))


def test_load_inserts_only_names_not_in_the_table(iteration2_db):
    cedant_id("Acme Re")

    with get_connection("WORKBENCH") as conn, conn.begin():
        first = load_cedant_names(conn, ["ACME RE", "Beta Mutual"])
    with get_connection("WORKBENCH") as conn, conn.begin():
        second = load_cedant_names(conn, ["ACME RE", "Beta Mutual"])

    rows = execute("SELECT name FROM cedant ORDER BY name", {}, connection="WORKBENCH")
    assert (first, second) == (1, 0)
    assert [r["name"] for r in rows] == ["Acme Re", "Beta Mutual"]


def test_the_committed_seed_file_reads():
    assert len(read_cedant_names(SEED_FILE)) == 25
