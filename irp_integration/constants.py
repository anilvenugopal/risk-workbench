"""
API endpoint constants and status/code maps for the Risk Modeler API.

Defines:
    - Endpoint path templates, grouped by area. Most contain ``str.format``
      placeholders (e.g. ``{exposureId}``, ``{jobId}``) that callers fill in
      with resource IDs before issuing the request.
    - Workflow status groupings: ``WORKFLOW_COMPLETED_STATUSES`` (terminal) and
      ``WORKFLOW_IN_PROGRESS_STATUSES`` (non-terminal). See ``client.py`` for how
      these drive polling and the terminal-status contract.
    - Code maps that translate human-readable names to the short API codes:
      ``TREATY_TYPES``, ``TREATY_ATTACHMENT_BASES``, and
      ``TREATY_ATTACHMENT_LEVELS``.
    - ``PERSPECTIVE_CODES``: the financial perspective codes the analysis result
      endpoints accept as ``perspectiveCode``. See ``analysis.py`` for how
      ``get_elt()``, ``get_ep()``, ``get_stats()``, and ``get_plt()`` validate
      against it.
    - ``EXPOSURE_RESOURCE_TYPES``: the values the same endpoints accept as
      ``exposureResourceType``; the four getters validate their
      ``exposure_resource_type`` keyword against it.
    - ``ANALYSIS_TYPES``: the values ``CREATE_ANALYSIS_JOB`` accepts as the job
      ``type``; ``submit_portfolio_analysis_job()`` validates its
      ``analysis_type`` keyword against it.
"""

# Auth endpoints
LOGIN_IMPLICIT = '/sml/auth/v1/login/implicit'

# Workflow endpoints
GET_WORKFLOWS = '/riskmodeler/v1/workflows'
GET_WORKFLOW_BY_ID = '/riskmodeler/v1/workflows/{workflow_id}'

# Risk Data Job endpoints
GET_RISK_DATA_JOB_BY_ID = '/platform/riskdata/v1/jobs/{job_id}'
SEARCH_RISK_DATA_JOBS = '/platform/riskdata/v1/jobs'

# Workflow statuses
WORKFLOW_FINISHED_STATUS = 'FINISHED'
WORKFLOW_COMPLETED_STATUSES = [WORKFLOW_FINISHED_STATUS, 'FAILED', 'CANCELLED'] # https://developer.rms.com/risk-modeler/docs/workflow-engine#polling-workflow-job-and-operation-statuses
WORKFLOW_IN_PROGRESS_STATUSES = ['QUEUED', 'PENDING', 'RUNNING', 'CANCEL_REQUESTED', 'CANCELLING']

# EDM/Datasource endpoints
SEARCH_DATABASE_SERVERS = '/platform/riskdata/v1/dataservers'
SEARCH_EXPOSURE_SETS = '/platform/riskdata/v1/exposuresets'
CREATE_EXPOSURE_SET = '/platform/riskdata/v1/exposuresets'
# Resource URI for a single exposure set, as import jobs expect it in resourceUri.
EXPOSURE_SET_URI = '/platform/riskdata/v1/exposuresets/{exposureSetId}'
SEARCH_EDMS = '/platform/riskdata/v1/exposures'
CREATE_EDM = '/platform/riskdata/v1/exposuresets/{exposureSetId}/exposures'
UPGRADE_EDM_DATA_VERSION = '/platform/riskdata/v1/exposures/{exposureId}/data-upgrade'
DELETE_EDM = '/platform/riskdata/v1/exposures/{exposureId}'
GET_CEDANTS = '/platform/riskdata/v1/exposures/{exposureId}/cedants'
GET_LOBS = '/platform/riskdata/v1/exposures/{exposureId}/lobs'

# Platform Import Endpoints
CREATE_IMPORT_FOLDER = '/platform/import/v1/folders'
SUBMIT_IMPORT_JOB = '/platform/import/v1/jobs'
GET_IMPORT_JOB = '/platform/import/v1/jobs/{jobId}'
SEARCH_IMPORTED_RDMS = '/platform/riskdata/v1/analyses/imported-rdms'

# Database file extensions accepted by EDM and RDM import jobs, as
# properties.fileExtension expects them — no leading dot, lowercase.
IMPORT_FILE_EXTENSIONS = ("bak", "mdf")

# Portfolio endpoints
SEARCH_PORTFOLIOS = '/platform/riskdata/v1/exposures/{exposureId}/portfolios'
SEARCH_ACCOUNTS_BY_PORTFOLIO = '/platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/accounts'
CREATE_PORTFOLIO = '/platform/riskdata/v1/exposures/{exposureId}/portfolios'
GET_PORTFOLIO_BY_ID = '/platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}'
GET_PORTFOLIO_METADATA = '/platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/metrics'
ADD_FILTERED_ACCOUNTS = '/platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/filtered-accounts'
MANAGE_ACCOUNTS_BY_PORTFOLIO = '/platform/riskdata/v1/exposures/{exposureId}/portfolios/{id}/accounts'
GEOHAZ_PORTFOLIO = '/platform/geohaz/v1/jobs'
GET_GEOHAZ_JOB = '/platform/geohaz/v1/jobs/{jobId}'

# Portfolio field limits, enforced server-side: 41 characters is rejected with
# "portfolioName size must be between 0 and 40".
PORTFOLIO_NAME_MAX_LENGTH = 40
PORTFOLIO_NUMBER_MAX_LENGTH = 20

# Account endpoints
SEARCH_ACCOUNTS = '/platform/riskdata/v1/exposures/{exposureId}/accounts'

