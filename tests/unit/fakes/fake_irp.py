"""In-memory fake Risk Modeler for the unit tier (Article 12).

Implements the ``app.services.irp_gateway.IRPGateway`` protocol without touching
``irp-integration`` or the network, so the poller and workers can be exercised
deterministically. Tests drive job outcomes explicitly:

    fake = FakeIRP()
    irp_gateway.configure(fake)
    res = fake.submit_edm_import(name="A", source_file_path="/x.bak")
    assert fake.get_import_job(res.irp_id).status == "QUEUED"
    fake.finish(res.irp_id)                 # → FINISHED
    fake.fail(res.irp_id)                   # → FAILED

Name-collision hits are seeded via ``add_edm_name`` / ``add_rdm_name``.
"""

from __future__ import annotations

from app.services.irp_gateway import AnalysisHit, EntityHit, JobStatus, SubmitResult


class FakeIRP:
    def __init__(self) -> None:
        self._seq = 0
        # irp_id -> current status string
        self.jobs: dict[str, str] = {}
        # irp_id -> terminal result body (set on finish/fail)
        self.results: dict[str, dict] = {}
        # recorded calls for assertions
        self.submits: list[dict] = []
        self.deleted_analysis_ids: list[int] = []
        # seeded name-collision universe
        self._edm_names: set[str] = set()
        self._rdm_names: set[str] = set()
        # seeded analyses for search_analyses (D2): list of dicts with the pair keys
        self._analyses: list[dict] = []
        # optionally force the next submit to fail (returns no irp_id)
        self.raise_on_submit = False

    # ── control surface (test-only) ────────────────────────────────────────────

    def add_edm_name(self, name: str) -> None:
        self._edm_names.add(name)

    def add_rdm_name(self, name: str) -> None:
        self._rdm_names.add(name)

    def add_analysis(self, *, source_rdm_name: str, exposure_name: str,
                     analysis_id: str, name: str | None = None) -> None:
        """Seed an analysis discoverable by ``search_analyses`` for this (RDM, EDM)
        pair — the backfill worker captures it as an ``irp_analysis`` row (D2)."""
        self._analyses.append({
            "analysis_id": str(analysis_id), "name": name,
            "source_rdm_name": source_rdm_name, "exposure_name": exposure_name})

    def finish(self, irp_id: str, result: dict | None = None) -> None:
        self.jobs[irp_id] = "FINISHED"
        self.results[irp_id] = result or {}

    def fail(self, irp_id: str, result: dict | None = None) -> None:
        self.jobs[irp_id] = "FAILED"
        self.results[irp_id] = result or {}

    def _next_id(self) -> str:
        self._seq += 1
        return str(self._seq)

    def _submit(self, kind: str, **meta) -> SubmitResult:
        if self.raise_on_submit:
            raise RuntimeError("fake IRP: forced submit failure")
        irp_id = self._next_id()
        self.jobs[irp_id] = "QUEUED"
        self.submits.append({"irp_id": irp_id, "kind": kind, **meta})
        return SubmitResult(
            irp_id=irp_id,
            resource_uri=f"/irp/{kind}/{irp_id}",
            payload={"kind": kind, **meta},
            response={"jobId": irp_id, "resourceUri": f"/irp/{kind}/{irp_id}"},
        )

    # ── IRPGateway protocol ─────────────────────────────────────────────────────

    def submit_edm_import(self, *, name: str, source_file_path: str) -> SubmitResult:
        return self._submit("import_edm", name=name, source_file_path=source_file_path)

    def submit_rdm_import(self, *, name: str, source_file_path: str,
                          edm_name: str | None) -> SubmitResult:
        return self._submit("import_rdm", name=name,
                            source_file_path=source_file_path, edm_name=edm_name)

    def submit_delete_edm(self, *, edm_irp_id: int) -> SubmitResult:
        return self._submit("delete_edm", edm_irp_id=edm_irp_id)

    def delete_analysis(self, *, analysis_id: int) -> None:
        # Synchronous single-analysis delete — no irp_job (R6). Record the call.
        self.deleted_analysis_ids.append(int(analysis_id))

    def search_analyses(self, *, filter: str) -> list[AnalysisHit]:
        # Return every seeded analysis whose pair markers appear in the filter,
        # e.g. 'sourceRdmName="R" AND exposureName="E"'.
        hits: list[AnalysisHit] = []
        for a in self._analyses:
            if (f'sourceRdmName="{a["source_rdm_name"]}"' in filter
                    and f'exposureName="{a["exposure_name"]}"' in filter):
                hits.append(AnalysisHit(
                    analysis_id=a["analysis_id"], name=a["name"],
                    source_rdm_name=a["source_rdm_name"],
                    exposure_name=a["exposure_name"]))
        return hits

    def get_import_job(self, irp_id: str) -> JobStatus:
        return JobStatus(status=self.jobs.get(irp_id, "QUEUED"),
                         result=self.results.get(irp_id))

    def get_delete_edm_job(self, irp_id: str) -> JobStatus:
        return JobStatus(status=self.jobs.get(irp_id, "QUEUED"),
                         result=self.results.get(irp_id))

    def search_edms(self, name: str) -> list[EntityHit]:
        return ([EntityHit(irp_id=f"edm-{name}", name=name)]
                if name in self._edm_names else [])

    def search_rdms(self, name: str) -> list[EntityHit]:
        return ([EntityHit(irp_id=f"rdm-{name}", name=name)]
                if name in self._rdm_names else [])
