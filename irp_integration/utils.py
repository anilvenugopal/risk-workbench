"""
Utility functions for IRP Integration module.

Provides common helper functions for response parsing, data extraction,
and reference data lookup operations.
"""

import base64
import hashlib
import json
import logging
from typing import Callable, Dict, List, Any, Optional
import requests
from .exceptions import IRPAPIError, IRPReferenceDataError

logger = logging.getLogger(__name__)

DEFAULT_PAGE_LIMIT = 100
MAX_PAGES = 1000


def get_location_header(
    response: requests.Response,
    error_context: str = "response"
) -> str:
    """
    Get Location header from response.

    Args:
        response: HTTP response object
        error_context: Context description for error message

    Returns:
        Location header value

    Raises:
        IRPAPIError: If the Location header is missing
    """
    if 'location' not in response.headers:
        raise IRPAPIError(
            f"Location header missing from {error_context}"
        )
    return response.headers.get('location', '')


def extract_id_from_location_header(
    response: requests.Response,
    error_context: str = "response",
) -> str:
    """
    Extract ID from Location header in HTTP response.

    Args:
        response: HTTP response object
        error_context: Context description for error message

    Returns:
        Extracted ID string

    Raises:
        IRPAPIError: If Location header is missing
    """
    location = get_location_header(response, error_context)
    resource_id = location.split('/')[-1]
    if not resource_id:
        raise IRPAPIError(
            f"Could not extract ID from Location header: {location}"
        )
    return resource_id


def decode_base64_field(encoded_value: str, field_name: str) -> str:
    """
    Decode a base64-encoded field value.

    Args:
        encoded_value: Base64-encoded string
        field_name: Field name for error message

    Returns:
        Decoded string

    Raises:
        IRPAPIError: If decoding fails
    """
    try:
        return base64.b64decode(encoded_value).decode("utf-8")
    except Exception as e:
        raise IRPAPIError(
            f"Failed to decode base64 field '{field_name}': {e}"
        )


def decode_presign_params(presign_params: Dict[str, Any]) -> Dict[str, str]:
    """
    Decode base64 credentials from MRI import file credentials response.

    Args:
        presign_params: Response JSON containing encoded credentials

    Returns:
        Dict with decoded credential fields

    Raises:
        IRPAPIError: If required fields missing or decoding fails
    """
    required_fields = ['accessKeyId', 'secretAccessKey', 'sessionToken', 'path', 'region']
    missing = [f for f in required_fields if f not in presign_params]
    if missing:
        raise IRPAPIError(
            f"Presign params response missing fields: {', '.join(missing)}"
        )

    try:
        return {
            'aws_access_key_id': decode_base64_field(presign_params['accessKeyId'], 'accessKeyId'),
            'aws_secret_access_key': decode_base64_field(presign_params['secretAccessKey'], 'secretAccessKey'),
            'aws_session_token': decode_base64_field(presign_params['sessionToken'], 'sessionToken'),
            's3_path': decode_base64_field(presign_params['path'], 'path'),
            's3_region': decode_base64_field(presign_params['region'], 'region')
        }
    except IRPAPIError:
        raise
    except Exception as e:
        raise IRPAPIError(f"Failed to decode MRI credentials: {e}")


def _fingerprint(value: Any) -> str:
    """
    Build a stable content hash of a record or a page of records.

    Used to tell whether a search has actually advanced, without needing to
    know which field of a given record type is its identifier.

    Args:
        value: Any JSON-serializable value

    Returns:
        Hex digest of the value's canonical JSON form
    """
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode('utf-8')
    ).hexdigest()


def paginate_search(
    fetch: Callable[[int, int], List[Any]],
    description: str,
    limit: int = DEFAULT_PAGE_LIMIT,
    max_pages: int = MAX_PAGES
) -> List[Any]:
    """
    Page through a limit/offset search operation until it is exhausted.

    ``offset`` counts records, so page N begins at ``N * limit``. The Risk Data
    API documents this two ways — the operation reference calls it a page number,
    the filtering guide a record offset — and the record reading is what the
    account, policy and location searches were observed to do.

    Four outcomes, because that reading comes from one deployment:

    - A page larger than ``limit``: the operation ignored pagination and the
      first response was already the whole result, which is returned as-is.
    - An empty page, or one shorter than ``limit``: the walk ends normally.
    - A page identical to one already seen: ``IRPAPIError``. That is what a
      server clamping or ignoring an out-of-range ``offset`` produces. A
      complete result of exactly ``limit`` records is indistinguishable from it
      and raises too, which is the safe side to be wrong on — callers build
      portfolios out of these results, so a list truncated silently would create
      a sub-portfolio missing accounts and report success.
    - ``max_pages`` exhausted with pages still full: ``IRPAPIError``. This caps
      a read at ``max_pages * limit`` records.

    Progress is tracked by hashing page content, not by reading record IDs, so a
    response shape that differs from the spec still ends the walk rather than
    spinning.

    One failure mode is uncovered: a server that treats ``offset`` as a page
    number *and* answers an out-of-range page with an empty list would stop after
    one page and look like a clean finish. Detecting it costs a request on every
    walk; a caller that suspects it should compare against a count the API did
    not produce.

    Args:
        fetch: Callable taking (limit, offset) and returning one page of results
        description: Phrase naming the search, used in log and error messages
        limit: Page size to request (default: 100)
        max_pages: Hard ceiling on requests before giving up (default: 1000)

    Returns:
        Every record the operation returned, in page order

    Raises:
        IRPAPIError: If the walk cannot establish that it read every page,
            because the operation repeated a page or because ``max_pages`` was
            exhausted with pages still coming back full
        Whatever ``fetch`` raises, unchanged
    """
    first_page = fetch(limit, 0)

    if len(first_page) > limit:
        logger.warning(
            "%s ignored pagination (%s rows for limit %s); treating that response as complete",
            description, len(first_page), limit
        )
        return first_page
    if len(first_page) < limit:
        return first_page

    all_results: List[Any] = list(first_page)
    seen_pages = {_fingerprint(first_page)}

    for page_index in range(1, max_pages):
        offset = page_index * limit
        page = fetch(limit, offset)
        if not page:
            break

        fingerprint = _fingerprint(page)
        if fingerprint in seen_pages:
            raise IRPAPIError(
                f"{description} returned an already-seen page at offset {offset}, "
                f"so pagination is not advancing and the {len(all_results)} records "
                f"read so far cannot be shown to be the complete result"
            )
        seen_pages.add(fingerprint)

        all_results.extend(page)

        # A short page is the last page
        if len(page) < limit:
            break
    else:
        raise IRPAPIError(
            f"{description} was still returning full pages of {limit} at the "
            f"{max_pages}-page ceiling, so the {len(all_results)} records read "
            f"cannot be shown to be the complete result"
        )

    return all_results


def extract_analysis_id_from_workflow_response(workflow: Dict[str, Any]) -> Optional[str]:
    """
    Extract analysis ID from workflow response.

    Args:
        workflow: Workflow response dict

    Returns:
        Analysis ID if found, None otherwise

    Raises:
        IRPAPIError: If required fields are missing from workflow response
    """
    try:
        return workflow['output']['analysisId']
    except (KeyError, TypeError) as e:
        raise IRPAPIError(
            f"Missing required field in workflow response: {e}"
        ) from e
