"""Typed service-layer errors for the submission & package domain.

These are raised by `app/services/*_service.py` and mapped to HTTP responses by
the routers (contracts/data-access.md):

- ``SubmissionClosed``     — a mutation was attempted on a non-ACTIVE submission
                             (read-only gate, R3/FR-015) → 409.
- ``ConcurrencyConflict``  — optimistic-concurrency marker mismatch
                             (updated_at, R1/FR-031) → 409, input preserved.
- ``SelfRenewalError``     — renews_from_submission_id == id (R9/FR-007) → 422.
- ``EmptyPackageError``    — a package would have zero members (R5/FR-024).

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


__all__ = [
    "ServiceError",
    "SubmissionClosed",
    "ConcurrencyConflict",
    "SelfRenewalError",
    "EmptyPackageError",
]
