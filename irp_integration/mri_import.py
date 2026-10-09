"""
MRI Import Manager for IRP Integration.

Handles Multi-Risk Insurance (MRI) data imports via the Platform Import API.
Files are uploaded to S3 and import jobs are submitted through the
/platform/import/v1 endpoints.
"""

import logging
import os
from typing import Dict, Any, Optional, Tuple, TYPE_CHECKING

from .constants import CREATE_IMPORT_FOLDER, SUBMIT_IMPORT_JOB
from .exceptions import IRPAPIError
from .validators import validate_non_empty_string, validate_file_exists
from .s3 import S3Manager
from .utils import extract_id_from_location_header

if TYPE_CHECKING:
    from . import IRPClient
    from .edm import EDMManager
    from .portfolio import PortfolioManager

logger = logging.getLogger(__name__)


class MRIImportManager:
    """Manager for MRI import operations."""

    def __init__(self, irp: "IRPClient") -> None:
        """
        Initialize MRI Import Manager.

        Args:
            irp: Owning IRP client instance
        """
        self._irp = irp
        self.client = irp.client

    @property
    def edm_manager(self) -> "EDMManager":
        """Return the owning client's EDM manager."""
        return self._irp.edm

    @property
    def portfolio_manager(self) -> "PortfolioManager":
        """Return the owning client's portfolio manager."""
        return self._irp.portfolio

    def submit_mri_import_job(
        self,
        edm_name: str,
        portfolio_name: str,
        accounts_file_path: str,
        locations_file_path: str,
        mapping_file_path: Optional[str] = None,
        delimiter: str = "TAB"
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Submit an MRI import job via the Platform Import API.

        This method handles the complete MRI import workflow:
        1. Look up EDM and portfolio
        2. Create import folder (get S3 credentials)
        3. Upload accounts, locations, and optionally mapping files to S3
        4. Submit import job

        Args:
            edm_name: Target EDM name
            portfolio_name: Target portfolio name within the EDM
            accounts_file_path: Path to accounts CSV file
            locations_file_path: Path to locations CSV file
            mapping_file_path: Optional path to .mff mapping file
            delimiter: File delimiter (default: "TAB")

        Returns:
            Tuple of (job_id, request_body) where request_body is the HTTP request payload

        Raises:
            IRPValidationError: If parameters are invalid
            IRPFileError: If file upload fails
            IRPAPIError: If any API call fails
        """
        validate_non_empty_string(edm_name, "edm_name")
        validate_non_empty_string(portfolio_name, "portfolio_name")
        validate_file_exists(accounts_file_path, "accounts_file_path")
        validate_file_exists(locations_file_path, "locations_file_path")
        if mapping_file_path is not None:
            validate_file_exists(mapping_file_path, "mapping_file_path")

        s3_manager = S3Manager()

        # Step 1: Look up EDM
        logger.debug("Looking up EDM '%s'", edm_name)
        edms = self.edm_manager.search_edms(filter=f"exposureName=\"{edm_name}\"")
        if len(edms) != 1:
            raise IRPAPIError(f"Expected 1 EDM with name {edm_name}, found {len(edms)}")
        try:
            exposure_id = edms[0]['exposureId']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(
                f"Failed to extract exposure ID for EDM '{edm_name}': {e}"
            ) from e

        # Step 2: Look up portfolio
        logger.debug("Looking up portfolio '%s'", portfolio_name)
        portfolios = self.portfolio_manager.search_portfolios(
            exposure_id=exposure_id,
            filter=f"portfolioName=\"{portfolio_name}\""
        )
        if len(portfolios) == 0:
            raise IRPAPIError(f"Portfolio with name {portfolio_name} not found")
        if len(portfolios) > 1:
            raise IRPAPIError(
                f"{len(portfolios)} portfolios found with name {portfolio_name}, please use a unique name"
            )
        try:
            portfolio_id = portfolios[0]['portfolioId']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(
                f"Failed to extract portfolio ID for portfolio '{portfolio_name}': {e}"
            ) from e

        resource_uri = f'/platform/riskdata/v1/exposures/{exposure_id}/portfolios/{portfolio_id}'

        # Step 3: Create import folder
        file_types = ["accountsFile", "locationsFile"]
        if mapping_file_path is not None:
            file_types.append("mappingFile")

        folder_data = {
            "folderType": "MRI",
            "properties": {
                "fileExtension": "csv",
                "fileTypes": file_types
            }
        }
        logger.debug("Creating import folder")
        response = self.client.request('POST', CREATE_IMPORT_FOLDER, json=folder_data)
        folder_response = response.json()

        try:
            folder_id = folder_response['folderId']
            upload_details = folder_response['uploadDetails']
        except (KeyError, TypeError) as e:
            raise IRPAPIError(
                f"Create import folder response missing required fields: {e}"
            ) from e

        # Step 4: Upload files to S3
        logger.debug("Uploading accounts file: %s", os.path.basename(accounts_file_path))
        try:
            accounts_upload = upload_details['accountsFile']
        except KeyError as e:
            raise IRPAPIError(f"Upload details missing accountsFile: {e}") from e
        s3_manager.upload_file(accounts_file_path, accounts_upload)

        logger.debug("Uploading locations file: %s", os.path.basename(locations_file_path))
        try:
            locations_upload = upload_details['locationsFile']
        except KeyError as e:
            raise IRPAPIError(f"Upload details missing locationsFile: {e}") from e
        s3_manager.upload_file(locations_file_path, locations_upload)

        if mapping_file_path is not None:
            logger.debug("Uploading mapping file: %s", os.path.basename(mapping_file_path))
            try:
                mapping_upload = upload_details['mappingFile']
            except KeyError as e:
                raise IRPAPIError(f"Upload details missing mappingFile: {e}") from e
            s3_manager.upload_file(mapping_file_path, mapping_upload)

        # Step 5: Submit import job
        settings = {
            "folderId": int(folder_id),
            "delimiter": delimiter
        }
        import_data = {
            "importType": "MRI",
            "resourceUri": resource_uri,
            "settings": settings
        }

        logger.info("Submitting MRI import job for '%s'/'%s'", edm_name, portfolio_name)
        response = self.client.request('POST', SUBMIT_IMPORT_JOB, json=import_data)
        job_id = extract_id_from_location_header(response, "MRI import job submission")

        logger.info("MRI import job submitted — job ID: %s", job_id)
        return int(job_id), import_data
