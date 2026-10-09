"""
Tests for ``validate_import_file_extension``.

``submit_edm_import_job`` and ``submit_rdm_import_job`` read
``properties.fileExtension`` out of the path they are handed, so what this
helper returns is what Create Import Folder is told the uploaded file is. It
runs before the S3 upload, which is why a rejected path has to raise rather
than fall through to the API.
"""

import pytest

from irp_integration.exceptions import IRPValidationError
from irp_integration.validators import validate_import_file_extension


@pytest.mark.parametrize("file_path,expected", [
    ("/data/exposure.bak", "bak"),
    ("/data/exposure.mdf", "mdf"),
    (r"C:\data\exposure.MDF", "mdf"),
    (r"C:\data\some.folder\exposure.BAK", "bak"),
])
def test_the_extension_comes_back_dotless_and_lowercase(file_path, expected):
    assert validate_import_file_extension(file_path, "edm_file_path") == expected


@pytest.mark.parametrize("file_path", [
    "/data/exposure.zip",
    "/data/exposure",
    "/data/exposure.bak.zip",
    r"C:\data\some.folder\exposure",
])
def test_anything_other_than_bak_or_mdf_raises(file_path):
    with pytest.raises(IRPValidationError, match=r"edm_file_path must name a \.bak, \.mdf file"):
        validate_import_file_extension(file_path, "edm_file_path")
