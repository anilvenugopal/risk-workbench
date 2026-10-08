"""
Custom exception classes for IRP Integration module.

These exceptions provide clear, structured error handling for different
failure scenarios when interacting with Moody's Risk Modeler API.
"""

from typing import Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .grouping import GroupingProblem


class IRPIntegrationError(Exception):
    """
    Base exception for all IRP integration errors.

    ``status_code`` holds the HTTP status of the failed response, or ``None``
    when no response came back or the error does not concern an HTTP response.
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        """Initialize the error with an optional HTTP status code.

        Args:
            message: Error message
            status_code: HTTP status of the failed response, if one came back
        """
        super().__init__(message)
        self.status_code = status_code


class IRPAPIError(IRPIntegrationError):
    """
    API request or response errors.

    Raised when HTTP requests fail, responses are malformed,
    or API returns unexpected status codes.

    ``status_code`` is set when an HTTP response came back. It is ``None`` for
    connection errors, timeouts, configuration errors and malformed responses.
    """


class IRPAuthenticationError(IRPIntegrationError):
    """
    Bearer-token authentication errors.

    Raised when bearer-token login or token refresh fails (bad
    credentials, missing access token in the response, etc.).

    ``status_code`` is ``401`` when ``Client.request()`` still gets a ``401``
    after re-logging in. It is the login response's status when the bearer
    login gets a non-OK response. It is ``None`` for login request errors, a
    non-JSON login response and a missing ``accessToken``.
    """


class IRPValidationError(IRPIntegrationError):
    """
    Input validation errors.

    Raised when method parameters fail validation checks
    (e.g., empty strings, invalid IDs, missing files).
    """
    pass


class IRPGroupingValidationError(IRPValidationError):
    """Rule-based grouping validation errors with structured problems."""

    def __init__(self, problems: Sequence["GroupingProblem"]) -> None:
        """Initialize the error from one or more grouping problems.

        Args:
            problems: Structured problems that prevented grouping submission
        """
        self.problems = tuple(problems)
        message = "; ".join(problem.message for problem in self.problems)
        super().__init__(message or "Grouping validation failed")


class IRPWorkflowError(IRPIntegrationError):
    """
    Workflow execution errors.

    Raised when workflows fail to complete successfully,
    timeout, or return error status.
    """
    pass


class IRPReferenceDataError(IRPIntegrationError):
    """
    Reference data lookup errors.

    Raised when required reference data (treaty types, currencies, etc.)
    cannot be found or retrieved.
    """
    pass


class IRPFileError(IRPIntegrationError):
    """
    File operation errors.

    Raised when file operations fail (file not found, invalid format,
    upload errors, etc.).
    """
    pass


class IRPJobError(IRPIntegrationError):
    """
    Job management errors.

    Raised when job submission, status retrieval,
    or result fetching encounters issues.
    """
    pass


class IRPDataBridgeError(IRPIntegrationError):
    """
    Data Bridge (SQL Server) operation errors.

    Base exception for all SQL Server / Data Bridge failures
    including connection, configuration, and query errors.
    """
    pass


class IRPDataBridgeConnectionError(IRPDataBridgeError):
    """
    Data Bridge connection errors.

    Raised when SQL Server connection fails (bad credentials,
    unreachable server, driver not installed).
    """
    pass


class IRPDataBridgeQueryError(IRPDataBridgeError):
    """
    Data Bridge query execution errors.

    Raised when SQL query execution fails, parameter substitution
    fails, or SQL file cannot be read.
    """
    pass
