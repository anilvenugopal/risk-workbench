"""
Tests for authenticated export-result downloads.

The download response and export job response are queued on one fake client so
the tests can assert that ``downloadUrl`` goes through the same client session.
All ZIP files are created in memory and every test runs without network access.
"""

import io
import os
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import zipfile

import pytest
import requests

from irp_integration.exceptions import (
    IRPAPIError,
    IRPAuthenticationError,
    IRPFileError,
    IRPJobError,
)
from irp_integration.export_job import ExportJobManager


JOB_ID = 42
DOWNLOAD_URL = (
    "https://downloads.example.invalid/exports/Example%20Losses.zip?signature=test"
)
DECODED_FILENAME = "Example Losses.zip"


class DownloadResponse:
    """Response double supporting JSON job data and streamed download bytes."""

    def __init__(
        self,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        chunks: Optional[List[bytes]] = None,
        headers: Optional[Dict[str, str]] = None,
        stream_error: Optional[Exception] = None,
    ) -> None:
        self._json_body = json_body
        self._chunks = list(chunks or [])
        self.headers = dict(headers or {})
        self._stream_error = stream_error
        self.closed = False

    def json(self) -> Dict[str, Any]:
        """Return the configured export job response."""
        if self._json_body is None:
            raise ValueError("No JSON response configured")
        return self._json_body

    def iter_content(self, chunk_size: int):
        """Yield configured chunks and optionally fail after the first chunk."""
        assert chunk_size == 8192
        for index, chunk in enumerate(self._chunks):
            yield chunk
            if index == 0 and self._stream_error is not None:
                raise self._stream_error
        if not self._chunks and self._stream_error is not None:
            raise self._stream_error

    def close(self) -> None:
        """Record that the manager released the streaming response."""
        self.closed = True


