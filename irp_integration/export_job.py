"""
Export job management operations.

Provides an interface for tracking and polling platform export jobs.
Uses the /platform/export/v1/jobs endpoint.
"""

import logging
import os
import tempfile
import time
import zipfile
from typing import Dict, Any, Optional, TYPE_CHECKING
from urllib.parse import parse_qsl, unquote, urlparse

import requests

from .constants import (
    GET_EXPORT_JOB,
    WORKFLOW_COMPLETED_STATUSES,
    WORKFLOW_FINISHED_STATUS,
)
from .exceptions import (
    IRPAPIError,
    IRPAuthenticationError,
    IRPFileError,
    IRPJobError,
)
from .validators import validate_positive_int, validate_non_empty_string

if TYPE_CHECKING:
    from . import IRPClient

logger = logging.getLogger(__name__)


class ExportJobManager:
    """Manager for platform export job operations."""

    def __init__(self, irp: "IRPClient") -> None:
        """
        Initialize ExportJobManager.

        Args:
            irp: Owning IRP client instance
        """
        self._irp = irp
        self.client = irp.client

    def get_export_job(self, job_id: int) -> Dict[str, Any]:
        """
        Get export job status by job ID.

        Args:
            job_id: Export job ID

        Returns:
            Dict containing job status details

        Raises:
            IRPValidationError: If job_id is invalid
            IRPAPIError: If request fails
            IRPAuthenticationError: If authenticated access fails
        """
        validate_positive_int(job_id, "job_id")

        try:
            response = self.client.request('GET', GET_EXPORT_JOB.format(jobId=job_id))
            return response.json()
        except IRPAuthenticationError:
            raise
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get export job status for job ID {job_id}: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def poll_export_job_to_completion(
        self,
        job_id: int,
        interval: int = 10,
        timeout: int = 600000
    ) -> Dict[str, Any]:
        """
        Poll export job until completion or timeout.

        Args:
            job_id: Export job ID
            interval: Polling interval in seconds (default: 10)
            timeout: Maximum timeout in seconds (default: 600000)

        Returns:
            Final job status details

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If job times out
            IRPAPIError: If polling fails
        """
        validate_positive_int(job_id, "job_id")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling export job ID %s", job_id)
            job_data = self.get_export_job(job_id)
            try:
                status = job_data['status']
                progress = job_data.get('progress', 0)
            except (KeyError, TypeError) as e:
                raise IRPAPIError(
                    f"Missing 'status' in job response for job ID {job_id}: {e}"
                ) from e
            logger.info("Job %s status: %s; progress: %s", job_id, status, progress)
            if status in WORKFLOW_COMPLETED_STATUSES:
                return job_data

            if time.time() - start > timeout:
                logger.error("Export job %s timed out after %s seconds. Last status: %s", job_id, timeout, status)
                raise IRPJobError(
                    f"Export job ID {job_id} did not complete within {timeout} seconds. "
                    f"Last status: {status}"
                )
            time.sleep(interval)

    def download_export_results(self, job_id: int, output_dir: str) -> str:
        """
        Download exported analysis results for a completed export job.

        Fetches the job, extracts ``downloadUrl`` from the ``DOWNLOAD_RESULTS``
        task, and uses the authenticated client session to stream the ZIP file
        to the output directory. A presigned S3 ``downloadUrl`` (one with an
        ``X-Amz-Signature`` query parameter) is requested without
        ``Authorization`` and ``x-rms-resource-group-id``. The completed
        download replaces an existing file with the same decoded filename only
        after the ZIP is validated.

        Args:
            job_id: Export job ID (must be FINISHED)
            output_dir: Directory to save the downloaded file

        Returns:
            Path to the downloaded file

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If job is not finished
            IRPAuthenticationError: If authenticated download access fails
            IRPAPIError: If ``downloadUrl`` is missing, the response is
                incomplete, or the response is not a ZIP file
            IRPFileError: If the output directory or downloaded file cannot be
                written or renamed
        """
        validate_positive_int(job_id, "job_id")
        validate_non_empty_string(output_dir, "output_dir")

        job_data = self.get_export_job(job_id)
        status = job_data.get('status')
        if status != WORKFLOW_FINISHED_STATUS:
            raise IRPJobError(
                f"Export job {job_id} is not finished (status: {status}). "
                "Cannot download results."
            )

        # Extract downloadUrl from the DOWNLOAD_RESULTS task
        download_url = None
        for task in job_data.get('tasks', []):
            if task.get('name') == 'DOWNLOAD_RESULTS':
                download_url = task.get('output', {}).get('log', {}).get('downloadUrl')
                break

        if not download_url:
            raise IRPAPIError(f"No download URL found in export job {job_id}")

        parsed_url = urlparse(download_url)

        # S3 returns 400 InvalidArgument when a presigned request also sends these headers.
        query_names = {name.lower() for name, _ in parse_qsl(parsed_url.query)}
        download_headers: Dict[str, Optional[str]] = {}
        if 'x-amz-signature' in query_names:
            download_headers = {'Authorization': None, 'x-rms-resource-group-id': None}

        # Extract filename from URL path (e.g., "{analysisId}_{portfolioName}_Losses.zip")
        url_path = unquote(parsed_url.path)
        filename = os.path.basename(url_path)
        if not filename:
            raise IRPAPIError(
                f"The download URL for export job ID {job_id} does not name a file"
            )
        output_path = os.path.join(output_dir, filename)
        temp_path = None
        response = None

        try:
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as e:
                raise IRPFileError(
                    f"Failed to create destination directory '{output_dir}' "
                    f"for export job ID {job_id}: {e}"
                ) from e

            logger.info("Downloading export results for job %s to %s", job_id, output_path)
            try:
                response = self.client.request(
                    'GET',
                    '',
                    full_url=download_url,
                    headers=download_headers,
                    stream=True,
                    timeout=300,
                )
            except IRPAuthenticationError:
                raise
            except IRPAPIError as e:
                raise IRPAPIError(
                    f"Failed to download export results for job ID {job_id}: {e}",
                    status_code=e.status_code,
                ) from e

            content_type = response.headers.get('Content-Type', '')
            media_type = content_type.split(';', 1)[0].strip().lower()
            if (
                media_type in {
                    'text/html',
                    'application/xhtml+xml',
                    'application/json',
                    'text/json',
                }
                or media_type.endswith('+json')
            ):
                raise IRPAPIError(
                    f"Export job ID {job_id} returned {media_type} instead of a ZIP file"
                )

            content_length = response.headers.get('Content-Length')
            expected_bytes = None
            if content_length is not None:
                try:
                    expected_bytes = int(content_length)
                except (TypeError, ValueError) as e:
                    raise IRPAPIError(
                        f"Export job ID {job_id} returned an invalid Content-Length "
                        f"header: {content_length!r}"
                    ) from e
                if expected_bytes < 0:
                    raise IRPAPIError(
                        f"Export job ID {job_id} returned a negative Content-Length "
                        f"header: {content_length!r}"
                    )

            try:
                temp_file = tempfile.NamedTemporaryFile(
                    mode='wb',
                    prefix='.export-',
                    suffix='.part',
                    dir=output_dir,
                    delete=False,
                )
                temp_path = temp_file.name
                downloaded_bytes = 0
                with temp_file:
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        temp_file.write(chunk)
                        downloaded_bytes += len(chunk)
            except requests.RequestException as e:
                raise IRPAPIError(
                    f"Failed while streaming export results for job ID {job_id}: {e}"
                ) from e
            except OSError as e:
                destination = temp_path or output_dir
                raise IRPFileError(
                    f"Failed to write export job ID {job_id} download to "
                    f"'{destination}': {e}"
                ) from e

            if expected_bytes is not None and downloaded_bytes != expected_bytes:
                raise IRPAPIError(
                    f"Export job ID {job_id} download was incomplete: expected "
                    f"{expected_bytes} bytes but received {downloaded_bytes}"
                )

            try:
                is_zip = zipfile.is_zipfile(temp_path)
            except OSError as e:
                raise IRPFileError(
                    f"Failed to read temporary download '{temp_path}' for export "
                    f"job ID {job_id}: {e}"
                ) from e
            if not is_zip:
                raise IRPAPIError(
                    f"Export job ID {job_id} download is not a valid ZIP file"
                )

            try:
                os.replace(temp_path, output_path)
            except OSError as e:
                raise IRPFileError(
                    f"Failed to move export job ID {job_id} download to "
                    f"'{output_path}': {e}"
                ) from e
            temp_path = None

            logger.info(
                "Downloaded %s bytes of export results for job %s to %s",
                downloaded_bytes,
                job_id,
                output_path,
            )
            return output_path
        except Exception:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    raise IRPFileError(
                        f"Failed to remove temporary download '{temp_path}' for "
                        f"export job ID {job_id}: {cleanup_error}"
                    ) from cleanup_error
            raise
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception as e:
                    logger.warning(
                        "Failed to close download response for export job ID %s: %s",
                        job_id,
                        e,
                    )
