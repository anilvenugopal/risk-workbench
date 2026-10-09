"""
Python client library for Moody's Risk Modeler API.

The single entry point is ``IRPClient``, which holds one HTTP client and exposes
a manager per functional area; reach operations through those managers.

Managers (``client.<name>``):
    edm, portfolio, mri_import, treaty, analysis, grouping, risk_data_job, rdm,
    import_job, export_job, reference_data, and (optional) databridge.

Name-based interface: high-level methods accept human-readable names (EDM names,
portfolio names, profile names, treaty names) and resolve them to IDs internally.
``analysis.submit_portfolio_analysis_job`` also accepts ``model_profile_id``,
``output_profile_id``, ``event_rate_scheme_id`` and ``analysis_type`` in place
of the profile and scheme names.

S3 transfers for import/export staging are handled transparently by the relevant
managers — there is no need to hand-roll boto3.

Data Bridge (SQL Server) support is optional: ``client.databridge`` exists only
when the ``[databridge]`` extra and its ODBC driver are installed.

Pointers:
    - Cross-cutting workflow contract, including terminal-status handling → ``client.py``.
    - Domain concepts → each area's module docstring (e.g. ``analysis.py``,
      ``edm.py``, ``rdm.py``, ``treaty.py``).
"""

import logging

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("irp-integration")
except PackageNotFoundError:
    __version__ = "0.0.0"

logging.getLogger(__name__).addHandler(logging.NullHandler())

from .risk_data_job import RiskDataJobManager
from .client import Client
from .edm import EDMManager
from .portfolio import PortfolioManager
from .mri_import import MRIImportManager
from .analysis import AnalysisManager
from .grouping import GroupingManager
from .treaty import TreatyManager
from .reference_data import ReferenceDataManager
from .rdm import RDMManager
from .import_job import ImportJobManager
from .export_job import ExportJobManager
try:
    from .databridge import DataBridgeManager
except ImportError:
    DataBridgeManager = None  # type: ignore[assignment,misc]

class IRPClient:
    """Main client for IRP integration providing access to all managers."""

    def __init__(self) -> None:
        """Initialize the client and instantiate every manager."""
        self._client = Client()
        self.risk_data_job = RiskDataJobManager(self)
        self.reference_data = ReferenceDataManager(self)
        self.export_job = ExportJobManager(self)
        self.edm = EDMManager(self)
        self.portfolio = PortfolioManager(self)
        self.mri_import = MRIImportManager(self)
        self.analysis = AnalysisManager(self)
        self.grouping = GroupingManager(self)
        self.treaty = TreatyManager(self)
        self.rdm = RDMManager(self)
        self.import_job = ImportJobManager(self)
        if DataBridgeManager is not None:
            self.databridge = DataBridgeManager()

    @property
    def client(self):
        """Get the underlying API client."""
        return self._client

__all__ = ['IRPClient', 'DataBridgeManager', '__version__']
