"""
Tests for what ``submit_rdm_import_job`` puts in the job's ``resourceUri``.

An RDM is imported either into an EDM or standalone, and the only thing that
distinguishes the two in the request is ``resourceUri``: the EDM's ``uri`` for
the first, the exposure set's URI for the second. Getting it wrong does not
fail the call — it files the RDM somewhere the caller did not ask for — so the
assertions below are on the submitted body, not on the return value.

The mutual-exclusion checks run before the S3 upload, which is why passing both
names or neither has to raise rather than fall through to the API.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from irp_integration.exceptions import IRPValidationError
from irp_integration.rdm import RDMManager

from conftest import FakeClient, FakeResponse

# Invented names. Nothing here may name a real EDM, exposure set, RDM or
# tenant: this file ships in the sdist, so a name used here is published to PyPI.
RDM_NAME = "example_rdm"
EDM_NAME = "example_edm"
EXPOSURE_SET_NAME = "example_exposure_set"
EDM_URI = "/platform/riskdata/v1/exposures/42"


class FakeEDMManagerForImport:
    """Stand-in for ``EDMManager`` covering the two RDM import lookups."""

    def __init__(self, edms: Optional[List[Dict[str, Any]]] = None,
                 exposure_set_id: int = 7) -> None:
        self.edms = [] if edms is None else edms
        self.exposure_set_id = exposure_set_id
        self.edm_filters: List[str] = []
        self.exposure_set_names: List[str] = []

    def search_edms(self, filter: str = "", **kwargs: Any) -> List[Dict[str, Any]]:
        """Record the filter and return the configured EDMs."""
        self.edm_filters.append(filter)
        return self.edms

    def get_or_create_exposure_set(self, name: str) -> int:
        """Record the name and return the configured exposure set ID."""
        self.exposure_set_names.append(name)
        return self.exposure_set_id


class FakeS3Manager:
    """Stand-in for ``S3Manager`` that records uploads instead of making them."""

    uploads: List[str] = []

    def upload_file(self, file_path: str, upload_details: Dict[str, Any]) -> None:
        """Record the uploaded path."""
        FakeS3Manager.uploads.append(file_path)


@pytest.fixture
def rdm_file(tmp_path):
    """Return the path to a .bak file that exists, so validate_file_exists passes."""
    path = tmp_path / "example.bak"
    path.write_bytes(b"not a real database")
    return str(path)


@pytest.fixture
def make_rdm_manager(monkeypatch):
    """Return a factory building (RDMManager, FakeClient, FakeEDMManagerForImport)."""
    FakeS3Manager.uploads = []
    monkeypatch.setattr("irp_integration.rdm.S3Manager", FakeS3Manager)

    def build(edms=None, exposure_set_id=7):
        client = FakeClient([
            FakeResponse(201, json_body={
                'folderId': 11,
                'folderType': 'RDM',
                'uploadDetails': {'resultsFile': {'url': 'https://example.invalid/upload'}},
            }),
            FakeResponse(201, headers={'location': '/platform/import/v1/jobs/55'}),
        ])
        edm_manager = FakeEDMManagerForImport(edms, exposure_set_id)
        irp = SimpleNamespace(client=client, edm=edm_manager, analysis=None)
        return RDMManager(irp), client, edm_manager

    return build


def test_edm_name_imports_into_the_edms_uri(make_rdm_manager, rdm_file):
    manager, client, edm_manager = make_rdm_manager(edms=[{'uri': EDM_URI}])

    job_id, body = manager.submit_rdm_import_job(
        rdm_name=RDM_NAME, rdm_file_path=rdm_file, edm_name=EDM_NAME
    )

    assert job_id == 55, "the ID comes from the location header"
    assert body['resourceUri'] == EDM_URI
    assert body['importType'] == 'RDM'
    assert body['settings'] == {'folderId': 11, 'rdmName': RDM_NAME}
    assert edm_manager.exposure_set_names == [], (
        "the EDM path must not create an exposure set"
    )
    assert FakeS3Manager.uploads == [rdm_file]


def test_exposure_set_name_imports_standalone(make_rdm_manager, rdm_file):
    manager, client, edm_manager = make_rdm_manager(exposure_set_id=7)

    _, body = manager.submit_rdm_import_job(
        rdm_name=RDM_NAME,
        rdm_file_path=rdm_file,
        exposure_set_name=EXPOSURE_SET_NAME,
    )

    assert body['resourceUri'] == '/platform/riskdata/v1/exposuresets/7'
    assert edm_manager.exposure_set_names == [EXPOSURE_SET_NAME]
    assert edm_manager.edm_filters == [], "no EDM is looked up for a standalone RDM"
    assert body['settings'] == {'folderId': 11, 'rdmName': RDM_NAME}, (
        "a standalone import sends the same settings as an import into an EDM"
    )


def test_the_import_folder_carries_the_file_extension(make_rdm_manager, tmp_path, monkeypatch):
    manager, client, _ = make_rdm_manager()
    mdf_path = tmp_path / "example.MDF"
    mdf_path.write_bytes(b"not a real database")

    manager.submit_rdm_import_job(
        rdm_name=RDM_NAME,
        rdm_file_path=str(mdf_path),
        exposure_set_name=EXPOSURE_SET_NAME,
    )

    assert client.calls[0]['json'] == {
        'folderType': 'RDM',
        'properties': {'fileExtension': 'mdf'},
    }


def test_both_names_raises_before_any_request(make_rdm_manager, rdm_file):
    manager, client, edm_manager = make_rdm_manager(edms=[{'uri': EDM_URI}])

    with pytest.raises(IRPValidationError, match="not both"):
        manager.submit_rdm_import_job(
            rdm_name=RDM_NAME,
            rdm_file_path=rdm_file,
            edm_name=EDM_NAME,
            exposure_set_name=EXPOSURE_SET_NAME,
        )

    assert client.calls == []
    assert edm_manager.exposure_set_names == [], (
        "rejecting the call must not leave a created exposure set behind"
    )
    assert FakeS3Manager.uploads == []


def test_neither_name_raises_before_any_request(make_rdm_manager, rdm_file):
    manager, client, _ = make_rdm_manager()

    with pytest.raises(IRPValidationError, match="One of edm_name or exposure_set_name"):
        manager.submit_rdm_import_job(rdm_name=RDM_NAME, rdm_file_path=rdm_file)

    assert client.calls == []
    assert FakeS3Manager.uploads == []


def test_a_missing_edm_raises_before_the_upload(make_rdm_manager, rdm_file):
    manager, client, _ = make_rdm_manager(edms=[])

    with pytest.raises(Exception, match=f"EDM '{EDM_NAME}' not found"):
        manager.submit_rdm_import_job(
            rdm_name=RDM_NAME, rdm_file_path=rdm_file, edm_name=EDM_NAME
        )

    assert client.calls == []
    assert FakeS3Manager.uploads == []
