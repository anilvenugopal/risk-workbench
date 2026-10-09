"""
Client for IRP Integration API requests.

HTTP transport plus the cross-cutting contracts every manager relies on. This
module is the authoritative home for those contracts; other modules point here
rather than restating them.

Async workflow model:
    Most write operations are asynchronous: submit a request, receive a
    ``201``/``202`` with a ``Location`` header naming the workflow, then poll
    that workflow until it reaches a terminal status. Building blocks:
        - ``execute_workflow(method, path, ...)`` — submit and poll in one call.
        - ``poll_workflow(url)`` — poll a workflow by its ``Location`` URL.
        - ``poll_workflow_to_completion(id)`` — poll a workflow by ID.
        - ``poll_workflow_batch_to_completion(ids)`` — poll many workflows at once.

Terminal status is not success:
    ``WORKFLOW_COMPLETED_STATUSES`` is ``FINISHED``, ``FAILED``, and
    ``CANCELLED``. Polling returns as soon as a workflow reaches *any* of these —
    including ``FAILED`` and ``CANCELLED``. A returned result therefore signals
    only that the workflow is done, not that it succeeded; the caller must
    inspect the returned ``status``.

Retries:
    Retries are built into the underlying session — 5 attempts with exponential
    backoff for ``429`` and ``5xx`` responses, across all HTTP methods. Do not
    add another retry layer on top of these calls.

Auth/config:
    The API base URL (``RISK_MODELER_BASE_URL``) and resource group
    (``RISK_MODELER_RESOURCE_GROUP_ID``) are always required. Two
    authentication strategies are supported, selected automatically in
    ``Client.__init__`` from which environment variables are populated:

        - **API key** (default / preserves existing behavior): if
          ``RISK_MODELER_API_KEY`` is set, it is sent verbatim in the
          ``Authorization`` header.
        - **Bearer login**: if the API key is absent but
          ``RISK_MODELER_TENANT_NAME``, ``RISK_MODELER_USERNAME``, and
          ``RISK_MODELER_PASSWORD`` are all set, the client logs in at
          construction to obtain a short-lived (1-hour) bearer token and
          sends ``Authorization: Bearer {accessToken}``.

    Requests made with ``full_url`` use the same authenticated session and
    retry policy. Export result downloads from a presigned S3 URL omit
    ``Authorization`` and ``x-rms-resource-group-id``.

    The API key takes precedence when both option sets are present.
    Bearer tokens are refreshed reactively: a ``401`` triggers a single
    re-login with the stored credentials and one retry of the request.
    ``__init__`` raises if neither complete option set is configured.
"""

import logging
import requests
import ssl
import time
import os
from typing import Dict, List, Any, Mapping, Optional, Union
from urllib3.util.retry import Retry
from urllib3.util.ssl_ import create_urllib3_context
from requests.adapters import HTTPAdapter
from .constants import  GET_WORKFLOWS, WORKFLOW_COMPLETED_STATUSES, WORKFLOW_IN_PROGRESS_STATUSES, GET_WORKFLOW_BY_ID
from .constants import LOGIN_IMPLICIT
from .exceptions import IRPAPIError, IRPAuthenticationError, IRPJobError, IRPWorkflowError
from .validators import validate_list_not_empty, validate_non_empty_string, validate_positive_int
from .utils import get_location_header

logger = logging.getLogger(__name__)


class _NonStrictX509Adapter(HTTPAdapter):
    """
    Verifies certificates without the ``VERIFY_X509_STRICT`` check.

    urllib3 sets ``VERIFY_X509_STRICT`` on Python 3.13+. The strict check
    rejects a CA certificate whose Basic Constraints are not marked critical,
    such as the root CA of some TLS-inspecting proxies. The CA bundle
    (``REQUESTS_CA_BUNDLE`` or certifi) is still loaded into this context.
    """

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs['ssl_context'] = create_urllib3_context(verify_flags=ssl.VERIFY_X509_PARTIAL_CHAIN)
        super().init_poolmanager(*args, **kwargs)


