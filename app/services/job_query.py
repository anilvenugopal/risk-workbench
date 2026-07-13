"""Read-only job views spanning both job tables (contracts/data-access.md).

``irp_job`` (async Risk Modeler ops) and ``rwb_job`` (app-side queue) have different
writers, so their write-services are separate; the **read** views that union them live
here, importing neither write-module's internals. No row scoping (Article 6).

US5 provides ``package_job_counts`` (the per-package card counts); US6 adds ``list_jobs``
(the filtered Jobs list).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from db import execute

# Terminal vocabularies per table.
_IRP_TERMINAL = ("FINISHED", "FAILED", "CANCELED", "SUBMISSION FAILED")
_IRP_FAILED = ("FAILED", "CANCELED", "SUBMISSION FAILED")


@dataclass
class JobCounts:
    all: int
    active: int
    failed: int


def package_job_counts(package_id: Any) -> JobCounts:
    """all / active / failed counts for a package's work (FR-023/FR-024). Unions the
    ``irp_job`` rows at the package grain with the ``rwb_job`` rows keyed to the package
    or one of its members. ``active`` = non-terminal; ``failed`` = a terminal failure."""
    pid = str(package_id)
    irp = execute("SELECT status FROM irp_job WHERE package_id = :p",
                  {"p": pid}, connection="WORKBENCH")
    rwb = execute(
        """
        SELECT status_code FROM rwb_job
        WHERE requestor_id = :p
           OR requestor_id IN (SELECT id FROM irp_edm WHERE package_id = :p)
           OR requestor_id IN (SELECT id FROM irp_rdm WHERE package_id = :p)
        """,
        {"p": pid}, connection="WORKBENCH")

    all_count = len(irp) + len(rwb)
    active = (sum(1 for r in irp if r["status"] not in _IRP_TERMINAL)
              + sum(1 for r in rwb if r["status_code"] in ("pending", "running")))
    failed = (sum(1 for r in irp if r["status"] in _IRP_FAILED)
              + sum(1 for r in rwb if r["status_code"] == "failed"))
    return JobCounts(all=all_count, active=active, failed=failed)


__all__ = ["JobCounts", "package_job_counts"]