# Policy endpoints
SEARCH_POLICIES = '/platform/riskdata/v1/exposures/{exposureId}/policies'

# Location endpoints
SEARCH_LOCATIONS = '/platform/riskdata/v1/exposures/{exposureId}/locations'

# Treaty endpoints
SEARCH_TREATIES = 'platform/riskdata/v1/exposures/{exposureId}/treaties'
CREATE_TREATY = '/platform/riskdata/v1/exposures/{exposureId}/treaties'
TREATY_TYPES = {
    'Catastrophe': 'CATA',
    'Corporate Catastrophe': 'CORP',
    'Non-Catastrophe': 'NCAT',
    'Quota Share': 'QUOT',
    'Stop Loss': 'STOP',
    'Surplus Share': 'SURP',
    'Working Excess': 'WORK'
}
TREATY_ATTACHMENT_BASES = {
    'Losses Occurring': 'L',
    'Risks Attaching': 'R'
}
TREATY_ATTACHMENT_LEVELS = {
    'Account': 'ACCT',
    'Portfolio': 'PORT',
    'Policy': 'POL',
    'Location': 'LOC'
}
CREATE_TREATY_LOB = '/platform/riskdata/v1/exposures/{exposureId}/treaties/{id}/lob'

# Analysis endpoints
SEARCH_ANALYSIS_JOBS = '/platform/model/v1/jobs'
CREATE_ANALYSIS_JOB = '/platform/model/v1/jobs'
GET_ANALYSIS_JOB = '/platform/model/v1/jobs/{jobId}'
SEARCH_ANALYSIS_RESULTS = '/platform/riskdata/v1/analyses'
GET_ANALYSIS_RESULT = '/platform/riskdata/v1/analyses/{analysisId}'
CREATE_ANALYSIS_GROUP = '/platform/grouping/v1/jobs'
GET_ANALYSIS_GROUPING_JOB = '/platform/grouping/v1/jobs/{jobId}'
DELETE_ANALYSIS = '/platform/riskdata/v1/analyses/{analysisId}'

# Analysis results endpoints (ELT, EP, Stats, PLT)
GET_ANALYSIS_ELT = '/platform/riskdata/v1/analyses/{analysisId}/elt'
GET_ANALYSIS_EP = '/platform/riskdata/v1/analyses/{analysisId}/ep'
GET_ANALYSIS_STATS = '/platform/riskdata/v1/analyses/{analysisId}/stats'
GET_ANALYSIS_PLT = '/platform/riskdata/v1/analyses/{analysisId}/plt'
GET_ANALYSIS_REGIONS = '/platform/riskdata/v1/analyses/{analysisId}/regions'
GET_ANALYSIS_TREATIES = '/platform/riskdata/v1/analyses/{analysisId}/treaties'

# Perspective codes for analysis results
PERSPECTIVE_CODES = [
    'C0', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'CG',
    'CL', 'DC', 'EL', 'FA', 'G1', 'G2', 'GR', 'GS',
    'GU', 'I0', 'I1', 'I2', 'I3', 'I4', 'I5', 'I6',
    'IG', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'LG',
    'M0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'MG',
    'NL', 'NP', 'OI', 'OL', 'PY', 'QS', 'RC', 'RG',
    'RL', 'RN', 'RP', 'SS', 'TE', 'TG', 'TN', 'TV',
    'TY', 'UC', 'VA', 'VL', 'VP', 'VT', 'VY', 'WX',
]

# Exposure resource types the analysis result endpoints accept as
# exposureResourceType. TREATY scopes the result to one treaty of the run.
EXPOSURE_RESOURCE_TYPES = ['PORTFOLIO', 'TREATY']

# Analysis job types posted as the `type` field of CREATE_ANALYSIS_JOB
ANALYSIS_TYPES = ['DLM', 'HD']

GET_MODEL_PROFILES = '/analysis-settings/modelprofiles'
GET_OUTPUT_PROFILES = '/analysis-settings/outputprofiles'
GET_EVENT_RATE_SCHEME = '/data-store/referencetables/eventratescheme'

# Tag endpoints
GET_TAGS = '/platform/referencedata/v1/tags'
CREATE_TAG = '/platform/referencedata/v1/tags'

# RDM endpoints
SEARCH_DATABASES = '/platform/riskdata/v1/dataservers/{serverId}/databases'
DELETE_RDM = '/databridge/v1/sql-instances/{instanceName}/databases/{rdmName}'
GET_DATABRIDGE_JOB = '/databridge/v1/jobs/{jobId}'
UPDATE_GROUP_ACCESS = '/databridge/v1/sql-instances/{instanceName}/Databases/{databaseName}'

# Currency endpoints
SEARCH_CURRENCIES = '/data-store/referencetables/currency'
SEARCH_CURRENCY_SCHEMES = '/data-store/referencetables/currencyscheme'
SEARCH_CURRENCY_SCHEME_VINTAGES = '/data-store/referencetables/currencyschemevintage'

# Simulation/Model reference data endpoints
SEARCH_SIMULATION_SETS = '/data-store/referenceTables/SimulationSet'
SEARCH_PET_METADATA = '/data-store/referenceTables/PETMetadata'
SEARCH_SOFTWARE_MODEL_VERSION_MAP = '/data-store/referenceTables/SoftwareModelVersionMap'

# Export endpoints
CREATE_EXPORT_JOB = '/platform/export/v1/jobs'
GET_EXPORT_JOB = '/platform/export/v1/jobs/{jobId}'
