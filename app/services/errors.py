"""Typed service-layer errors for the submission & package domain.

These are raised by `app/services/*_service.py` and mapped to HTTP responses by
the routers (contracts/data-access.md):

- ``SubmissionClosed``     — a mutation was attempted on a non-ACTIVE submission
                             (read-only gate, R3/FR-015) → 409.
- ``ConcurrencyConflict``  — optimistic-concurrency marker mismatch
                             (updated_at, R1/FR-031) → 409, input preserved.
- ``SelfRenewalError``     — renews_from_submission_id == id (R9/FR-007) → 422.
- ``EmptyPackageError``    — a package would have zero members (R5/FR-024).
- ``InvalidSourceFile``    — a browse selection is outside SHARED_DRIVE_ROOT,
                             missing, or not a file (FR-008/FR-009) → 422.
- ``InvalidMemberName``    — an EDM/RDM name has disallowed characters or is too
                             long (letters/digits/underscore/hyphen, ≤50) → 422.
- ``JobSubmitError``       — a Risk Modeler submit failed on a request-path helper
                             (retry / replace-file). This iteration defers all
                             submits to workers, so it is raised only from the
                             gateway-touching recovery helpers (contracts/data-access.md).
- ``NameCollisionError``   — an EDM/RDM name already exists in Risk Modeler
                             (FR-012 as amended by issue #17) → 422, blocking.
                             Raised only when the check actually reached Risk
                             Modeler; an unreachable gateway fails OPEN with a
                             warning instead (the worker-side submit validation
                             is the backstop).

They deliberately carry no DB or HTTP coupling — the service raises, the router
translates.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for domain service errors."""


class SubmissionClosed(ServiceError):
    """Raised when a mutation is attempted on a submission that is not ACTIVE."""


class ConcurrencyConflict(ServiceError):
    """Raised when an optimistic-concurrency check (updated_at) fails —
    someone else wrote the row first. The write is refused, never applied."""


class SelfRenewalError(ServiceError):
    """Raised when a submission would name itself as its own renewal source."""


class EmptyPackageError(ServiceError):
    """Raised when a package would be persisted with zero members."""


class InvalidSourceFile(ServiceError):
    """Raised when a shared-drive selection is outside SHARED_DRIVE_ROOT, missing,
    or is not a file. Mapped to HTTP 422 (FR-008/FR-009)."""


class InvalidMemberName(ServiceError):
    """Raised when an EDM/RDM name contains characters other than letters, digits,
    underscores, or hyphens, or exceeds 50 characters. Mapped to HTTP 422."""


class JobSubmitError(ServiceError):
    """Raised when a Risk Modeler submit fails on a request-path recovery helper
    (retry / replace-file). Normal submits are deferred to workers this iteration."""


class NameCollisionError(ServiceError):
    """Raised when a save would create an EDM/RDM whose name already exists in
    Risk Modeler (blocking — issue #17). Mapped to HTTP 422. Raised only when the
    check reached Risk Modeler; an unreachable gateway fails open instead."""


__all__ = [
    "ServiceError",
    "SubmissionClosed",
    "ConcurrencyConflict",
    "SelfRenewalError",
    "EmptyPackageError",
    "InvalidSourceFile",
    "InvalidMemberName",
    "JobSubmitError",
    "NameCollisionError",
]