class RecordingClient:
    """Return queued responses or exceptions and record every request argument."""

    def __init__(self, responses: List[Any]) -> None:
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> DownloadResponse:
        """Record one request and return its queued response."""
        self.calls.append({'method': method, 'path': path, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def zip_bytes() -> bytes:
    """Return a small valid ZIP archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode='w') as archive:
        archive.writestr('results.csv', 'analysisId,loss\n7,123.45\n')
    return buffer.getvalue()


def finished_job(download_url: Optional[str] = DOWNLOAD_URL) -> Dict[str, Any]:
    """Return a FINISHED export job with an optional ``downloadUrl``."""
    log = {} if download_url is None else {'downloadUrl': download_url}
    return {
        'status': 'FINISHED',
        'tasks': [
            {
                'name': 'DOWNLOAD_RESULTS',
                'output': {'log': log},
            }
        ],
    }


def make_manager(*responses: Any):
    """Return an export manager and its recording client."""
    client = RecordingClient(list(responses))
    return ExportJobManager(SimpleNamespace(client=client)), client


def part_files(output_dir) -> List[Any]:
    """Return temporary download files left in an output directory."""
    return list(output_dir.glob('*.part')) if output_dir.exists() else []


def test_valid_download_uses_authenticated_client_and_decoded_filename(tmp_path):
    data = zip_bytes()
    download_response = DownloadResponse(
        chunks=[data[:11], b'', data[11:]],
        headers={
            'Content-Type': 'application/zip',
            'Content-Length': str(len(data)),
        },
    )
    manager, client = make_manager(
        DownloadResponse(json_body=finished_job()),
        download_response,
    )
    output_dir = tmp_path / 'downloads'

    result = manager.download_export_results(JOB_ID, str(output_dir))

    destination = output_dir / DECODED_FILENAME
    assert result == str(destination)
    assert destination.read_bytes() == data
    assert zipfile.is_zipfile(destination)
    assert part_files(output_dir) == []
    assert client.calls[1] == {
        'method': 'GET',
        'path': '',
        'full_url': DOWNLOAD_URL,
        'headers': {},
        'stream': True,
        'timeout': 300,
    }
    assert download_response.closed


@pytest.mark.parametrize('name', ['X-Amz-Signature', 'x-amz-signature'])
def test_presigned_s3_download_removes_risk_modeler_headers(tmp_path, name):
    url = f"https://bucket.s3.example.invalid/Example%20Losses.zip?{name}=test"
    manager, client = make_manager(
        DownloadResponse(json_body=finished_job(download_url=url)),
        DownloadResponse(chunks=[zip_bytes()]),
    )

    manager.download_export_results(JOB_ID, str(tmp_path))

    assert client.calls[1]['headers'] == {
        'Authorization': None,
        'x-rms-resource-group-id': None,
    }


def test_cloudfront_download_keeps_session_headers(tmp_path):
    url = "https://cdn.example.invalid/Example%20Losses.zip?Signature=test&Key-Pair-Id=test"
    manager, client = make_manager(
        DownloadResponse(json_body=finished_job(download_url=url)),
        DownloadResponse(chunks=[zip_bytes()]),
    )

    manager.download_export_results(JOB_ID, str(tmp_path))

    assert client.calls[1]['headers'] == {}


@pytest.mark.parametrize(
    'content_type,body',
    [
        ('text/html; charset=utf-8', b'<html>Admin Center</html>'),
        ('application/json', b'{"message":"unauthorized"}'),
    ],
)
def test_html_and_json_downloads_are_rejected(tmp_path, content_type, body):
    manager, _ = make_manager(
        DownloadResponse(json_body=finished_job()),
        DownloadResponse(
            chunks=[body],
            headers={'Content-Type': content_type, 'Content-Length': str(len(body))},
        ),
    )

    with pytest.raises(IRPAPIError, match=rf"job ID {JOB_ID}.*instead of a ZIP"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert not (tmp_path / DECODED_FILENAME).exists()
    assert part_files(tmp_path) == []


def test_invalid_zip_is_rejected_and_removed(tmp_path):
    data = b'not a zip archive'
    manager, _ = make_manager(
        DownloadResponse(json_body=finished_job()),
        DownloadResponse(
            chunks=[data],
            headers={'Content-Type': 'application/octet-stream'},
        ),
    )

    with pytest.raises(IRPAPIError, match=rf"job ID {JOB_ID}.*not a valid ZIP"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert not (tmp_path / DECODED_FILENAME).exists()
    assert part_files(tmp_path) == []


def test_truncated_content_length_is_rejected_and_removed(tmp_path):
    data = zip_bytes()
    manager, _ = make_manager(
        DownloadResponse(json_body=finished_job()),
        DownloadResponse(
            chunks=[data],
            headers={'Content-Length': str(len(data) + 10)},
        ),
    )

    with pytest.raises(IRPAPIError, match=rf"job ID {JOB_ID}.*incomplete"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert not (tmp_path / DECODED_FILENAME).exists()
    assert part_files(tmp_path) == []


def test_streaming_exception_removes_partial_file(tmp_path):
    manager, _ = make_manager(
        DownloadResponse(json_body=finished_job()),
        DownloadResponse(
            chunks=[b'partial'],
            stream_error=requests.ConnectionError('connection reset'),
        ),
    )

    with pytest.raises(IRPAPIError, match=rf"streaming.*job ID {JOB_ID}"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert not (tmp_path / DECODED_FILENAME).exists()
    assert part_files(tmp_path) == []


def test_write_failure_raises_file_error_and_removes_partial_file(
    tmp_path, monkeypatch
):
    data = zip_bytes()
    manager, _ = make_manager(
        DownloadResponse(json_body=finished_job()),
        DownloadResponse(chunks=[data]),
    )
    real_named_temporary_file = tempfile.NamedTemporaryFile

    class FailingWriteFile:
        """Temporary file wrapper that fails its first write."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._file = real_named_temporary_file(*args, **kwargs)
            self.name = self._file.name

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._file.close()

        def write(self, chunk: bytes) -> int:
            raise OSError('disk full')

    monkeypatch.setattr(
        'irp_integration.export_job.tempfile.NamedTemporaryFile',
        FailingWriteFile,
    )

    with pytest.raises(IRPFileError, match=rf"job ID {JOB_ID}.*disk full"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert not (tmp_path / DECODED_FILENAME).exists()
    assert part_files(tmp_path) == []


def test_rename_failure_raises_file_error_and_removes_partial_file(
    tmp_path, monkeypatch
):
    data = zip_bytes()
    manager, _ = make_manager(
        DownloadResponse(json_body=finished_job()),
        DownloadResponse(chunks=[data]),
    )

    def fail_replace(source: str, destination: str) -> None:
        raise OSError('destination is read-only')

    monkeypatch.setattr(os, 'replace', fail_replace)

    with pytest.raises(IRPFileError, match=rf"job ID {JOB_ID}.*{DECODED_FILENAME}"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert not (tmp_path / DECODED_FILENAME).exists()
    assert part_files(tmp_path) == []


def test_http_failure_names_export_job(tmp_path):
    manager, _ = make_manager(
        DownloadResponse(json_body=finished_job()),
        IRPAPIError('HTTP request failed with status 403'),
    )

    with pytest.raises(IRPAPIError, match=rf"job ID {JOB_ID}.*status 403"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert part_files(tmp_path) == []


def test_authentication_error_from_download_is_preserved(tmp_path):
    authentication_error = IRPAuthenticationError('Bearer authentication failed')
    manager, _ = make_manager(
        DownloadResponse(json_body=finished_job()),
        authentication_error,
    )

    with pytest.raises(IRPAuthenticationError) as caught:
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert caught.value is authentication_error


def test_missing_download_url_fails_before_download_request(tmp_path):
    manager, client = make_manager(
        DownloadResponse(json_body=finished_job(download_url=None)),
    )

    with pytest.raises(IRPAPIError, match=rf"download URL.*job {JOB_ID}"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert len(client.calls) == 1
    assert part_files(tmp_path) == []


def test_non_finished_job_fails_before_download_request(tmp_path):
    manager, client = make_manager(
        DownloadResponse(json_body={'status': 'FAILED', 'tasks': []}),
    )

    with pytest.raises(IRPJobError, match=rf"job {JOB_ID}.*status: FAILED"):
        manager.download_export_results(JOB_ID, str(tmp_path))

    assert len(client.calls) == 1
    assert part_files(tmp_path) == []