class Client:
    """Client for Moody's Risk Modeler API."""

    def __init__(self) -> None:
        """
        Initialize API client with credentials from environment.

        Two authentication strategies are supported, selected automatically
        from which environment variables are populated (see the module
        docstring for details):

            - **API key** (default): ``RISK_MODELER_API_KEY`` set.
            - **Bearer login**: API key absent and all of
              ``RISK_MODELER_TENANT_NAME``, ``RISK_MODELER_USERNAME``, and
              ``RISK_MODELER_PASSWORD`` set. The client logs in immediately to
              fetch the initial token.

        The API key takes precedence when both option sets are present. The
        chosen strategy is exposed as ``self.auth_mode`` (``'apikey'`` or
        ``'bearer'``); it is set once here and should be treated as read-only.

        Environment variables:
            RISK_MODELER_BASE_URL: API base URL (always required)
            RISK_MODELER_RESOURCE_GROUP_ID: Resource group ID (always required)
            RISK_MODELER_API_KEY: API authentication key (API-key strategy)
            RISK_MODELER_TENANT_NAME: Tenant name (bearer strategy)
            RISK_MODELER_USERNAME: Username (bearer strategy)
            RISK_MODELER_PASSWORD: Password (bearer strategy)
            RISK_MODELER_X509_STRICT: ``false`` turns off Python 3.13+'s
                ``VERIFY_X509_STRICT`` check; certificates are still verified

        Raises:
            IRPAPIError: If required configuration is missing or no complete
                authentication strategy is configured
            IRPAuthenticationError: If the initial bearer login fails
        """
        base_url = os.environ.get('RISK_MODELER_BASE_URL')
        resource_group_id = os.environ.get('RISK_MODELER_RESOURCE_GROUP_ID')
        api_key = os.environ.get('RISK_MODELER_API_KEY')
        tenant_name = os.environ.get('RISK_MODELER_TENANT_NAME')
        username = os.environ.get('RISK_MODELER_USERNAME')
        password = os.environ.get('RISK_MODELER_PASSWORD')

        if not base_url:
            raise IRPAPIError("Missing required environment variable: RISK_MODELER_BASE_URL")
        if not resource_group_id:
            raise IRPAPIError("Missing required environment variable: RISK_MODELER_RESOURCE_GROUP_ID")

        if api_key:
            self.auth_mode = 'apikey'
        elif tenant_name and username and password:
            self.auth_mode = 'bearer'
            self._tenant_name = tenant_name
            self._username = username
            self._password = password
        else:
            raise IRPAPIError(
                "No authentication strategy configured: set RISK_MODELER_API_KEY, "
                "or RISK_MODELER_TENANT_NAME + RISK_MODELER_USERNAME + RISK_MODELER_PASSWORD"
            )

        self.base_url = base_url
        self.timeout = 200

        session = requests.Session()
        session.headers.update({
            'x-rms-resource-group-id': resource_group_id,
        })

        retry = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
            raise_on_status=False,
        )
        adapter_class = HTTPAdapter
        if os.environ.get('RISK_MODELER_X509_STRICT', '').lower() == 'false':
            adapter_class = _NonStrictX509Adapter
        session.mount("https://", adapter_class(max_retries=retry))
        session.mount("http://", adapter_class(max_retries=retry))
        self.session = session

        if api_key:
            session.headers['Authorization'] = api_key
        else:
            self._login()

    @staticmethod
    def _safe_server_msg(response: requests.Response) -> str:
        """
        Extract a safe, truncated server message from an error response.

        Pulls the ``message`` or ``error`` field from the JSON body and caps it
        at 200 characters, returning a string like `` | server: <message>`` (or
        ``""`` if none is available). The full body is never returned or logged.

        Args:
            response: The error response to inspect

        Returns:
            A short, safe `` | server: ...`` suffix, or an empty string
        """
        try:
            body = response.json()
            server_msg = body.get("message") or body.get("error") or ""
            if server_msg:
                return f" | server: {str(server_msg)[:200]}"
        except Exception:
            pass
        return ""

    def _login(self) -> None:
        """
        Log in with stored bearer credentials and set the session token.

        POSTs to ``LOGIN_IMPLICIT`` with the tenant/username/password and
        installs the returned access token as the session ``Authorization``
        header. Issued through the session (so it shares the retry/backoff
        adapter and connection pool) but with ``Authorization`` explicitly
        overridden to ``None`` for this request, so it carries no stale token.
        Callers invoke it directly rather than via ``self.request`` to avoid
        recursing through the reactive 401-retry logic.

        Raises:
            IRPAuthenticationError: If login fails or the response is missing
                an access token. Server messages are truncated and credentials
                are never logged.
        """
        logger.info("Logging in for bearer token (tenant=%s)", self._tenant_name)
        try:
            response = self.session.post(
                f"{self.base_url}{LOGIN_IMPLICIT}",
                json={
                    "tenantName": self._tenant_name,
                    "username": self._username,
                    "password": self._password,
                },
                headers={"Content-Type": "application/json", "Authorization": None},  # type: ignore[dict-item]
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            logger.error("Bearer login request error: %s", e)
            raise IRPAuthenticationError(f"Bearer login request error: {e}") from e

        if not response.ok:
            safe_msg = self._safe_server_msg(response)
            logger.error("Bearer login failed (status %s)%s", response.status_code, safe_msg)
            raise IRPAuthenticationError(
                f"Bearer login failed (status {response.status_code}){safe_msg}",
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except Exception as e:
            raise IRPAuthenticationError("Bearer login returned a non-JSON response") from e

        access_token = body.get("accessToken")
        if not access_token:
            raise IRPAuthenticationError("Bearer login response missing 'accessToken'")

        token_type = body.get("tokenType") or "Bearer"
        # Stashed only as a hook for a possible future proactive-refresh upgrade.
        self._refresh_token = body.get("refreshToken")
        self._expires_in = body.get("expiresIn")
        self.session.headers['Authorization'] = f"{token_type} {access_token}"
        logger.info("Bearer login succeeded; token installed")

    def request(
        self,
        method: str,
        path: str,
        *,
        full_url: Optional[str] = None,
        base_url: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Union[Dict[str, Any], List[Any]]] = None,
        headers: Mapping[str, Optional[str]] = {},
        timeout: Optional[int] = None,
        stream: bool = False
    ) -> requests.Response:
        """
        Make HTTP request to API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: API path (e.g., '/api/v1/datasources')
            full_url: Full URL (overrides path/base_url if provided). The
                request still uses the authenticated session.
            base_url: Base URL (overrides default if provided)
            params: Query parameters
            json: JSON request body
            headers: Additional headers; ``None`` removes a session header
            timeout: Request timeout in seconds
            stream: Enable streaming response

        Returns:
            HTTP response object

        Raises:
            IRPAPIError: If the HTTP request fails
            IRPAuthenticationError: In bearer mode, if a ``401`` persists after
                a re-login and retry
        """
        validate_non_empty_string(method, "method")

        if full_url:
            url = full_url
        else:
            if base_url:
                url = f"{base_url}/{path.lstrip('/')}"
            else:
                url = f"{self.base_url}/{path.lstrip('/')}"

        logger.debug("%s %s", method, url)

        # Allow at most one reactive re-login on a 401 in bearer mode.
        auth_retried = False
        while True:
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=headers,  # type: ignore[arg-type]  # requests' hints omit None
                    timeout=timeout or self.timeout,
                    stream=stream,
                )
                response.raise_for_status()
            except requests.HTTPError as e:
                status_code = response.status_code
                if status_code == 401 and self.auth_mode == 'bearer' and not auth_retried:
                    logger.info("Received 401; re-logging in and retrying once: %s %s", method, url)
                    auth_retried = True
                    self._login()
                    continue
                safe_msg = self._safe_server_msg(response)
                logger.error(
                    "HTTP request failed: %s %s (status %s)%s",
                    method, url, status_code, safe_msg,
                )
                if status_code == 401 and self.auth_mode == 'bearer':
                    # Bearer 401 that survived a re-login + retry: this is an
                    # authentication failure, so surface it as one.
                    raise IRPAuthenticationError(
                        f"Bearer authentication failed after re-login: {method} {url} "
                        f"(status {status_code}){safe_msg}",
                        status_code=status_code,
                    ) from e
                raise IRPAPIError(
                    f"HTTP request failed: {method} {url} (status {status_code}){safe_msg}",
                    status_code=status_code,
                ) from e
            except requests.RequestException as e:
                logger.error("Request error: %s %s — %s", method, url, e)
                raise IRPAPIError(f"Request error: {e}") from e
            break

        logger.debug("%s %s — %s", method, url, response.status_code)
        return response


    def get_workflow(self, workflow_id: int) -> Dict[str, Any]:
        """
        Retrieve workflow status by workflow ID.

        Args:
            workflow_id: Workflow ID

        Returns:
            Dict containing workflow status details

        Raises:
            IRPValidationError: If workflow_id is invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(workflow_id, "workflow_id")

        try:
            response = self.request('GET', GET_WORKFLOW_BY_ID.format(workflow_id=workflow_id))
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get workflow status for workflow ID {workflow_id}: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def poll_workflow_to_completion(
        self,
        workflow_id: int,
        interval: int = 10,
        timeout: int = 600000
    ) -> Dict[str, Any]:
        """
        Poll workflow until completion or timeout.

        Returns on any terminal status (FINISHED, FAILED, or CANCELLED) — the
        caller must inspect the returned ``status``; see "Terminal status is not
        success" in the module docstring.

        Args:
            workflow_id: Workflow ID
            interval: Polling interval in seconds
            timeout: Maximum timeout in seconds

        Returns:
            Dict containing the final workflow status details

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If the workflow times out
            IRPAPIError: If a status request fails
        """
        validate_positive_int(workflow_id, "workflow_id")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling workflow ID %s", workflow_id)
            job_data = self.get_workflow(workflow_id)
            try:
                status = job_data['status']
                progress = job_data['progress']
            except (KeyError, TypeError) as e:
                raise IRPAPIError(
                    f"Missing 'status' or 'progress' in job response for workflow ID {workflow_id}: {e}"
                ) from e
            logger.info("Workflow %s status: %s; progress: %s", workflow_id, status, progress)
            if status in WORKFLOW_COMPLETED_STATUSES:
                return job_data

            if time.time() - start > timeout:
                logger.error("Workflow %s timed out after %s seconds. Last status: %s", workflow_id, timeout, status)
                raise IRPJobError(
                    f"Risk data workflow ID {workflow_id} did not complete within {timeout} seconds. Last status: {status}"
                )
            time.sleep(interval)


    def poll_workflow(
        self,
        workflow_url: str,
        interval: int = 10,
        timeout: int = 600000
    ) -> requests.Response:
        """
        Poll workflow until completion or timeout.

        Args:
            workflow_url: Full URL to workflow endpoint
            interval: Polling interval in seconds
            timeout: Maximum timeout in seconds

        Returns:
            Final workflow response

        Raises:
            IRPValidationError: If workflow_url is invalid
            IRPWorkflowError: If workflow times out
        """
        validate_non_empty_string(workflow_url, "workflow_url")
        
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling workflow URL %s", workflow_url)
            response = self.request('GET', '', full_url=workflow_url)
            workflow_data = response.json()
            status = workflow_data.get('status', '')
            progress = workflow_data.get('progress', '')
            logger.info("Workflow status: %s; progress: %s", status, progress)

            if status in WORKFLOW_COMPLETED_STATUSES:
                return response

            if time.time() - start > timeout:
                logger.error("Workflow timed out after %s seconds. Last status: %s", timeout, status)
                raise IRPWorkflowError(
                    f"Workflow did not complete within {timeout} seconds. Last status: {status}"
                )
            time.sleep(interval)

    def poll_workflow_batch_to_completion(
        self,
        workflow_ids: List[int],
        interval: int = 20,
        timeout: int = 600000
    ) -> requests.Response:
        """
        Poll multiple workflows until all complete or timeout.

        Args:
            workflow_ids: List of workflow IDs to poll
            interval: Polling interval in seconds
            timeout: Maximum timeout in seconds

        Returns:
            Response with all workflows combined

        Raises:
            IRPValidationError: If inputs are invalid
            IRPWorkflowError: If workflows time out
        """
        validate_list_not_empty(workflow_ids, "workflow_ids")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling batch workflow IDs: %s", ",".join(str(item) for item in workflow_ids))

            # Fetch all workflows across all pages
            all_workflows = []
            offset = 0
            limit = 100

            while True:
                params = {
                    'ids': ','.join(str(item) for item in workflow_ids),
                    'limit': limit,
                    'offset': offset
                }
                response = self.request('GET', GET_WORKFLOWS, params=params)
                response_data = response.json()

                try:
                    total_match_count = response_data['totalMatchCount']
                except (KeyError, TypeError) as e:
                    raise IRPAPIError(
                        f"Missing 'totalMatchCount' in workflow batch response: {e}"
                    ) from e

                workflows = response_data.get('workflows', [])

                all_workflows.extend(workflows)

                # Check if we've fetched all workflows
                if len(all_workflows) >= total_match_count:
                    break

                # Move to next page
                offset += limit

            # Check if all workflows are completed
            all_completed = True
            for workflow in all_workflows:
                status = workflow.get('status', '')
                if status in WORKFLOW_IN_PROGRESS_STATUSES:
                    all_completed = False
                    break

            if all_completed:
                # Return the last response but with all workflows combined
                response_data['workflows'] = all_workflows
                return response

            if time.time() - start > timeout:
                logger.error("Batch workflows timed out after %s seconds", timeout)
                raise IRPWorkflowError(
                    f"Batch workflows did not complete within {timeout} seconds"
                )
            time.sleep(interval)

    def execute_workflow(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Union[Dict[str, Any], List[Any]]] = None,
        headers: Dict[str, str] = {},
        timeout: Optional[int] = None,
        stream: bool = False
    ) -> requests.Response:
        """
        Execute workflow: submit request and poll until completion.

        This is a convenience method that combines request submission
        with automatic workflow polling.

        Args:
            method: HTTP method (POST, DELETE, etc.)
            path: API path
            params: Query parameters
            json: JSON request body
            headers: Additional headers
            timeout: Request timeout in seconds
            stream: Enable streaming response

        Returns:
            Final workflow response after completion

        Raises:
            IRPAPIError: If request fails
            IRPWorkflowError: If workflow times out
        """
        logger.info("Submitting workflow request...")
        response = self.request(
            method, path,
            params=params,
            json=json,
            headers=headers,
            timeout=timeout,
            stream=stream
        )

        if response.status_code not in (201, 202):
            return response

        workflow_url = get_location_header(response)
        if not workflow_url:
            raise IRPAPIError(
                "Workflow submission succeeded but Location header is missing"
            )

        return self.poll_workflow(workflow_url)
