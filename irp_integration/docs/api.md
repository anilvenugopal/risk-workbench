# API Reference

_This file is generated from source docstrings by `irp_integration/docs/generate_api_docs.py`. Do not edit by hand — run `uv run python irp_integration/docs/generate_api_docs.py` to regenerate._

Python client library for Moody's Risk Modeler API.

The single entry point is ``IRPClient``, which holds one HTTP client and exposes a manager per functional area; reach operations through those managers.

**Managers (``client.<name>``):**

edm, portfolio, mri_import, treaty, analysis, grouping, risk_data_job, rdm, import_job, export_job, reference_data, and (optional) databridge.

Name-based interface: high-level methods accept human-readable names (EDM names, portfolio names, profile names, treaty names) and resolve them to IDs internally. ``analysis.submit_portfolio_analysis_job`` also accepts ``model_profile_id``, ``output_profile_id``, ``event_rate_scheme_id`` and ``analysis_type`` in place of the profile and scheme names.

S3 transfers for import/export staging are handled transparently by the relevant managers — there is no need to hand-roll boto3.

Data Bridge (SQL Server) support is optional: ``client.databridge`` exists only when the ``[databridge]`` extra and its ODBC driver are installed.

**Pointers:**

- Cross-cutting workflow contract, including terminal-status handling → ``client.py``.
- Domain concepts → each area's module docstring (e.g. ``analysis.py``, ``edm.py``, ``rdm.py``, ``treaty.py``).

## Table of Contents

- [`irp_integration.client`](#irp_integrationclient)
  - [Client](#class-client)
- [`irp_integration.edm`](#irp_integrationedm)
  - [EDMManager](#class-edmmanager)
- [`irp_integration.portfolio`](#irp_integrationportfolio)
  - [PortfolioManager](#class-portfoliomanager)
- [`irp_integration.mri_import`](#irp_integrationmri_import)
  - [MRIImportManager](#class-mriimportmanager)
- [`irp_integration.treaty`](#irp_integrationtreaty)
  - [TreatyManager](#class-treatymanager)
- [`irp_integration.analysis`](#irp_integrationanalysis)
  - [AppliedTreaty](#class-appliedtreaty)
  - [RunDescription](#class-rundescription)
  - [AnalysisManager](#class-analysismanager)
- [`irp_integration.grouping`](#irp_integrationgrouping)
  - [EventRateSchemeOption](#class-eventrateschemeoption)
  - [EventRateSelection](#class-eventrateselection)
  - [GroupingCurrency](#class-groupingcurrency)
  - [GroupingInspection](#class-groupinginspection)
  - [GroupingManager](#class-groupingmanager)
  - [GroupingMember](#class-groupingmember)
  - [GroupingPartition](#class-groupingpartition)
  - [GroupingPartitionKey](#class-groupingpartitionkey)
  - [GroupingProblem](#class-groupingproblem)
  - [GroupingProblemCode](#class-groupingproblemcode)
  - [GroupingRegionFact](#class-groupingregionfact)
  - [GroupingSettings](#class-groupingsettings)
  - [GroupingSimulationMapping](#class-groupingsimulationmapping)
  - [GroupingSubmission](#class-groupingsubmission)
  - [GroupingTreaty](#class-groupingtreaty)
  - [SimulationPeriodsSelection](#class-simulationperiodsselection)
  - [SimulationSetOption](#class-simulationsetoption)
  - [SimulationSetSelection](#class-simulationsetselection)
- [`irp_integration.analysis_validation`](#irp_integrationanalysis_validation)
- [`irp_integration.rdm`](#irp_integrationrdm)
  - [RDMManager](#class-rdmmanager)
- [`irp_integration.risk_data_job`](#irp_integrationrisk_data_job)
  - [RiskDataJobManager](#class-riskdatajobmanager)
- [`irp_integration.import_job`](#irp_integrationimport_job)
  - [ImportJobManager](#class-importjobmanager)
- [`irp_integration.export_job`](#irp_integrationexport_job)
  - [ExportJobManager](#class-exportjobmanager)
- [`irp_integration.s3`](#irp_integrations3)
  - [S3Manager](#class-s3manager)
- [`irp_integration.reference_data`](#irp_integrationreference_data)
  - [ReferenceDataManager](#class-referencedatamanager)
- [`irp_integration.databridge`](#irp_integrationdatabridge)
  - [ExpressionTemplate](#class-expressiontemplate)
  - [DataBridgeManager](#class-databridgemanager)
- [`irp_integration.exceptions`](#irp_integrationexceptions)
  - [IRPIntegrationError](#class-irpintegrationerror)
  - [IRPAPIError](#class-irpapierror)
  - [IRPAuthenticationError](#class-irpauthenticationerror)
  - [IRPValidationError](#class-irpvalidationerror)
  - [IRPGroupingValidationError](#class-irpgroupingvalidationerror)
  - [IRPWorkflowError](#class-irpworkflowerror)
  - [IRPReferenceDataError](#class-irpreferencedataerror)
  - [IRPFileError](#class-irpfileerror)
  - [IRPJobError](#class-irpjoberror)
  - [IRPDataBridgeError](#class-irpdatabridgeerror)
  - [IRPDataBridgeConnectionError](#class-irpdatabridgeconnectionerror)
  - [IRPDataBridgeQueryError](#class-irpdatabridgequeryerror)
- [`irp_integration.validators`](#irp_integrationvalidators)
- [`irp_integration.utils`](#irp_integrationutils)
- [`irp_integration.constants`](#irp_integrationconstants)

---

## `irp_integration.client`

Client for IRP Integration API requests.

HTTP transport plus the cross-cutting contracts every manager relies on. This module is the authoritative home for those contracts; other modules point here rather than restating them.

**Async workflow model:**

Most write operations are asynchronous: submit a request, receive a ``201``/``202`` with a ``Location`` header naming the workflow, then poll that workflow until it reaches a terminal status. Building blocks:

- ``execute_workflow(method, path, ...)`` — submit and poll in one call.
- ``poll_workflow(url)`` — poll a workflow by its ``Location`` URL.
- ``poll_workflow_to_completion(id)`` — poll a workflow by ID.
- ``poll_workflow_batch_to_completion(ids)`` — poll many workflows at once.

**Terminal status is not success:**

``WORKFLOW_COMPLETED_STATUSES`` is ``FINISHED``, ``FAILED``, and ``CANCELLED``. Polling returns as soon as a workflow reaches *any* of these — including ``FAILED`` and ``CANCELLED``. A returned result therefore signals only that the workflow is done, not that it succeeded; the caller must inspect the returned ``status``.

**Retries:**

Retries are built into the underlying session — 5 attempts with exponential backoff for ``429`` and ``5xx`` responses, across all HTTP methods. Do not add another retry layer on top of these calls.

**Auth/config:**

The API base URL (``RISK_MODELER_BASE_URL``) and resource group (``RISK_MODELER_RESOURCE_GROUP_ID``) are always required. Two authentication strategies are supported, selected automatically in ``Client.__init__`` from which environment variables are populated:

- **API key** (default / preserves existing behavior): if ``RISK_MODELER_API_KEY`` is set, it is sent verbatim in the ``Authorization`` header.
- **Bearer login**: if the API key is absent but ``RISK_MODELER_TENANT_NAME``, ``RISK_MODELER_USERNAME``, and ``RISK_MODELER_PASSWORD`` are all set, the client logs in at construction to obtain a short-lived (1-hour) bearer token and sends ``Authorization: Bearer {accessToken}``.

Requests made with ``full_url`` use the same authenticated session and retry policy. Export result downloads from a presigned S3 URL omit ``Authorization`` and ``x-rms-resource-group-id``.

The API key takes precedence when both option sets are present. Bearer tokens are refreshed reactively: a ``401`` triggers a single re-login with the stored credentials and one retry of the request. ``__init__`` raises if neither complete option set is configured.

### `class Client`

Client for Moody's Risk Modeler API.

#### `__init__`

```python
def __init__(self)
```

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

**Environment variables:**
> RISK_MODELER_BASE_URL: API base URL (always required)
> RISK_MODELER_RESOURCE_GROUP_ID: Resource group ID (always required)
> RISK_MODELER_API_KEY: API authentication key (API-key strategy)
> RISK_MODELER_TENANT_NAME: Tenant name (bearer strategy)
> RISK_MODELER_USERNAME: Username (bearer strategy)
> RISK_MODELER_PASSWORD: Password (bearer strategy)
> RISK_MODELER_X509_STRICT: ``false`` turns off Python 3.13+'s
>     ``VERIFY_X509_STRICT`` check; certificates are still verified

**Raises:**
 - **IRPAPIError:**  If required configuration is missing or no complete
   authentication strategy is configured
 - **IRPAuthenticationError:**  If the initial bearer login fails

#### `request`

```python
def request(
    self,
    method: str,
    path: str,
    *,
    full_url: Optional[str] = None,
    base_url: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    json: Union[Dict[str, Any], List[Any], NoneType] = None,
    headers: Mapping[str, Optional[str]] = {},
    timeout: Optional[int] = None,
    stream: bool = False
) -> requests.models.Response
```

Make HTTP request to API.

**Arguments:**
 - **method:**  HTTP method (GET, POST, PUT, DELETE, etc.)
 - **path:**  API path (e.g., '/api/v1/datasources')
 - **full_url:**  Full URL (overrides path/base_url if provided). The
   request still uses the authenticated session.
 - **base_url:**  Base URL (overrides default if provided)
 - **params:**  Query parameters
 - **json:**  JSON request body
 - **headers:**  Additional headers; ``None`` removes a session header
 - **timeout:**  Request timeout in seconds
 - **stream:**  Enable streaming response

**Returns:**
> HTTP response object

**Raises:**
 - **IRPAPIError:**  If the HTTP request fails
 - **IRPAuthenticationError:**  In bearer mode, if a ``401`` persists after
   a re-login and retry

#### `get_workflow`

```python
def get_workflow(self, workflow_id: int) -> Dict[str, Any]
```

Retrieve workflow status by workflow ID.

**Arguments:**
 - **workflow_id:**  Workflow ID

**Returns:**
> Dict containing workflow status details

**Raises:**
 - **IRPValidationError:**  If workflow_id is invalid
 - **IRPAPIError:**  If request fails

#### `poll_workflow_to_completion`

```python
def poll_workflow_to_completion(
    self,
    workflow_id: int,
    interval: int = 10,
    timeout: int = 600000
) -> Dict[str, Any]
```

Poll workflow until completion or timeout.

Returns on any terminal status (FINISHED, FAILED, or CANCELLED) — the
caller must inspect the returned ``status``; see "Terminal status is not
success" in the module docstring.

**Arguments:**
 - **workflow_id:**  Workflow ID
 - **interval:**  Polling interval in seconds
 - **timeout:**  Maximum timeout in seconds

**Returns:**
> Dict containing the final workflow status details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If the workflow times out
 - **IRPAPIError:**  If a status request fails

#### `poll_workflow`

```python
def poll_workflow(
    self,
    workflow_url: str,
    interval: int = 10,
    timeout: int = 600000
) -> requests.models.Response
```

Poll workflow until completion or timeout.

**Arguments:**
 - **workflow_url:**  Full URL to workflow endpoint
 - **interval:**  Polling interval in seconds
 - **timeout:**  Maximum timeout in seconds

**Returns:**
> Final workflow response

**Raises:**
 - **IRPValidationError:**  If workflow_url is invalid
 - **IRPWorkflowError:**  If workflow times out

#### `poll_workflow_batch_to_completion`

```python
def poll_workflow_batch_to_completion(
    self,
    workflow_ids: List[int],
    interval: int = 20,
    timeout: int = 600000
) -> requests.models.Response
```

Poll multiple workflows until all complete or timeout.

**Arguments:**
 - **workflow_ids:**  List of workflow IDs to poll
 - **interval:**  Polling interval in seconds
 - **timeout:**  Maximum timeout in seconds

**Returns:**
> Response with all workflows combined

**Raises:**
 - **IRPValidationError:**  If inputs are invalid
 - **IRPWorkflowError:**  If workflows time out

#### `execute_workflow`

```python
def execute_workflow(
    self,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Union[Dict[str, Any], List[Any], NoneType] = None,
    headers: Dict[str, str] = {},
    timeout: Optional[int] = None,
    stream: bool = False
) -> requests.models.Response
```

Execute workflow: submit request and poll until completion.

This is a convenience method that combines request submission
with automatic workflow polling.

**Arguments:**
 - **method:**  HTTP method (POST, DELETE, etc.)
 - **path:**  API path
 - **params:**  Query parameters
 - **json:**  JSON request body
 - **headers:**  Additional headers
 - **timeout:**  Request timeout in seconds
 - **stream:**  Enable streaming response

**Returns:**
> Final workflow response after completion

**Raises:**
 - **IRPAPIError:**  If request fails
 - **IRPWorkflowError:**  If workflow times out

---

## `irp_integration.edm`

EDM (Exposure Data Management) operations.

Handles datasource creation, duplication, deletion, and associated data retrieval (cedants, LOBs).

### `class EDMManager`

Manager for EDM (Exposure Data Management) operations.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize EDM manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `validate_unique_edms`

```python
def validate_unique_edms(self, edm_names: List[str]) -> None
```

Validate that EDM names are unique (don't already exist).

**Arguments:**
 - **edm_names:**  List of EDM names to validate

**Raises:**
 - **IRPAPIError:**  If any EDM names already exist

#### `submit_create_edm_jobs`

```python
def submit_create_edm_jobs(self, edm_data_list: List[Dict[str, Any]]) -> List[int]
```

Submit multiple EDM creation jobs.

**Arguments:**
 - **edm_data_list:**  List of EDM data dicts, each containing:
   - server_name: str
   - edm_name: str

**Returns:**
> List of job IDs

**Raises:**
 - **IRPValidationError:**  If edm_data_list is empty or invalid
 - **IRPAPIError:**  If EDM creation fails or duplicate names exist

#### `search_database_servers`

```python
def search_database_servers(self, filter: str = '') -> List[Dict[str, Any]]
```

Search database servers.

**Arguments:**
 - **filter:**  Optional filter string for server names

**Returns:**
> List of database server dicts

#### `search_exposure_sets`

```python
def search_exposure_sets(self, filter: str = '') -> List[Dict[str, Any]]
```

Search exposure sets.

**Arguments:**
 - **filter:**  Optional filter string for exposure set names

**Returns:**
> List of exposure set dicts

#### `create_exposure_set`

```python
def create_exposure_set(self, name: str) -> int
```

Create a new exposure set.

**Arguments:**
 - **name:**  Name of the exposure set

**Returns:**
> The exposure set ID

#### `get_or_create_exposure_set`

```python
def get_or_create_exposure_set(self, name: str) -> int
```

Return the ID of the exposure set named ``name``, creating it if none exists.

Risk Modeler permits duplicate exposure set names. When the search
returns more than one exposure set named ``name``, the first is used.

**Arguments:**
 - **name:**  Name of the exposure set

**Returns:**
> The exposure set ID

**Raises:**
 - **IRPValidationError:**  If name is empty
 - **IRPAPIError:**  If the search or the creation fails

#### `search_edms`

```python
def search_edms(
    self,
    filter: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search EDMs (exposures).

**Arguments:**
 - **filter:**  Optional filter string for EDM names
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of EDM dictionaries

#### `search_edms_paginated`

```python
def search_edms_paginated(self, filter: str = '') -> List[Dict[str, Any]]
```

Search all EDMs with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``.

**Arguments:**
 - **filter:**  Optional filter string for EDM names

**Returns:**
> Complete list of all matching EDMs across all pages

**Raises:**
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `submit_create_edm_job`

```python
def submit_create_edm_job(
    self,
    edm_name: str,
    server_name: str = 'databridge-1'
) -> Tuple[int, Dict[str, Any]]
```

Submit job to create a new EDM (exposure).

**Arguments:**
 - **edm_name:**  Name of the EDM
 - **server_name:**  Name of the database server (default: "databridge-1")

**Returns:**
> Tuple of (job_id, request_body) where request_body is the HTTP request payload

#### `submit_upgrade_edm_data_version_jobs`

```python
def submit_upgrade_edm_data_version_jobs(self, edm_data_list: List[Dict[str, Any]]) -> List[int]
```

Submit multiple EDM data version upgrade jobs.

**Arguments:**
 - **edm_data_list:**  List of EDM upgrade data dicts, each containing:
   - edm_name: str
   - edm_version: str

**Returns:**
> List of job IDs

**Raises:**
 - **IRPValidationError:**  If edm_data_list is empty or invalid
 - **IRPAPIError:**  If upgrade submission fails or EDM not found

#### `submit_upgrade_edm_data_version_job`

```python
def submit_upgrade_edm_data_version_job(self, edm_name: str, edm_version: str) -> Tuple[int, Dict[str, Any]]
```

Submit job to upgrade EDM data version.

**Arguments:**
 - **edm_name:**  Name of the EDM to upgrade
 - **edm_version:**  Target EDM data version (e.g., "22")

**Returns:**
> Tuple of (job_id, request_body) where request_body is the HTTP request payload

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If EDM not found or upgrade fails

#### `poll_data_version_upgrade_job_batch_to_completion`

```python
def poll_data_version_upgrade_job_batch_to_completion(
    self,
    job_ids: List[int],
    interval: int = 20,
    timeout: int = 600000
) -> List[Dict[str, Any]]
```

Poll multiple EDM data version upgrade jobs until all complete or timeout.

**Arguments:**
 - **job_ids:**  List of job IDs
 - **interval:**  Polling interval in seconds (default: 20)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> List of final job status details for all jobs

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If jobs time out
 - **IRPAPIError:**  If polling fails

#### `delete_edm`

```python
def delete_edm(self, edm_name: str) -> Dict[str, Any]
```

Delete an EDM and all its associated analyses.

**Arguments:**
 - **edm_name:**  Name of EDM to delete

**Returns:**
> Dict containing final delete job status

**Raises:**
 - **IRPValidationError:**  If edm_name is invalid
 - **IRPAPIError:**  If EDM not found or deletion fails

#### `submit_delete_edm_job`

```python
def submit_delete_edm_job(self, exposure_id: int) -> int
```

Submit job to delete an EDM (exposure).

**Arguments:**
 - **exposure_id:**  ID of the exposure (EDM)

**Returns:**
> The job ID

#### `get_cedants_by_edm`

```python
def get_cedants_by_edm(self, exposure_id: int) -> List[Dict[str, Any]]
```

Retrieve cedants for an EDM.

**Arguments:**
 - **exposure_id:**  Exposure ID

**Returns:**
> List of cedant data

**Raises:**
 - **IRPValidationError:**  If exposure_id is invalid
 - **IRPAPIError:**  If request fails

#### `get_lobs_by_edm`

```python
def get_lobs_by_edm(self, exposure_id: int) -> List[Dict[str, Any]]
```

Retrieve lines of business (LOBs) for an EDM.

**Arguments:**
 - **exposure_id:**  Exposure ID

**Returns:**
> List of LOB dicts

**Raises:**
 - **IRPValidationError:**  If exposure_id is invalid
 - **IRPAPIError:**  If request fails

#### `submit_edm_import_job`

```python
def submit_edm_import_job(
    self,
    edm_name: str,
    edm_file_path: str,
    server_name: str = 'sql-instance-1'
) -> Tuple[int, Dict[str, Any]]
```

Submit EDM import job with S3 file upload.

All cheap, upload-independent checks run before the expensive S3 upload
so the call fails fast (e.g. on a bad server name, exposure-set error,
or duplicate EDM name) without burning a multi-GB transfer.

This method handles the complete EDM import workflow:
1. Look up database server
2. Create or get existing exposure set
3. Validate the EDM name is unique
4. Create import folder (get S3 credentials)
5. Upload the EDM database file to S3
6. Submit import job

The import folder's ``properties.fileExtension`` is read from
``edm_file_path``, so a .bak and a .mdf file are both imported by
passing the path.

**Arguments:**
 - **edm_name:**  Name for the EDM
 - **edm_file_path:**  Path to the .bak or .mdf file to import
 - **server_name:**  Database server name (default: "sql-instance-1")

**Returns:**
> Tuple of (job_id, request_body) where request_body is the HTTP request payload

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPFileError:**  If file upload fails
 - **IRPAPIError:**  If API calls fail

---

## `irp_integration.portfolio`

Portfolio management operations.

Handles portfolio creation, retrieval, and geocoding/hazard operations.

### `class PortfolioManager`

Manager for portfolio operations.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize portfolio manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `get_portfolio_by_id`

```python
def get_portfolio_by_id(self, exposure_id: int, portfolio_id: int) -> Dict[str, Any]
```

Retrieve portfolio details by portfolio ID.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **portfolio_id:**  Portfolio ID

**Returns:**
> Dict containing portfolio details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `get_portfolio_metadata`

```python
def get_portfolio_metadata(self, exposure_id: int, portfolio_id: int) -> Dict[str, Any]
```

Retrieve portfolio metadata by portfolio ID.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **portfolio_id:**  Portfolio ID

**Returns:**
> Dict containing portfolio metadata details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `search_portfolios`

```python
def search_portfolios(
    self,
    exposure_id: int,
    filter: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search portfolios within an exposure.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter string for portfolio names
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of portfolio dictionaries

#### `search_portfolios_paginated`

```python
def search_portfolios_paginated(self, exposure_id: int, filter: str = '') -> List[Dict[str, Any]]
```

Search all portfolios within an exposure with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter string for portfolio names

**Returns:**
> Complete list of all matching portfolios across all pages

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `search_accounts_by_portfolio`

```python
def search_accounts_by_portfolio(
    self,
    exposure_id: int,
    portfolio_id: int,
    filter: str = '',
    sort: str = '',
    limit: Optional[int] = None,
    offset: Optional[int] = None
) -> List[Dict[str, Any]]
```

Retrieve accounts within a portfolio.

Filterable properties are a closed list: accountid (the API's own
spelling), accountName, accountNumber, branchName, cedantName,
ownerName, producerName, underwriterName. Line of business and state are
not among them; see ``search_policies`` and ``search_locations``.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **portfolio_id:**  Portfolio ID
 - **filter:**  Optional filter expression over the properties listed above
 - **sort:**  Optional comma-delimited sort properties, each optionally
   suffixed with ASC or DESC
 - **limit:**  Maximum results per page; omitted from the request when
   None, since the API does not declare limit/offset for this
   operation (default: None)
 - **offset:**  Offset for pagination; omitted from the request when None
   (default: None)

**Returns:**
> List of account dicts

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `search_accounts_by_portfolio_paginated`

```python
def search_accounts_by_portfolio_paginated(
    self,
    exposure_id: int,
    portfolio_id: int,
    filter: str = '',
    sort: str = ''
) -> List[Dict[str, Any]]
```

Retrieve all accounts within a portfolio with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``. This operation does not declare limit/offset, so a
response larger than the requested limit means it ignored them and was
already complete; ``paginate_search`` returns that response as-is.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **portfolio_id:**  Portfolio ID
 - **filter:**  Optional filter expression; see
   ``search_accounts_by_portfolio`` for supported properties
 - **sort:**  Optional comma-delimited sort properties

**Returns:**
> Complete list of all matching accounts across all pages

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `search_accounts`

```python
def search_accounts(
    self,
    exposure_id: int,
    filter: str = '',
    sort: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search accounts across an entire EDM.

Wider filter surface than the portfolio-scoped
``search_accounts_by_portfolio``: adds locationsCount, tagIds,
policyExpirationDate, stampDate, reportsCount, resultsCount, userId1-4
and userText1-2 to the account properties.

Filter grammar: ``property <operator> value``, where operator is one of
=, !=, >, <, >=, <=, LIKE, NOT LIKE, IN, NOT IN, IS NULL, IS NOT NULL,
combined with AND/OR. String literals are double-quoted, IN lists are
parenthesised, and ``*`` is the wildcard. List operators are not
supported on YYYY-MM-DD properties; use range comparisons instead.

Line of business and state are not filterable here. Use
``search_policies`` (LOB) and ``search_locations`` (admin1Code) for
those selections and join back on accountId.

The API's ``allowDeepFilters`` parameter is not exposed and is always
sent as false. It widens the accepted filter set — ``lobName`` and
``admin1Code`` stop returning 400 — but the widened filters then lie:
on one EDM they returned zero rows with HTTP 200 where Data Bridge and
``search_locations`` both counted 272 accounts. An empty result is
legitimate for a valid filter, so a caller cannot tell the two apart.

This operation cannot be scoped to a portfolio: ``portfolioId``,
``portfolioName``, ``portInfoId``, ``portfolio`` and
``portfolioNumber`` are all rejected. Listing account IDs is the only
route, which meets the URL length limit described on
``search_policies``.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter expression over the account properties
 - **sort:**  Optional comma-delimited sort properties, each optionally
   suffixed with ASC or DESC
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of account dicts

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `search_accounts_paginated`

```python
def search_accounts_paginated(
    self,
    exposure_id: int,
    filter: str = '',
    sort: str = ''
) -> List[Dict[str, Any]]
```

Search all accounts within an EDM with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``, which documents what the API's ``offset`` counts
and the guards around that.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter expression; see ``search_accounts`` for the
   supported properties and grammar
 - **sort:**  Optional comma-delimited sort properties

**Returns:**
> Complete list of all matching accounts across all pages

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `search_policies`

```python
def search_policies(
    self,
    exposure_id: int,
    filter: str = '',
    sort: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search policies across an entire EDM.

This is how a sub-portfolio breakout selects accounts by line of
business. LOB is not filterable on any operation, but every policy
carries both its account and its LOB, so scope the search to a
portfolio's accounts and group the results client-side::

    accounts = pm.search_accounts_by_portfolio_paginated(edm_id, portfolio_id)
    ids = ','.join(str(account['accountId']) for account in accounts)
    policies = pm.search_policies_paginated(edm_id, filter=f'accountId IN ({ids})')

    by_lob: Dict[str, set] = {}
    for policy in policies:
        by_lob.setdefault(policy['lob']['lobName'], set()).add(policy['accountId'])

Two traps in that loop. ``accountId`` is flat but ``lobName`` is nested
under ``lob``, and reading the wrong key yields an empty grouping rather
than an error. And an account writing three lines of business lands in
all three sub-portfolios, because portfolios hold whole accounts.

``lobId`` is sortable but not filterable, and filtering on it returns
HTTP 500, not 400 — do not retry that 500 as transient.

Chunking a long ``accountId IN (...)`` list is the caller's job.
``filter`` travels in the URL and the server answers HTTP 431 past
roughly 4,870 characters — budget in characters, not IDs, since
seven-digit IDs fit about half as many as four-digit ones, and other
predicates spend the same budget. 431 is a header limit that the bearer
token shares, so treat the figure as this deployment's, not the API's.
The alternative is to omit the filter and intersect against the
portfolio's accounts client-side.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter expression; see ``search_accounts`` for the
   grammar and the searchPolicies reference for the filterable
   properties
 - **sort:**  Optional comma-delimited sort properties, each optionally
   suffixed with ASC or DESC
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of policy dicts, each carrying accountId and a nested
> lob dict of {lobId, lobName, uri}

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `search_policies_paginated`

```python
def search_policies_paginated(
    self,
    exposure_id: int,
    filter: str = '',
    sort: str = ''
) -> List[Dict[str, Any]]
```

Search all policies within an EDM with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter expression; see ``search_policies`` for the
   supported properties
 - **sort:**  Optional comma-delimited sort properties

**Returns:**
> Complete list of all matching policies across all pages

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `search_locations`

```python
def search_locations(
    self,
    exposure_id: int,
    filter: str = '',
    sort: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search locations across an entire EDM.

This is how a sub-portfolio breakout selects accounts by state or
province, and unlike the LOB case it resolves server-side: admin1Code is
filterable alongside accountId, so one query returns the locations in a
state and their accounts are the accounts to add::

    filter = f'accountId IN ({ids}) AND admin1Code = "FL"'
    locations = pm.search_locations_paginated(edm_id, filter=filter)
    account_ids = {
        location['location']['property']['accountId'] for location in locations
    }

admin1 is the first-level administrative division, so non-US provinces
and regions use the same attribute. admin1Code is not always a
two-letter abbreviation — some EDMs carry numeric codes — so treat it as
an opaque string rather than constructing codes.

**Filter on admin1Code, not admin1Name.** admin1Name is a geocoding
output: it is empty on every location until GeoHaz runs, and filtering
on it before that returns zero rows with HTTP 200. A state selection
built on admin1Name produces empty sub-portfolios and reports success.

**A partial match admits the whole account.** An account with locations
in three states lands in all three state sub-portfolios, so the
sub-portfolios overlap and their TIV sums to more than the source
portfolio's. Platform behavior, not something this method can filter
around; how much it inflates depends on the book.

Results are nested, and reading the wrong key returns a plausible empty
result rather than an error. Each item is {location,
propertyReference}::

    row["location"]["property"]["accountId"]
    row["location"]["property"]["locationId"]
    row["location"]["address"]["admin1Code"]

The URL-length limit on ``search_policies`` applies here too, and this
operation meets it first: scoping a state selection to a source
portfolio means listing that portfolio's account IDs, since no
portfolio predicate is accepted anywhere (see ``search_accounts``).

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter expression; see ``search_accounts`` for the
   grammar and the searchLocations reference for the filterable
   properties
 - **sort:**  Optional comma-delimited sort properties, each optionally
   suffixed with ASC or DESC
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of location search item dicts, each wrapping a nested
> location dict

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `search_locations_paginated`

```python
def search_locations_paginated(
    self,
    exposure_id: int,
    filter: str = '',
    sort: str = ''
) -> List[Dict[str, Any]]
```

Search all locations within an EDM with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``. Because that helper detects progress by hashing
page content, it does not depend on this operation's nested response
shape being what the spec describes.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter expression; see ``search_locations`` for
   the supported properties
 - **sort:**  Optional comma-delimited sort properties

**Returns:**
> Complete list of all matching location search items across all
> pages

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `add_filtered_accounts`

```python
def add_filtered_accounts(
    self,
    exposure_id: int,
    portfolio_id: int,
    *,
    marked_accounts: Optional[List[int]] = None,
    query_filter: str = '',
    select_all: bool = False,
    manage_existing_accounts: bool = False
) -> Dict[str, Any]
```

Add accounts to a portfolio by ID list, by query filter, or both.

Wraps the synchronous ``manageFilteredAccounts`` operation. HTTP 200 is
its only success status and it declares no response body, so an empty
dict is returned on success; any other 2xx raises rather than being
polled as a workflow.

Prefer ``manage_portfolio_accounts`` for populating a portfolio from an
account-ID list. This operation writes correctly and is idempotent, but
it returns an empty object, so a caller cannot tell a populate that
added everything from one that added nothing without reading the
portfolio back.

``selectAll`` adds every account matched by ``queryFilter`` — every
account in the EDM when no filter is given — and overrides
``markedAccounts``. ``manageExistingAccounts`` is a mode switch, not an
upsert flag: it discards ``markedAccounts`` and ``queryFilter``, so it
cannot add anything. Combining it with either is refused here rather
than sent, since the API answers 200 and adds nothing. The grammar of
``queryFilter`` is undocumented; it is transported verbatim.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **portfolio_id:**  Portfolio ID
 - **marked_accounts:**  Account IDs to add (default: None)
 - **query_filter:**  Expression selecting the accounts to add, passed
   through unmodified (default: "")
 - **select_all:**  Add every account matched by query_filter, or every
   account in the EDM when query_filter is empty. Overrides
   marked_accounts (default: False)
 - **manage_existing_accounts:**  Restrict the operation to accounts
   already in the portfolio. Discards marked_accounts and
   query_filter, so it cannot be combined with either
   (default: False)

**Returns:**
> Parsed response body, or an empty dict when the response has none

**Raises:**
 - **IRPValidationError:**  If parameters are invalid, if no accounts were
   selected by any means, or if manage_existing_accounts is
   combined with a selection it would discard
 - **IRPAPIError:**  If the request fails or returns an unexpected status

#### `manage_portfolio_accounts`

```python
def manage_portfolio_accounts(
    self,
    exposure_id: int,
    portfolio_id: int,
    *,
    accounts_to_add: Optional[List[int]] = None,
    accounts_to_remove: Optional[List[int]] = None
) -> Dict[str, Any]
```

Add and/or remove accounts on a portfolio by account ID.

Wraps the synchronous ``managePortfolioAccounts`` operation. Unlike
``add_filtered_accounts`` it reports what it did, which makes it the
populate path for a sub-portfolio built from a known list of account
IDs::

    {"addAccounts": {"completed": 4, "total": 4},
     "removeAccounts": {"completed": 0, "total": 0}}

``completed`` counts IDs newly added, not IDs that ended up as members,
so ``completed < total`` means "already present", not "failed". The call
is idempotent, and a caller treating ``completed < total`` as an error
will fail every healthy re-run. To confirm what a portfolio holds, read
it back and compare against the intended ID list. The counts are logged;
nothing here raises on a partial result.

HTTP 200 is the only success status; any other 2xx raises. The API
documents 403 as the caller lacking the "Edit Portfolios" action.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **portfolio_id:**  Portfolio ID
 - **accounts_to_add:**  Account IDs to add to the portfolio
   (default: None)
 - **accounts_to_remove:**  Account IDs to remove from the portfolio
   (default: None)

**Returns:**
> Dict of add/remove counts as shown above, or an empty dict when the
> response has no body

**Raises:**
 - **IRPValidationError:**  If parameters are invalid, or if both account
   lists are empty
 - **IRPAPIError:**  If the request fails or returns an unexpected status

#### `create_portfolios`

```python
def create_portfolios(self, portfolio_data_list: List[Dict[str, Any]]) -> List[int]
```

Create multiple portfolios.

**Arguments:**
 - **portfolio_data_list:**  List of portfolio data dicts, each containing:
   - edm_name: str
   - portfolio_name: str (at most 40 characters)
   - portfolio_number: str (at most 20 characters)
   - description: str

**Returns:**
> List of portfolio IDs

**Raises:**
 - **IRPValidationError:**  If portfolio_data_list is empty or invalid, if a
   name field exceeds its length limit, or if any portfolio name is
   already taken in its EDM
 - **IRPAPIError:**  If portfolio creation fails

#### `create_portfolio`

```python
def create_portfolio(
    self,
    edm_name: str,
    portfolio_name: str,
    portfolio_number: str = '',
    description: str = ''
) -> Tuple[int, Dict[str, Any]]
```

Create new portfolio in EDM.

Portfolio names must be unique within the EDM, enforced client-side: the
name is looked up first and a match raises ``IRPValidationError`` with no
POST sent. A caller adopting an existing portfolio by name should call
``search_portfolios`` itself rather than catching that.

``portfolioName`` is capped at 40 characters server-side and
``portfolioNumber`` at 20. Both are validated before any request is sent,
and neither is truncated — a shortened value can collide two distinct
inputs into one identifier.

Because ``portfolio_number`` defaults to ``portfolio_name``, a name over
20 characters requires an explicit ``portfolio_number``. A
``{source portfolio} - {breakout value}`` naming scheme hits this
immediately.

**Arguments:**
 - **edm_name:**  Name of EDM datasource
 - **portfolio_name:**  Name for new portfolio, at most 40 characters
 - **portfolio_number:**  Portfolio number, at most 20 characters; defaults
   to portfolio_name when empty, which then has to be within that
   limit too (default: "")
 - **description:**  Portfolio description; an auto-generated description is
   used when empty (default: "")

**Returns:**
> Tuple of (portfolio_id, request_body) where request_body is the HTTP request payload

**Raises:**
 - **IRPValidationError:**  If inputs are invalid, if either name field
   exceeds its length limit, or if a portfolio with this name
   already exists in the EDM
 - **IRPAPIError:**  If the EDM lookup or the creation request fails

#### `submit_geohaz_jobs`

```python
def submit_geohaz_jobs(self, geohaz_data_list: List[Dict[str, Any]]) -> List[int]
```

Submit multiple geohaz jobs (geocoding and hazard operations).

**Arguments:**
 - **geohaz_data_list:**  List of geohaz data dicts, each containing:
   - edm_name: str
   - portfolio_name: str
   - layers: list of GeoHaz layer dictionaries

**Returns:**
> List of job IDs

**Raises:**
 - **IRPValidationError:**  If geohaz_data_list is empty or invalid
 - **IRPAPIError:**  If job submission fails or resources not found

#### `submit_geohaz_job`

```python
def submit_geohaz_job(
    self,
    portfolio_name: str,
    edm_name: str,
    layers: List[Dict[str, Any]]
) -> Tuple[int, Dict[str, Any]]
```

Execute geocoding and/or hazard operations on portfolio.

The returned job ID is served by ``/platform/geohaz/v1/jobs``, so poll it
with ``poll_geohaz_job_to_completion`` (or ``get_geohaz_job`` for one
status check), not with ``import_job.poll_import_job_to_completion``,
which answers ``404 Invalid job id`` for a GeoHaz job that is running
normally.

Geocoding populates each location's ``admin1Name``, which arrives empty
from an MRI import; see ``search_locations``.

**Arguments:**
 - **portfolio_name:**  Name of the portfolio
 - **edm_name:**  Name of the EDM containing the portfolio
 - **layers:**  Non-empty list of geocode and/or hazard layer dictionaries.
   Every layer specifies ``type``, ``name``, ``engineType``,
   ``version``, and ``layerOptions``. Geocode options must include
   ``geoLicenseType``, ``aggregateTriggerEnabled``, and
   ``skipPrevGeocoded``. Hazard options must include
   ``overrideUserDef`` and ``skipPrevHazard``.

**Returns:**
> Tuple of (job_id, request_body), where job_id is a GeoHaz job ID and
> request_body is the HTTP request payload

**Raises:**
 - **IRPValidationError:**  If inputs are invalid
 - **IRPAPIError:**  If workflow fails or times out

#### `get_geohaz_job`

```python
def get_geohaz_job(self, job_id: int) -> Dict[str, Any]
```

Retrieve geohaz job status by job ID.

**Arguments:**
 - **job_id:**  Job ID

**Returns:**
> Dict containing job status details

**Raises:**
 - **IRPValidationError:**  If job_id is invalid
 - **IRPAPIError:**  If request fails

#### `poll_geohaz_job_to_completion`

```python
def poll_geohaz_job_to_completion(
    self,
    job_id: int,
    interval: int = 10,
    timeout: int = 600000
) -> Dict[str, Any]
```

Poll geohaz job until completion or timeout.

**Arguments:**
 - **job_id:**  Job ID
 - **interval:**  Polling interval in seconds (default: 10)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> Final job status details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If job times out
 - **IRPAPIError:**  If polling fails

#### `poll_geohaz_job_batch_to_completion`

```python
def poll_geohaz_job_batch_to_completion(
    self,
    job_ids: List[int],
    interval: int = 20,
    timeout: int = 600000
) -> List[Dict[str, Any]]
```

Poll multiple geohaz jobs until all complete or timeout.

**Arguments:**
 - **job_ids:**  List of job IDs
 - **interval:**  Polling interval in seconds (default: 20)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> List of final job status details for all jobs

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If jobs time out
 - **IRPAPIError:**  If polling fails

---

## `irp_integration.mri_import`

MRI Import Manager for IRP Integration.

Handles Multi-Risk Insurance (MRI) data imports via the Platform Import API. Files are uploaded to S3 and import jobs are submitted through the /platform/import/v1 endpoints.

### `class MRIImportManager`

Manager for MRI import operations.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize MRI Import Manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `submit_mri_import_job`

```python
def submit_mri_import_job(
    self,
    edm_name: str,
    portfolio_name: str,
    accounts_file_path: str,
    locations_file_path: str,
    mapping_file_path: Optional[str] = None,
    delimiter: str = 'TAB'
) -> Tuple[int, Dict[str, Any]]
```

Submit an MRI import job via the Platform Import API.

This method handles the complete MRI import workflow:
1. Look up EDM and portfolio
2. Create import folder (get S3 credentials)
3. Upload accounts, locations, and optionally mapping files to S3
4. Submit import job

**Arguments:**
 - **edm_name:**  Target EDM name
 - **portfolio_name:**  Target portfolio name within the EDM
 - **accounts_file_path:**  Path to accounts CSV file
 - **locations_file_path:**  Path to locations CSV file
 - **mapping_file_path:**  Optional path to .mff mapping file
 - **delimiter:**  File delimiter (default: "TAB")

**Returns:**
> Tuple of (job_id, request_body) where request_body is the HTTP request payload

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPFileError:**  If file upload fails
 - **IRPAPIError:**  If any API call fails

---

## `irp_integration.treaty`

Treaty Manager for IRP Integration.

Handles treaty-related operations including creation, retrieval, and Line of Business (LOB) assignments.

### `class TreatyManager`

Manager for treaty operations.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize Treaty Manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `search_treaties`

```python
def search_treaties(
    self,
    exposure_id: int,
    filter: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search treaties for a given exposure ID.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter string
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of treaty dictionaries

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If API request fails

#### `search_treaties_paginated`

```python
def search_treaties_paginated(self, exposure_id: int, filter: str = '') -> List[Dict[str, Any]]
```

Search all treaties for a given exposure ID with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **filter:**  Optional filter string

**Returns:**
> Complete list of all matching treaties across all pages

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `create_treaties`

```python
def create_treaties(self, treaty_data_list: List[Dict[str, Any]]) -> List[int]
```

Create multiple treaties.

**Arguments:**
 - **treaty_data_list:**  List of treaty data dicts, each containing all required treaty fields

**Returns:**
> List of treaty IDs

**Raises:**
 - **IRPValidationError:**  If treaty_data_list is empty or invalid
 - **IRPAPIError:**  If treaty creation fails or EDM not found

#### `create_treaty`

```python
def create_treaty(
    self,
    edm_name: str,
    treaty_name: str,
    treaty_number: str,
    treaty_type: str,
    per_risk_limit: float,
    occurrence_limit: float,
    attachment_point: float,
    inception_date: str,
    expiration_date: str,
    currency_name: str,
    attachment_basis: str,
    attachment_level: str,
    pct_covered: float,
    pct_placed: float,
    pct_share: float,
    pct_retention: float,
    premium: float,
    num_reinstatements: int,
    pct_reinstatement_charge: float,
    aggregate_limit: float,
    aggregate_deductible: float,
    priority: int
) -> Tuple[int, Dict[str, Any]]
```

Create a treaty with provided parameters.

**Arguments:**
 - **edm_name:**  EDM name to create the treaty in
 - **treaty_name:**  Treaty name
 - **treaty_number:**  Treaty number (max 20 chars)
 - **treaty_type:**  Treaty type (must be in TREATY_TYPES)
 - **per_risk_limit:**  Per risk limit amount
 - **occurrence_limit:**  Occurrence limit amount
 - **attachment_point:**  Attachment point amount
 - **inception_date:**  Inception date (ISO format)
 - **expiration_date:**  Expiration date (ISO format)
 - **currency_name:**  Currency name (e.g., "US Dollar")
 - **attachment_basis:**  Attachment basis (must be in TREATY_ATTACHMENT_BASES)
 - **attachment_level:**  Attachment level (must be in TREATY_ATTACHMENT_LEVELS)
 - **pct_covered:**  Percent covered
 - **pct_placed:**  Percent placed
 - **pct_share:**  Percent share
 - **pct_retention:**  Percent retention
 - **premium:**  Premium amount
 - **num_reinstatements:**  Number of reinstatements
 - **pct_reinstatement_charge:**  Percent reinstatement charge
 - **aggregate_limit:**  Aggregate limit amount
 - **aggregate_deductible:**  Aggregate deductible amount
 - **priority:**  Priority

**Returns:**
> Tuple of (treaty_id, request_body) where request_body is the HTTP request payload

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If treaty creation fails or EDM not found

#### `create_treaty_lob`

```python
def create_treaty_lob(self, exposure_id: int, treaty_id: int, lob_id: int, lobName: str) -> int
```

Create a Line of Business (LOB) for a treaty.

**Arguments:**
 - **exposure_id:**  Exposure ID
 - **treaty_id:**  Treaty ID
 - **lob_id:**  LOB ID
 - **lobName:**  LOB Name

**Returns:**
> LOB ID of the created LOB

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If LOB creation fails

---

## `irp_integration.analysis`

Analysis management operations.

Handles portfolio analysis submission, job tracking, and result retrieval.

### `class AppliedTreaty`

One treaty as applied to one analysis, with the name Risk Modeler shows.

``terms`` are the analysis-level loss-affecting values, normalized the way ``GroupingManager`` normalizes them for its treaty comparison: what the analysis ran with, not the EDM treaty definition. An analysis run in CAD against a treaty defined in USD reports CAD.

``treaty_number`` is None for a treaty carrying no ``treatyNumber``. The treaty is still reported, and ``RunDescription.problems`` carries a ``treaty_number_missing`` problem naming its ``treatyId``.

``terms`` is a ``Dict``, so ``AppliedTreaty`` is not hashable despite ``frozen=True``, and its contents stay mutable.

#### `__init__`

```python
def __init__(
    self,
    treaty_id: Optional[int],
    treaty_number: Optional[str],
    treaty_name: Optional[str],
    terms: Dict[str, Any]
)
```

### `class RunDescription`

What one analysis ran with: regions, event-rate scheme names, treaties.

``regions`` holds one ``GroupingRegionFact`` per region row the Platform returned, uncollapsed: a windstorm analysis covering 23 sub-regions reports 23 regions. A row whose framework, engine, peril, region, or model version did not resolve is absent from ``regions`` and reported in ``problems``.

``regions`` and ``problems`` do not partition the rows, so do not reconcile ``len(regions) + len(problems)`` against the Platform's row count. A row that is kept is still reported when it raises ``pet_id_missing``, ``pet_periods_missing``, ``apply_contract_flag_unsupported`` or ``event_rate_scheme_missing``, and one row can raise more than one of them. ``GroupingProblem.sub_regions`` names the row a row-level problem concerns. A ``treaty_number_missing`` problem concerns no row at all.

``event_rate_scheme_names`` names every ``event_rate_scheme_id`` in ``regions`` that an active Risk Modeler event-rate scheme row resolves; an ID no active row carries is absent.

``event_rate_scheme_names`` is a ``Mapping``, so ``RunDescription`` is not hashable despite ``frozen=True`` and its contents stay mutable. That is the existing pattern here rather than something ``describe_run`` introduced: ``GroupingTreaty.terms`` is a ``Dict``, so ``GroupingTreaty`` and any ``GroupingProblem`` carrying one are unhashable too.

#### `__init__`

```python
def __init__(
    self,
    analysis_id: int,
    is_group: bool,
    regions: Tuple[irp_integration.grouping.GroupingRegionFact, ...],
    event_rate_scheme_names: Mapping[int, str],
    treaties: Tuple[irp_integration.analysis.AppliedTreaty, ...],
    problems: Tuple[irp_integration.grouping.GroupingProblem, ...]
)
```

### `class AnalysisManager`

Manager for analysis operations.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize analysis manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `get_analysis_by_id`

```python
def get_analysis_by_id(self, analysis_id: int) -> Dict[str, Any]
```

Retrieve analysis by ID.

**Arguments:**
 - **analysis_id:**  Analysis ID

**Returns:**
> Dict containing analysis details

**Raises:**
 - **IRPValidationError:**  If analysis_id is invalid
 - **IRPAPIError:**  If request fails

#### `submit_portfolio_analysis_jobs`

```python
def submit_portfolio_analysis_jobs(self, analysis_data_list: List[Dict[str, Any]]) -> List[int]
```

Submit multiple portfolio analysis jobs.

Each dict names the EDM, portfolio and job, then identifies the model
profile, output profile and event rate scheme either by name or by
Risk Modeler id. The two are all-or-nothing per dict, as for
``submit_portfolio_analysis_job``.

**Arguments:**
 - **analysis_data_list:**  List of analysis job data dicts, each containing:
   - edm_name: str
   - portfolio_name: str
   - job_name: str
   - treaty_names: List[str], optional (defaults to [])
   - tag_names: List[str], optional (defaults to [])

   and, on the name path:

   - analysis_profile_name: str
   - output_profile_name: str
   - event_rate_scheme_name: str, optional

   or, on the id path:

   - model_profile_id: int
   - output_profile_id: int
   - event_rate_scheme_id: int, required for ``'DLM'``, optional for ``'HD'``
   - analysis_type: str, ``'DLM'`` or ``'HD'``

**Returns:**
> List of job IDs

**Raises:**
 - **IRPValidationError:**  If analysis_data_list is empty, if a dict mixes
   names with ids, if a dict gives neither the profile names
   nor the profile ids and analysis_type, or if a dict gives
   analysis_type 'DLM' without event_rate_scheme_id
 - **IRPAPIError:**  If analysis submission fails, duplicate analysis names
   exist, or a dict is missing edm_name, portfolio_name or job_name

#### `submit_portfolio_analysis_job`

```python
def submit_portfolio_analysis_job(
    self,
    edm_name: str,
    portfolio_name: str,
    job_name: str,
    analysis_profile_name: Optional[str] = None,
    output_profile_name: Optional[str] = None,
    event_rate_scheme_name: Optional[str] = None,
    treaty_names: Optional[List[str]] = None,
    tag_names: Optional[List[str]] = None,
    currency: Optional[Dict[str, str]] = None,
    skip_duplicate_check: bool = False,
    franchise_deductible: bool = False,
    min_loss_threshold: float = 1.0,
    treat_construction_occupancy_as_unknown: bool = True,
    num_max_loss_event: int = 1,
    model_profile_id: Optional[int] = None,
    output_profile_id: Optional[int] = None,
    event_rate_scheme_id: Optional[int] = None,
    analysis_type: Optional[str] = None
) -> Tuple[int, Dict[str, Any]]
```

Submit portfolio analysis job (submits but doesn't wait).

The model profile, output profile and event rate scheme are identified
either by name or by Risk Modeler id, never both. On the name path the
method resolves each name through ``reference_data`` and derives the job
``type`` from the model profile's ``softwareVersionCode``. On the id path
it posts ``model_profile_id``, ``output_profile_id`` and
``event_rate_scheme_id`` as given with ``analysis_type`` as the job
``type``; no reference-data request is made and the peril/region check
does not run. A ``'DLM'`` job still requires ``event_rate_scheme_id``.

A name-path argument is any of ``analysis_profile_name``,
``output_profile_name`` or ``event_rate_scheme_name`` that is not
``None``; ``event_rate_scheme_name=""`` counts. An id-path argument is
any of ``model_profile_id``, ``output_profile_id``,
``event_rate_scheme_id`` or ``analysis_type`` that is not ``None``.
Giving both kinds raises ``IRPValidationError``. On the id path, "no
event rate scheme" is expressed by leaving ``event_rate_scheme_id`` as
``None``.

**Arguments:**
 - **edm_name:**  Name of the EDM (exposure database)
 - **portfolio_name:**  Name of the portfolio to analyze
 - **job_name:**  Name for analysis job (must be unique)
 - **analysis_profile_name:**  Name path. Model profile name
 - **output_profile_name:**  Name path. Output profile name
 - **event_rate_scheme_name:**  Name path. Event rate scheme name (required
   for DLM, optional for HD)
 - **treaty_names:**  List of treaty names to apply. ``None`` or an empty list
   submits the analysis with no treaties applied (treatyIds is sent as [])
 - **tag_names:**  List of tag names to apply. ``None`` or an empty list submits
   the analysis with no tags applied (tagIds is sent as [])
 - **currency:**  Optional currency configuration
 - **skip_duplicate_check:**  Skip checking if analysis name already exists (for batch operations)
 - **franchise_deductible:**  Whether to apply franchise deductible (default: False)
 - **min_loss_threshold:**  Minimum loss threshold value (default: 1.0)
 - **treat_construction_occupancy_as_unknown:**  Treat construction/occupancy as unknown (default: True)
 - **num_max_loss_event:**  Number of max loss events to include (default: 1)
 - **model_profile_id:**  Id path. Model profile id, posted as ``modelProfileId``
 - **output_profile_id:**  Id path. Output profile id, posted as ``outputProfileId``
 - **event_rate_scheme_id:**  Id path. Event rate scheme id, posted as
   ``eventRateSchemeId`` when given. Required when ``analysis_type``
   is ``'DLM'``, optional for ``'HD'``
 - **analysis_type:**  Id path. Job ``type``, ``'DLM'`` or ``'HD'``; see
   ANALYSIS_TYPES in constants.py. Required with ``model_profile_id``

**Returns:**
> Tuple of (job_id, request_body) where request_body is the HTTP request payload

**Raises:**
 - **IRPValidationError:**  If inputs are invalid; if name-path and id-path
   arguments are mixed; if neither the two profile names nor
   ``model_profile_id``, ``output_profile_id`` and ``analysis_type``
   are given; if ``analysis_type`` is not in ANALYSIS_TYPES; or if
   ``analysis_type`` is ``'DLM'`` and ``event_rate_scheme_id`` is
   ``None``
 - **IRPAPIError:**  If request fails or EDM/portfolio not found
 - **IRPReferenceDataError:**  Name path only. If a profile, tag, or event
   rate scheme cannot be resolved; if the model profile is DLM and
   no event rate scheme name was given; or if the event rate
   scheme's perilCode and modelRegionCode do not match the model
   profile's

#### `get_analysis_job`

```python
def get_analysis_job(self, job_id: int) -> Dict[str, Any]
```

Retrieve analysis job status by job ID.

**Arguments:**
 - **job_id:**  Job ID

**Returns:**
> Dict containing job status details

**Raises:**
 - **IRPValidationError:**  If job_id is invalid
 - **IRPAPIError:**  If request fails

#### `poll_analysis_job_to_completion`

```python
def poll_analysis_job_to_completion(
    self,
    job_id: int,
    interval: int = 10,
    timeout: int = 600000
) -> Dict[str, Any]
```

Poll analysis job until completion or timeout.

**Arguments:**
 - **job_id:**  Job ID
 - **interval:**  Polling interval in seconds (default: 10)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> Final job status details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If job times out
 - **IRPAPIError:**  If polling fails

#### `search_analysis_jobs`

```python
def search_analysis_jobs(
    self,
    filter: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search analysis jobs with optional filtering.

**Arguments:**
 - **filter:**  Optional filter string (default: "")
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of analysis job dicts

**Raises:**
 - **IRPAPIError:**  If search fails

#### `poll_analysis_job_batch_to_completion`

```python
def poll_analysis_job_batch_to_completion(
    self,
    job_ids: List[int],
    interval: int = 20,
    timeout: int = 600000
) -> List[Dict[str, Any]]
```

Poll multiple analysis jobs until all complete or timeout.

**Arguments:**
 - **job_ids:**  List of job IDs
 - **interval:**  Polling interval in seconds (default: 20)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> List of final job status details for all jobs

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If jobs time out
 - **IRPAPIError:**  If polling fails

#### `search_analyses`

```python
def search_analyses(
    self,
    filter: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search analysis results with optional filtering.

**Arguments:**
 - **filter:**  Optional filter string (default: "")
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of analysis result dicts

**Raises:**
 - **IRPAPIError:**  If search fails

#### `search_analyses_paginated`

```python
def search_analyses_paginated(self, filter: str = '') -> List[Dict[str, Any]]
```

Search all analysis results with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``.

**Arguments:**
 - **filter:**  Optional filter string (default: "")

**Returns:**
> Complete list of all matching analysis results across all pages

**Raises:**
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `get_analysis_by_name`

```python
def get_analysis_by_name(self, analysis_name: str, edm_name: str) -> Dict[str, Any]
```

Get an analysis by name and EDM name.

**Arguments:**
 - **analysis_name:**  Name of the analysis
 - **edm_name:**  Name of the EDM (exposure database)

**Returns:**
> Dict containing analysis details

**Raises:**
 - **IRPValidationError:**  If inputs are invalid
 - **IRPAPIError:**  If analysis not found or multiple matches

#### `delete_analysis`

```python
def delete_analysis(self, analysis_id: int) -> None
```

Delete an analysis by ID.

**Arguments:**
 - **analysis_id:**  Analysis ID to delete

**Raises:**
 - **IRPValidationError:**  If analysis_id is invalid
 - **IRPAPIError:**  If deletion fails

#### `get_analysis_by_app_analysis_id`

```python
def get_analysis_by_app_analysis_id(self, app_analysis_id: int) -> Dict[str, Any]
```

Retrieve analysis by appAnalysisId (the ID used in the application/UI).

**Arguments:**
 - **app_analysis_id:**  Application analysis ID (e.g., 35810)

**Returns:**
> Dict containing analysisId and exposureResourceId

**Raises:**
 - **IRPValidationError:**  If app_analysis_id is invalid
 - **IRPAPIError:**  If request fails or analysis not found

#### `get_elt`

```python
def get_elt(
    self,
    analysis_id: int,
    perspective_code: str,
    exposure_resource_id: int,
    filter: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    *,
    exposure_resource_type: str = 'PORTFOLIO'
) -> List[Dict[str, Any]]
```

Retrieve Event Loss Table (ELT) for an analysis.

**Arguments:**
 - **analysis_id:**  Analysis ID
 - **perspective_code:**  Risk Modeler financial perspective code
   (e.g. 'GU', 'GR', 'RL', 'WX', 'QS'). See PERSPECTIVE_CODES
   in constants.py for the full set.
 - **exposure_resource_id:**  Exposure resource ID: the analysis's portfolio ID
   when exposure_resource_type is 'PORTFOLIO', or one treaty ID from
   search_analysis_treaties when it is 'TREATY'
 - **exposure_resource_type:**  'PORTFOLIO' (default) or 'TREATY'. See
   EXPOSURE_RESOURCE_TYPES in constants.py. A treaty that took no
   loss at the perspective answers an empty list.
 - **filter:**  Optional filter string (e.g., "eventId IN (1, 2, 3)" or "eventId = 123")
 - **limit:**  Optional maximum number of records to return
 - **offset:**  Optional number of records to skip (for pagination)

**Returns:**
> List of ELT records containing eventId, positionValue, stdDevI, stdDevC, etc.

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `get_ep`

```python
def get_ep(
    self,
    analysis_id: int,
    perspective_code: str,
    exposure_resource_id: int,
    *,
    exposure_resource_type: str = 'PORTFOLIO'
) -> List[Dict[str, Any]]
```

Retrieve EP (Exceedance Probability) metrics for an analysis.

**Arguments:**
 - **analysis_id:**  Analysis ID
 - **perspective_code:**  Risk Modeler financial perspective code
   (e.g. 'GU', 'GR', 'RL', 'WX', 'QS'). See PERSPECTIVE_CODES
   in constants.py for the full set.
 - **exposure_resource_id:**  Exposure resource ID: the analysis's portfolio ID
   when exposure_resource_type is 'PORTFOLIO', or one treaty ID from
   search_analysis_treaties when it is 'TREATY'
 - **exposure_resource_type:**  'PORTFOLIO' (default) or 'TREATY'. See
   EXPOSURE_RESOURCE_TYPES in constants.py. A treaty that took no
   loss at the perspective answers an empty list.

**Returns:**
> List of EP curve data (OEP, AEP, CEP, TCE curves)

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `get_stats`

```python
def get_stats(
    self,
    analysis_id: int,
    perspective_code: str,
    exposure_resource_id: int,
    *,
    exposure_resource_type: str = 'PORTFOLIO'
) -> List[Dict[str, Any]]
```

Retrieve statistics for an analysis.

**Arguments:**
 - **analysis_id:**  Analysis ID
 - **perspective_code:**  Risk Modeler financial perspective code
   (e.g. 'GU', 'GR', 'RL', 'WX', 'QS'). See PERSPECTIVE_CODES
   in constants.py for the full set.
 - **exposure_resource_id:**  Exposure resource ID: the analysis's portfolio ID
   when exposure_resource_type is 'PORTFOLIO', or one treaty ID from
   search_analysis_treaties when it is 'TREATY'
 - **exposure_resource_type:**  'PORTFOLIO' (default) or 'TREATY'. See
   EXPOSURE_RESOURCE_TYPES in constants.py. A treaty that took no
   loss at the perspective answers an empty list.

**Returns:**
> List of statistical metrics

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `get_plt`

```python
def get_plt(
    self,
    analysis_id: int,
    perspective_code: str,
    exposure_resource_id: int,
    filter: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    *,
    exposure_resource_type: str = 'PORTFOLIO'
) -> List[Dict[str, Any]]
```

Retrieve Period Loss Table (PLT) for an analysis.

Note: PLT is only available for HD (High Definition) analyses.

**Arguments:**
 - **analysis_id:**  Analysis ID
 - **perspective_code:**  Risk Modeler financial perspective code
   (e.g. 'GU', 'GR', 'RL', 'WX', 'QS'). See PERSPECTIVE_CODES
   in constants.py for the full set.
 - **exposure_resource_id:**  Exposure resource ID: the analysis's portfolio ID
   when exposure_resource_type is 'PORTFOLIO', or one treaty ID from
   search_analysis_treaties when it is 'TREATY'
 - **exposure_resource_type:**  'PORTFOLIO' (default) or 'TREATY'. See
   EXPOSURE_RESOURCE_TYPES in constants.py. A treaty that took no
   loss at the perspective answers an empty list.
 - **filter:**  Optional filter string (e.g., "eventId IN (1, 2, 3)" or "eventId = 123")
 - **limit:**  Optional maximum number of records to return (default: 100000)
 - **offset:**  Optional number of records to skip (for pagination)

**Returns:**
> List of PLT records containing event dates, loss dates, and loss amounts

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails

#### `get_regions`

```python
def get_regions(self, analysis_id: int) -> List[Dict[str, Any]]
```

Retrieve region/peril breakdown for an analysis or group.

This is used to build the regionPerilSimulationSet for grouping requests.
Each region entry contains framework, peril display name, region code, and
simulation identifiers (eventRateSchemeId for ELT, petId for PLT).

**Arguments:**
 - **analysis_id:**  Analysis or group ID

**Returns:**
> List of region dicts containing:
>     - region: Region code (e.g., "NA")
>     - subRegion: Sub-region code (e.g., "I2")
>     - peril: Peril display name (e.g., "Earthquake", "Windstorm"); the
>       analysis detail carries the code in ``perilCode``
>     - eventRateSchemeId: Event rate scheme ID (for ELT framework)
>     - framework: Framework type ("ELT" or "PLT")
>     - analysisId: The analysis ID
>     - modelProfileId: Model profile ID
>     - petId: PET ID (for PLT/HD framework)
>     - numSamples: Number of samples
>     - periods: Number of periods
>     - applyContractFlag: Contract application flag
>     - engineVersion: Engine version (e.g., "RL23", "HDv2.0")

**Raises:**
 - **IRPValidationError:**  If analysis_id is invalid
 - **IRPAPIError:**  If request fails

#### `search_analysis_treaties`

```python
def search_analysis_treaties(
    self,
    analysis_id: int,
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search the treaties applied to an analysis.

**Arguments:**
 - **analysis_id:**  Analysis ID
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of treaty dictionaries

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If API request fails

#### `search_analysis_treaties_paginated`

```python
def search_analysis_treaties_paginated(self, analysis_id: int) -> List[Dict[str, Any]]
```

Search all treaties applied to an analysis with automatic pagination.

Fetches all pages of results, paging via ``paginate_search``.

**Arguments:**
 - **analysis_id:**  Analysis ID

**Returns:**
> Complete list of all treaties across all pages. Each treaty contains
> treatyId, treatyNumber, treatyName, cedant, producer, treatyType
> (CATA, QUOT, SURP, WORK, CORP, STOP, NCAT), currency, attachmentBasis
> (L or R), attachmentLevel (PORT, ACCT, POL, LOC), premium,
> occurrenceLimit, attachmentPoint, riskLimit, retentionAmount,
> percentagePlaced, effectiveDate, expirationDate, percentageRetention,
> percentageRiShare, percentageCovered, priority,
> numberOfReinstatements, reinstatementCharge, maolAmount, isValid,
> userId1, userId2, aggregateDeductible, aggregateLimit, uri, lobs,
> lossOccurrences, analysisId, and tagIds.

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `describe_run`

```python
def describe_run(self, analysis_id: int) -> irp_integration.analysis.RunDescription
```

Describe what one analysis ran with.

Reads the analysis detail, its region rows, and its treaties, and names
each region's event-rate scheme from the active Risk Modeler reference
rows. Regions are normalized the way ``GroupingManager.inspect``
normalizes them. A region row names its peril as a display name, so its
peril code is the row's own ``perilCode`` resolved against the detail,
then the detail's ``perilCode``, and failing both the
``modelRegionCode`` of the ``eventratescheme`` row its
``eventRateSchemeId`` names or the ``PETMetadata`` row its ``petId``
names, with the region code stripped off the front. A PLT region's
``petId`` is then named through the ``PETMetadata`` row for the
region's model version, since PET ID 12 exists under more than one
model version with a different ``petName``. A ``petId`` no
``PETMetadata`` row qualifies keeps its ID and periods and reports
``pet_name`` None.

A region row that does not normalize is reported in ``problems``
rather than raised on: one unresolved sub-region does not make the rest
of an otherwise readable analysis unavailable. A treaty carrying no
``treatyNumber`` is reported the same way, as a
``treaty_number_missing`` problem naming its ``treatyId``, and is
returned in ``treaties`` with ``treaty_number`` None.

**Arguments:**
 - **analysis_id:**  Analysis ID

**Returns:**
> ``RunDescription`` with the region facts, the problems found in the
> rows left out of them, the event-rate scheme names those regions
> carry, and the treaties applied to the analysis

**Raises:**
 - **IRPValidationError:**  If analysis_id is invalid
 - **IRPAPIError:**  If the analysis, region, treaty, or event-rate scheme
   reference read fails, if the analysis detail is empty or is
   not an object, or if the region or treaty search returns a
   non-list response. A failed ``PETMetadata`` read does not
   raise: the rows fall back to their own ``perilCode``.

#### `submit_analysis_export_job`

```python
def submit_analysis_export_job(
    self,
    analysis_id: int,
    loss_details: List[Dict[str, Any]],
    file_extension: str = 'PARQUET'
) -> Tuple[int, Dict[str, Any]]
```

Submit an analysis results export job.

**Arguments:**
 - **analysis_id:**  ID of the analysis to export
 - **loss_details:**  List of loss detail configurations, each containing:
   - metricType: str (e.g., "LOSS_TABLES")
   - outputLevels: List[str] (e.g., ["Portfolio"])
   - perspectiveCodes: List[str] (e.g., ["GU", "GR"])
 - **file_extension:**  Export file format (default: "PARQUET")

**Returns:**
> Tuple of (job_id, request_body)

**Raises:**
 - **IRPValidationError:**  If inputs are invalid
 - **IRPAPIError:**  If the analysis doesn't exist or request fails

---

## `irp_integration.grouping`

Rules-based analysis grouping operations.

Grouping uses an inspect-then-submit contract. Inspection reads analyses, regions, treaties, and reference mappings without creating a Platform job. Submission repeats the inspection, compares its deterministic fingerprint, validates the caller's explicit choices, and posts the resulting request immediately. Treaties with the same Treaty Number and different loss-affecting terms produce warnings but do not block submission.

Member event-rate schemes are facts used to detect a conflict. For a conflicting partition whose ELT regions all come from DLM analyses, inspection returns every active Risk Modeler event-rate scheme with the partition's ``perilCode``, ``modelRegionCode``, and ``modelVersionCode``. A conflicting partition with an HD ELT region retains the peril and model-region comparison. Whether the group outputs ELT or PLT does not change either comparison. Inspection selects no default. Simulation-set choices match the partition and resolve through an active event-rate scheme reference row; a simulation set's ``eventRateSchemeId`` does not constrain the caller's event-rate selection.

Treaty comparison includes cedant, treaty type, currency, attachment and limit terms, dates, percentages, priority, reinstatement and aggregate terms, LOBs, and loss occurrences. Each warning carries the compared analysis treaty rows. Treaty comparison excludes treaty IDs, display names, producers, premiums, user-defined fields, tags, and URIs.

### `class EventRateSchemeOption`

Event-rate scheme returned for a grouping partition.

A conflicting partition whose ELT regions all come from DLM analyses receives every active Risk Modeler scheme with the partition's ``perilCode``, ``modelRegionCode``, and ``modelVersionCode``. A conflicting partition with an HD ELT region retains the peril and model-region comparison. A non-conflicting partition receives its resolved observed scheme. A conflicting partition with no applicable active scheme receives no options and reports ``event_rate_scheme_mapping_missing`` in ``blocking_problems``.

#### `__init__`

```python
def __init__(self, event_rate_scheme_id: int, label: Optional[str] = None)
```

### `class EventRateSelection`

Caller-selected event-rate scheme for a conflicting partition.

#### `__init__`

```python
def __init__(
    self,
    partition: irp_integration.grouping.GroupingPartitionKey,
    event_rate_scheme_id: int
)
```

### `class GroupingCurrency`

Explicit currency settings for a grouping request.

#### `__init__`

```python
def __init__(self, code: str, scheme: str, vintage: str, as_of_date: str)
```

### `class GroupingInspection`

Fresh facts, choices, warnings, and blocks for selected analyses.

#### `__init__`

```python
def __init__(
    self,
    analysis_ids: Tuple[int, ...],
    resource_uris: Tuple[str, ...],
    inspected_at: str,
    fingerprint: str,
    members: Tuple[irp_integration.grouping.GroupingMember, ...],
    output_loss_table: str,
    simulate_to_plt: bool,
    partitions: Tuple[irp_integration.grouping.GroupingPartition, ...],
    simulation_mappings: Tuple[irp_integration.grouping.GroupingSimulationMapping, ...],
    required_caller_inputs: Tuple[str, ...],
    warnings: Tuple[irp_integration.grouping.GroupingProblem, ...],
    blocking_problems: Tuple[irp_integration.grouping.GroupingProblem, ...]
)
```

### `class GroupingManager`

Inspect analysis members and submit resolved grouping requests.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize the grouping manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `inspect`

```python
def inspect(
    self,
    *,
    analysis_ids: Sequence[int]
) -> irp_integration.grouping.GroupingInspection
```

Inspect selected analyses without creating a Platform grouping job.

Conflicting member event-rate schemes require a caller selection from
the Risk Modeler-applicable schemes returned for the partition. A
single observed member scheme remains resolved. Simulation-set options
are the choices Risk Modeler presents for an ELT partition converted to
PLT. Inspection selects no preference or default.

**Arguments:**
 - **analysis_ids:**  At least two distinct positive Platform analysis IDs

**Returns:**
> Normalized member facts, choices, warnings, blocks, and fingerprint

**Raises:**
 - **IRPValidationError:**  If analysis_ids is malformed
 - **IRPAPIError:**  If a Platform or reference-data read fails

#### `submit`

```python
def submit(
    self,
    *,
    analysis_ids: Sequence[int],
    settings: irp_integration.grouping.GroupingSettings,
    event_rate_selections: Sequence[irp_integration.grouping.EventRateSelection],
    expected_inspection_fingerprint: str,
    simulation_set_selections: Sequence[irp_integration.grouping.SimulationSetSelection] = (),
    simulation_periods_selections: Sequence[irp_integration.grouping.SimulationPeriodsSelection] = ()
) -> irp_integration.grouping.GroupingSubmission
```

Reinspect, validate explicit choices, and create a grouping job.

**Arguments:**
 - **analysis_ids:**  At least two distinct positive Platform analysis IDs
 - **settings:**  Explicit grouping request settings
 - **event_rate_selections:**  One Risk Modeler-applicable offered scheme
   for each conflicting partition
 - **expected_inspection_fingerprint:**  Fingerprint returned by the caller's inspection
 - **simulation_set_selections:**  One offered simulation set for each ELT
   partition converted to PLT
 - **simulation_periods_selections:**  At most one ``simulationPeriods`` value
   per partition of a PLT group; a partition without one keeps the
   PET's period count or the chosen set's ``defaultPeriods``

**Returns:**
> Created grouping job ID and exact submitted request body

**Raises:**
 - **IRPValidationError:**  If a direct method argument is malformed
 - **IRPGroupingValidationError:**  If inspected facts or caller selections block submission
 - **IRPAPIError:**  If a Platform read or grouping POST fails

#### `get_job`

```python
def get_job(self, *, job_id: int) -> Dict[str, Any]
```

Retrieve grouping job status by job ID.

**Arguments:**
 - **job_id:**  Positive Platform grouping job ID

**Returns:**
> Grouping job status response

**Raises:**
 - **IRPValidationError:**  If job_id is invalid
 - **IRPAPIError:**  If the Platform read fails

### `class GroupingMember`

Normalized facts for one requested Platform analysis ID.

#### `__init__`

```python
def __init__(
    self,
    analysis_id: int,
    exists: bool,
    is_group: bool,
    analysis_framework: Optional[str],
    engine_type: Optional[str],
    engine_version: Optional[str],
    peril_code: Optional[str],
    region_code: Optional[str],
    model_version: Optional[str],
    regions: Tuple[irp_integration.grouping.GroupingRegionFact, ...]
)
```

### `class GroupingPartition`

Risk Modeler choices and observed member facts for one partition.

Member event-rate schemes determine whether ``event_rate_selection_required`` is true. When member schemes conflict, ``event_rate_scheme_options`` for a partition whose ELT regions all come from DLM analyses contains every active Risk Modeler scheme with the partition's ``perilCode``, ``modelRegionCode``, and ``modelVersionCode``. A conflicting partition with an HD ELT region retains the peril and model-region comparison. With one observed member scheme, the observed scheme remains resolved and no caller selection is required. ``simulation_set_options`` contains the simulation sets Risk Modeler presents for the partition. The package applies no preference or default to either choice.

#### `__init__`

```python
def __init__(
    self,
    key: irp_integration.grouping.GroupingPartitionKey,
    analysis_ids: Tuple[int, ...],
    event_rate_scheme_options: Tuple[irp_integration.grouping.EventRateSchemeOption, ...],
    observed_pet_ids: Tuple[int, ...],
    event_rate_selection_required: bool,
    simulation_set_options: Tuple[irp_integration.grouping.SimulationSetOption, ...] = (),
    simulation_set_selection_required: bool = False
)
```

### `class GroupingPartitionKey`

Peril, region, and model-version key used by grouping rules.

#### `__init__`

```python
def __init__(self, peril_code: str, region_code: str, model_version: str)
```

### `class GroupingProblem`

Structured grouping problem suitable for caller rendering.

``sub_regions`` names the region row a row-level problem concerns. A 23-sub-region windstorm analysis whose engine, region and peril resolve no ``SoftwareModelVersionMap`` entry reports 23 problems, each naming its own sub-region, so a caller can tell which of AL, CT, D1 was dropped. ``inspect`` reports one problem per distinct sub-region where it reported one for the whole analysis.

``terms`` on a ``GroupingTreaty`` in ``treaties`` is a ``Dict``, so a ``GroupingProblem`` carrying one is not hashable despite ``frozen=True``.

#### `__init__`

```python
def __init__(
    self,
    code: str,
    message: str,
    analysis_ids: Tuple[int, ...] = (),
    partition: Optional[irp_integration.grouping.GroupingPartitionKey] = None,
    pet_ids: Tuple[int, ...] = (),
    sub_regions: Tuple[str, ...] = (),
    treaty_numbers: Tuple[str, ...] = (),
    treaty_ids: Tuple[int, ...] = (),
    differing_fields: Tuple[str, ...] = (),
    treaties: Tuple[irp_integration.grouping.GroupingTreaty, ...] = ()
)
```

### `class GroupingProblemCode`

*Bases:* `builtins.str, enum.Enum`

Stable codes returned for rule-based grouping problems.

``EVENT_RATE_SCHEME_MISSING`` reports one ELT region that carries no positive ``eventRateSchemeId``. ``EVENT_RATE_SCHEME_MAPPING_MISSING`` reports a partition whose members disagree on their event-rate scheme and for which no active reference row carries the partition's ``perilCode``, ``modelRegionCode``, and, when the partition's ELT regions all come from DLM analyses, ``modelVersionCode``. The partition then has no option to offer, so the problem is returned in ``blocking_problems`` and ``submit`` refuses the group.

A row-level message states what is true of the region row, not what it blocks, because ``AnalysisManager.describe_run`` reports the same problems for an analysis it is only describing. ``REGION_ROW_METADATA_MISSING`` reports one region row that is malformed, carries no ELT/PLT classification, or is missing its engine, peril, or region.

The ``MEMBER_`` codes stay confined to ``inspect``: ``MEMBER_NOT_FOUND``, ``MEMBER_REGION_DATA_MISSING`` and ``MEMBER_CLASSIFICATION_CONFLICT`` concern an analysis offered as a group member, and ``describe_run`` returns none of them. ``APPLY_CONTRACT_FLAG_UNSUPPORTED`` is the one code both callers return that also carries a grouping meaning: the row sets ``applyContractFlag``, which is a fact about the row, and ``inspect`` will not group the analysis.

``TREATY_NUMBER_MISSING`` reports one treaty carrying no ``treatyNumber``. Only ``AnalysisManager.describe_run`` returns it: ``inspect`` keys treaties by ``treatyNumber`` to compare them across members, so an unnumbered treaty makes the grouping decision unsafe and raises ``IRPAPIError`` there instead.

### `class GroupingRegionFact`

Normalized Platform region fact used by grouping inspection.

``pet_name`` is the ``petName`` of the ``PETMetadata`` row for ``pet_id``, the name Risk Modeler shows for a PLT region's simulation set. It is ``None`` for an ELT region and when the ``PETMetadata`` lookup returned no single row. ``PETMetadata`` and the ``SimulationSet`` reference table are separate tables with separate ID sequences: a ``pet_id`` does not name a ``SimulationSetOption``.

#### `__init__`

```python
def __init__(
    self,
    analysis_id: int,
    framework: str,
    peril_code: str,
    region_code: str,
    model_version: str,
    engine_version: str,
    sub_region: str,
    model_region_code: str,
    event_rate_scheme_id: Optional[int],
    pet_id: Optional[int],
    pet_name: Optional[str],
    periods: Optional[int],
    apply_contract_flag: bool
)
```

### `class GroupingSettings`

Explicit caller settings for a grouping request.

#### `__init__`

```python
def __init__(
    self,
    analysis_name: str,
    currency: irp_integration.grouping.GroupingCurrency,
    propagate_detailed_losses: bool,
    num_of_simulations: int,
    description: Optional[str] = None,
    reporting_window_start: Optional[str] = None,
    simulation_window_start: Optional[str] = None,
    simulation_window_end: Optional[str] = None
)
```

### `class GroupingSimulationMapping`

Available reference-data mapping for one simulated ELT partition.

#### `__init__`

```python
def __init__(
    self,
    partition: irp_integration.grouping.GroupingPartitionKey,
    analysis_ids: Tuple[int, ...],
    engine_version: str,
    model_region_code: str,
    event_rate_scheme_id: int,
    simulation_set_id: int,
    simulation_periods: int
)
```

### `class GroupingSubmission`

Created grouping job ID and the exact submitted request body.

#### `__init__`

```python
def __init__(self, job_id: int, request_body: Dict[str, Any])
```

### `class GroupingTreaty`

One treaty as applied to one analysis, with the loss-affecting terms the grouping comparison read.

Terms are the analysis-level values from ``AnalysisManager.search_analysis_treaties_paginated``, not the EDM definition: an analysis run in CAD against a treaty defined in USD reports CAD. Keys are the ``LOSS_AFFECTING_TREATY_FIELDS`` names plus ``lobs`` and ``lossOccurrences``, normalized the same way the comparison normalizes them, so a value shown for a field in ``differing_fields`` always explains why that field differs.

#### `__init__`

```python
def __init__(
    self,
    analysis_id: int,
    treaty_id: Optional[int],
    treaty_number: str,
    terms: Dict[str, Any]
)
```

### `class SimulationPeriodsSelection`

Caller-selected ``simulationPeriods`` for one partition of a PLT group.

Without a selection the request row keeps the member PET's period count (PLT partition) or the chosen simulation set's ``defaultPeriods`` (ELT partition converted to PLT).

#### `__init__`

```python
def __init__(
    self,
    partition: irp_integration.grouping.GroupingPartitionKey,
    simulation_periods: int
)
```

### `class SimulationSetOption`

Simulation set available for converting one ELT partition to PLT.

``event_rate_scheme_id`` describes the simulation-set reference row. It does not constrain the event-rate scheme selected for the group.

#### `__init__`

```python
def __init__(
    self,
    simulation_set_id: int,
    simulation_periods: int,
    event_rate_scheme_id: Optional[int] = None,
    label: Optional[str] = None
)
```

### `class SimulationSetSelection`

Caller-selected simulation set for one ELT-to-PLT partition.

#### `__init__`

```python
def __init__(
    self,
    partition: irp_integration.grouping.GroupingPartitionKey,
    simulation_set_id: int
)
```

---

## `irp_integration.analysis_validation`

Analysis job type classification and event rate scheme validation.

The functions in this module operate only on values already retrieved from Risk Modeler. They do not make API requests or require an ``IRPClient``.

### Functions

#### `analysis_type_for_software_version`

```python
def analysis_type_for_software_version(software_version_code: str) -> Literal['DLM', 'HD']
```

Return the analysis job type for a model profile software version code.

The result is posted as the analysis job's ``type`` field.

**Arguments:**
 - **software_version_code:**  Model profile ``softwareVersionCode``.

**Returns:**
> ``"HD"`` when ``software_version_code`` contains ``"HD"``;
> otherwise, ``"DLM"``.

#### `validate_event_rate_scheme_settings`

```python
def validate_event_rate_scheme_settings(
    software_version_code: str,
    scheme_provided: bool,
    profile_peril_code: Optional[str] = None,
    profile_model_region_code: Optional[str] = None,
    scheme_peril_code: Optional[str] = None,
    scheme_model_region_code: Optional[str] = None
) -> Optional[str]
```

Validate event rate scheme settings against a model profile.

Two rules apply, and they cannot both fail: a DLM model profile requires an
event rate scheme, and an event rate scheme that was supplied must carry the
model profile's peril and model region. The first rule needs
``scheme_provided`` to be ``False``, the second needs it to be ``True``.

Pair validation is skipped unless all four peril and model region codes are
known. Risk Modeler omits ``perilCode`` and ``modelRegionCode`` from some
model profiles and event rate schemes.

**Arguments:**
 - **software_version_code:**  Model profile ``softwareVersionCode``.
 - **scheme_provided:**  Whether an event rate scheme name was supplied.
 - **profile_peril_code:**  Model profile ``perilCode``, if known.
 - **profile_model_region_code:**  Model profile ``modelRegionCode``, if known.
 - **scheme_peril_code:**  Event rate scheme ``perilCode``, if known.
 - **scheme_model_region_code:**  Event rate scheme ``modelRegionCode``, if known.

**Returns:**
> The validation error message, or ``None`` when the settings are valid.

---

## `irp_integration.rdm`

RDM (Risk Data Model) export operations.

Handles exporting analysis results to RDM via databridge.

### `class RDMManager`

Manager for RDM export operations.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize RDM manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `export_analyses_to_rdm`

```python
def export_analyses_to_rdm(
    self,
    server_name: str,
    rdm_name: str,
    analysis_names: List[str],
    skip_missing: bool = False
) -> Dict[str, Any]
```

Export multiple analyses to RDM (Risk Data Model) and poll to completion.

**Arguments:**
 - **server_name:**  Database server name
 - **rdm_name:**  Name for the RDM
 - **analysis_names:**  List of analysis names to export
 - **skip_missing:**  If True, skip missing analyses instead of raising an error

**Returns:**
> Dict containing final export job status

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If export fails or analyses not found

#### `submit_rdm_export_job`

```python
def submit_rdm_export_job(
    self,
    server_name: str,
    rdm_name: str,
    analysis_names: List[str],
    database_id: Optional[int] = None,
    analysis_edm_map: Optional[Dict[str, str]] = None,
    group_names: Optional[set] = None,
    skip_missing: bool = True
) -> Dict[str, Any]
```

Submit RDM export job.

Performs validation (server lookup, RDM existence check, analysis URI
resolution) and submits the export job.

**Arguments:**
 - **server_name:**  Database server name
 - **rdm_name:**  Name for the RDM
 - **analysis_names:**  List of analysis and group names to export
 - **database_id:**  Optional database ID (for appending to existing RDM)
 - **analysis_edm_map:**  Optional mapping of analysis names to EDM names.
   Used to look up analyses by name + EDM (since analysis names are only
   unique within an EDM). If not provided, lookups use name only.
 - **group_names:**  Optional set of known group names. Items in this set are
   looked up as groups (by name only), all others are looked up as
   analyses (by name + EDM if mapping provided).
 - **skip_missing:**  If True (default), skip analyses/groups that don't exist
   instead of raising an error. If all items are missing, returns
   a result with job_id=None and skipped=True.

**Returns:**
> Dict containing:
>     - job_id: RDM export job ID (int), or None if skipped
>     - skipped: True if job was skipped (all items missing)
>     - skipped_items: List of item names that were not found and skipped
>     - included_items: List of item names that were found and included
>     - skip_reason: Reason for skipping (if skipped=True)

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If job submission fails, or if skip_missing=False and items not found

#### `get_rdm_export_job`

```python
def get_rdm_export_job(self, job_id: int) -> Dict[str, Any]
```

Retrieve RDM export job status by job ID.

**Arguments:**
 - **job_id:**  Job ID

**Returns:**
> Dict containing job status details

**Raises:**
 - **IRPValidationError:**  If job_id is invalid
 - **IRPAPIError:**  If request fails

#### `poll_rdm_export_job_to_completion`

```python
def poll_rdm_export_job_to_completion(
    self,
    job_id: int,
    interval: int = 10,
    timeout: int = 600000
) -> Dict[str, Any]
```

Poll RDM export job until completion or timeout.

**Arguments:**
 - **job_id:**  Job ID
 - **interval:**  Polling interval in seconds (default: 10)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> Final job status details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If job times out
 - **IRPAPIError:**  If polling fails

#### `get_rdm_database_id`

```python
def get_rdm_database_id(self, rdm_name: str, server_name: str = 'databridge-1') -> int
```

Get database ID for an existing RDM by name.

**Arguments:**
 - **rdm_name:**  Name of the RDM
 - **server_name:**  Name of the database server (default: "databridge-1")

**Returns:**
> Database ID

**Raises:**
 - **IRPAPIError:**  If RDM not found

#### `get_rdm_database_full_name`

```python
def get_rdm_database_full_name(self, rdm_name: str, server_name: str = 'databridge-1') -> str
```

Get full database name for an existing RDM by name prefix.

**Arguments:**
 - **rdm_name:**  Name prefix of the RDM
 - **server_name:**  Name of the database server (default: "databridge-1")

**Returns:**
> Full database name

**Raises:**
 - **IRPAPIError:**  If RDM not found

#### `search_databases`

```python
def search_databases(
    self,
    server_name: str,
    filter: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search databases on a server.

**Arguments:**
 - **server_name:**  Name of the database server
 - **filter:**  Optional filter string (e.g., 'databaseName="MyRDM"')
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of database records

**Raises:**
 - **IRPAPIError:**  If request fails

#### `search_databases_paginated`

```python
def search_databases_paginated(self, server_name: str, filter: str = '') -> List[Dict[str, Any]]
```

Search all databases on a server with automatic pagination.

Fetches all pages of results matching the filter criteria, paging via
``paginate_search``. Note that each page re-resolves the server name to
a server ID, as the single-page call does.

**Arguments:**
 - **server_name:**  Name of the database server
 - **filter:**  Optional filter string (e.g., 'databaseName="MyRDM"')

**Returns:**
> Complete list of all matching database records across all pages

**Raises:**
 - **IRPAPIError:**  If a request fails, or if pagination cannot be shown to
   have read every page

#### `submit_delete_rdm_job`

```python
def submit_delete_rdm_job(self, rdm_name: str, server_name: str = 'databridge-1') -> str
```

Submit job to delete an RDM from the databridge server.

**Arguments:**
 - **rdm_name:**  Name prefix of the RDM to delete
 - **server_name:**  Name of the database server (default: "databridge-1")

**Returns:**
> Job ID for the delete operation

**Raises:**
 - **IRPAPIError:**  If RDM not found or delete request fails

#### `get_databridge_job`

```python
def get_databridge_job(self, job_id: str) -> str
```

Get the status of a databridge job.

**Arguments:**
 - **job_id:**  Job ID from databridge operation (e.g., delete RDM)

**Returns:**
> Job status string

**Raises:**
 - **IRPAPIError:**  If request fails

#### `poll_delete_rdm_job_to_completion`

```python
def poll_delete_rdm_job_to_completion(self, job_id: str, interval: int = 10, timeout: int = 600000) -> str
```

Poll delete RDM job until completion or timeout.

Valid statuses:
- "Enqueued": Job queued for processing
- "Processing": Job in progress
- "Succeeded": Job completed successfully
- Any other status is treated as an error

**Arguments:**
 - **job_id:**  Job ID from delete operation
 - **interval:**  Polling interval in seconds (default: 10)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> Final job status string ("Succeeded")

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If job fails or times out
 - **IRPAPIError:**  If polling fails

#### `add_group_access_to_rdm`

```python
def add_group_access_to_rdm(
    self,
    database_name: str,
    group_id: Optional[str] = None,
    server_name: str = 'databridge-1'
) -> Dict[str, Any]
```

Add group access to an RDM database.

**Arguments:**
 - **database_name:**  Name of the RDM database
 - **group_id:**  Group ID to grant access to. If None, uses DATABRIDGE_GROUP_ID
   environment variable.
 - **server_name:**  Name of the database server (default: "databridge-1")

**Returns:**
> Dict containing the API response

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPAPIError:**  If request fails or group_id is not configured

#### `search_imported_rdms`

```python
def search_imported_rdms(
    self,
    filter: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search imported RDMs.

Each record reports ``rdmName`` and ``exposureName``. An RDM imported
standalone, against an exposure set rather than an EDM, has no
``exposureName``.

**Arguments:**
 - **filter:**  Optional filter string on ``rdmName``
   (e.g., 'rdmName="MyRDM"'). ``name`` is rejected with a 400.
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of imported RDM records

**Raises:**
 - **IRPAPIError:**  If request fails

#### `submit_rdm_import_job`

```python
def submit_rdm_import_job(
    self,
    rdm_name: str,
    rdm_file_path: str,
    *,
    edm_name: Optional[str] = None,
    exposure_set_name: Optional[str] = None
) -> Tuple[int, Dict[str, Any]]
```

Submit RDM import job with S3 file upload.

This method handles the complete RDM import workflow:
1. Resolve the resource URI the RDM is imported into
2. Create import folder (get S3 credentials)
3. Upload the RDM database file to S3
4. Submit import job

Pass exactly one of ``edm_name`` or ``exposure_set_name``. With
``edm_name``, the job's ``resourceUri`` is the EDM's ``uri`` and the RDM
is imported into that EDM. With ``exposure_set_name``, the job's
``resourceUri`` is the exposure set's URI and the RDM is imported
standalone — no EDM is involved. The exposure set is created if no
exposure set of that name exists.

The import folder's ``properties.fileExtension`` is read from
``rdm_file_path``, so a .bak and a .mdf file are both imported by
passing the path.

**Arguments:**
 - **rdm_name:**  Name for the imported RDM
 - **rdm_file_path:**  Path to the .bak or .mdf file to import
 - **edm_name:**  Name of the EDM to import into. Mutually exclusive with
   ``exposure_set_name``.
 - **exposure_set_name:**  Name of the exposure set to import into, for a
   standalone RDM. Mutually exclusive with ``edm_name``.

**Returns:**
> Tuple of (job_id, request_body) where request_body is the HTTP request payload

**Raises:**
 - **IRPValidationError:**  If parameters are invalid, or if ``edm_name``
   and ``exposure_set_name`` are both given or both omitted
 - **IRPFileError:**  If file upload fails
 - **IRPAPIError:**  If API calls fail

---

## `irp_integration.risk_data_job`

Risk data job management operations.

Handles job status tracking, polling, and batch polling for all platform risk data jobs via the unified /platform/riskdata/v1/jobs endpoint.

### `class RiskDataJobManager`

Manager for risk data job status tracking and polling.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize risk data job manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `get_risk_data_job`

```python
def get_risk_data_job(self, job_id: int) -> Dict[str, Any]
```

Retrieve job status by job ID.

**Arguments:**
 - **job_id:**  Job ID

**Returns:**
> Dict containing job status details

**Raises:**
 - **IRPValidationError:**  If job_id is invalid
 - **IRPAPIError:**  If request fails

#### `search_risk_data_jobs`

```python
def search_risk_data_jobs(
    self,
    filter: str = '',
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]
```

Search risk data jobs with optional filtering.

**Arguments:**
 - **filter:**  Optional filter string (default: "")
 - **limit:**  Maximum results per page (default: 100)
 - **offset:**  Offset for pagination (default: 0)

**Returns:**
> List of risk data job dicts

**Raises:**
 - **IRPAPIError:**  If search fails

#### `poll_risk_data_job_to_completion`

```python
def poll_risk_data_job_to_completion(
    self,
    job_id: int,
    interval: int = 10,
    timeout: int = 600000
) -> Dict[str, Any]
```

Poll risk data job until completion or timeout.

Returns on any terminal status (FINISHED, FAILED, or CANCELLED) — the
caller must inspect the returned ``status`` (see the workflow contract
in ``client.py``).

**Arguments:**
 - **job_id:**  Job ID
 - **interval:**  Polling interval in seconds
 - **timeout:**  Maximum timeout in seconds

**Returns:**
> Dict containing the final job status details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If the job times out
 - **IRPAPIError:**  If a status request fails

#### `poll_risk_data_job_batch_to_completion`

```python
def poll_risk_data_job_batch_to_completion(
    self,
    job_ids: List[int],
    interval: int = 20,
    timeout: int = 600000
) -> List[Dict[str, Any]]
```

Poll multiple risk data jobs until all complete or timeout.

**Arguments:**
 - **job_ids:**  List of job IDs
 - **interval:**  Polling interval in seconds (default: 20)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> List of final job status details for all jobs

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If jobs time out
 - **IRPAPIError:**  If polling fails

---

## `irp_integration.import_job`

Import job management operations.

Provides a centralized interface for submitting, tracking, and polling platform import jobs. Uses the /platform/import/v1/jobs endpoint.

### `class ImportJobManager`

Manager for platform import job operations (EDM, RDM, MRI).

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize ImportJobManager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `submit_job`

```python
def submit_job(self, import_type: str, **kwargs) -> Tuple[int, Dict[str, Any]]
```

Submit an import job, routing to the appropriate manager based on type.

**Arguments:**
 - **import_type:**  Type of import - "EDM", "RDM", or "MRI"
 - ****kwargs:**  Arguments passed to the underlying submit method.

   For EDM (routed to EDMManager.submit_edm_import_job):
       edm_name (str): Name for the EDM
       edm_file_path (str): Path to the .bak or .mdf file
       server_name (str): Database server name (default: "sql-instance-1")

   For RDM (routed to RDMManager.submit_rdm_import_job):
       rdm_name (str): Name for the RDM
       rdm_file_path (str): Path to the .bak or .mdf file
       edm_name (str, optional): Name of the target EDM
       exposure_set_name (str, optional): Name of the target
           exposure set, for a standalone RDM with no EDM. Pass
           exactly one of edm_name or exposure_set_name.

   For MRI (routed to MRIImportManager.submit_mri_import_job):
       edm_name (str): Target EDM name
       portfolio_name (str): Target portfolio name
       accounts_file_path (str): Path to accounts CSV file
       locations_file_path (str): Path to locations CSV file
       mapping_file_path (str, optional): Path to .mff mapping file
       delimiter (str): File delimiter (default: "TAB")

**Returns:**
> Tuple of (job_id, request_body)

**Raises:**
 - **IRPValidationError:**  If import_type is invalid or kwargs are wrong
 - **IRPAPIError:**  If submission fails

#### `get_import_job`

```python
def get_import_job(self, job_id: int) -> Dict[str, Any]
```

Get import job status by job ID.

**Arguments:**
 - **job_id:**  Import job ID

**Returns:**
> Dict containing job status details

**Raises:**
 - **IRPValidationError:**  If job_id is invalid
 - **IRPAPIError:**  If request fails

#### `poll_import_job_to_completion`

```python
def poll_import_job_to_completion(
    self,
    job_id: int,
    interval: int = 10,
    timeout: int = 600000
) -> Dict[str, Any]
```

Poll import job until completion or timeout.

**Arguments:**
 - **job_id:**  Import job ID
 - **interval:**  Polling interval in seconds (default: 10)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> Final job status details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If job times out
 - **IRPAPIError:**  If polling fails

---

## `irp_integration.export_job`

Export job management operations.

Provides an interface for tracking and polling platform export jobs. Uses the /platform/export/v1/jobs endpoint.

### `class ExportJobManager`

Manager for platform export job operations.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize ExportJobManager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `get_export_job`

```python
def get_export_job(self, job_id: int) -> Dict[str, Any]
```

Get export job status by job ID.

**Arguments:**
 - **job_id:**  Export job ID

**Returns:**
> Dict containing job status details

**Raises:**
 - **IRPValidationError:**  If job_id is invalid
 - **IRPAPIError:**  If request fails
 - **IRPAuthenticationError:**  If authenticated access fails

#### `poll_export_job_to_completion`

```python
def poll_export_job_to_completion(
    self,
    job_id: int,
    interval: int = 10,
    timeout: int = 600000
) -> Dict[str, Any]
```

Poll export job until completion or timeout.

**Arguments:**
 - **job_id:**  Export job ID
 - **interval:**  Polling interval in seconds (default: 10)
 - **timeout:**  Maximum timeout in seconds (default: 600000)

**Returns:**
> Final job status details

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If job times out
 - **IRPAPIError:**  If polling fails

#### `download_export_results`

```python
def download_export_results(self, job_id: int, output_dir: str) -> str
```

Download exported analysis results for a completed export job.

Fetches the job, extracts ``downloadUrl`` from the ``DOWNLOAD_RESULTS``
task, and uses the authenticated client session to stream the ZIP file
to the output directory. A presigned S3 ``downloadUrl`` (one with an
``X-Amz-Signature`` query parameter) is requested without
``Authorization`` and ``x-rms-resource-group-id``. The completed
download replaces an existing file with the same decoded filename only
after the ZIP is validated.

**Arguments:**
 - **job_id:**  Export job ID (must be FINISHED)
 - **output_dir:**  Directory to save the downloaded file

**Returns:**
> Path to the downloaded file

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPJobError:**  If job is not finished
 - **IRPAuthenticationError:**  If authenticated download access fails
 - **IRPAPIError:**  If ``downloadUrl`` is missing, the response is
   incomplete, or the response is not a ZIP file
 - **IRPFileError:**  If the output directory or downloaded file cannot be
   written or renamed

---

## `irp_integration.s3`

S3 upload and download operations for IRP Integration.

Handles file uploads to AWS S3 using temporary credentials provided by Moody's Risk Modeler API, and file downloads from CloudFront/presigned URLs.

### `class S3Manager`

Manager for S3 upload and CloudFront download operations.

#### `__init__`

```python
def __init__(
    self,
    transfer_config: Optional[boto3.s3.transfer.TransferConfig] = None
)
```

Initialize S3 Manager.

**Arguments:**
 - **transfer_config:**  Optional boto3 TransferConfig for multipart uploads.
   If not provided, uses default optimized settings.

#### `upload_file`

```python
def upload_file(
    self,
    file_path: str,
    upload_details: Dict[str, Any],
    content_type: Optional[str] = None
) -> None
```

Upload file to S3 using credentials from API response.

This method handles the S3 upload for EDM/RDM import workflows.
It extracts the upload URL and credentials from the API response
and performs a multipart upload.

**Arguments:**
 - **file_path:**  Path to the file to upload
 - **upload_details:**  Upload details dict from create import folder response,
   containing 'uploadUrl' and 'presignParams' (with base64-encoded
   credentials)
 - **content_type:**  Optional content type override. If not provided,
   inferred from file extension.

**Raises:**
 - **IRPValidationError:**  If parameters are invalid or required fields missing
 - **IRPFileError:**  If file upload fails

**Example:**
> ```python
> # From create import folder response:
> # response['uploadDetails']['exposureFile']
> upload_details = {
>     "fileUri": "platform/import/v1/folders/39073/files/105108",
>     "presignParams": {
>         "accessKeyId": "<base64>",
>         "secretAccessKey": "<base64>",
>         "sessionToken": "<base64>",
>         "path": "<base64>",
>         "region": "<base64>"
>     },
>     "uploadUrl": "https://bucket.s3.amazonaws.com/path/to/file.bak"
> }
> s3_manager.upload_file("/path/to/file.bak", upload_details)
> ```

#### `upload_fileobj`

```python
def upload_fileobj(
    self,
    fileobj: <class 'BinaryIO'>,
    upload_details: Dict[str, Any],
    content_type: str
) -> None
```

Upload file-like object to S3 using credentials from API response.

**Arguments:**
 - **fileobj:**  File-like object (e.g., BytesIO, open file in 'rb' mode)
 - **upload_details:**  Upload details dict from create import folder response
 - **content_type:**  Content type for the upload (required for streams)

**Raises:**
 - **IRPValidationError:**  If parameters are invalid or required fields missing
 - **IRPFileError:**  If file upload fails

#### `upload_file_from_credentials`

```python
def upload_file_from_credentials(
    self,
    file_path: str,
    credentials: Dict[str, str],
    bucket: str,
    key: str,
    content_type: Optional[str] = None
) -> None
```

Upload file to S3 using pre-decoded credentials.

Lower-level method for cases where credentials are already decoded
(e.g., MRI import workflow).

**Arguments:**
 - **file_path:**  Path to the file to upload
 - **credentials:**  Dict with decoded AWS credentials:
   - aws_access_key_id: str
   - aws_secret_access_key: str
   - aws_session_token: str
   - s3_region: str
 - **bucket:**  S3 bucket name
 - **key:**  S3 object key (path within bucket)
 - **content_type:**  Optional content type override

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPFileError:**  If file upload fails

#### `download_from_url`

```python
def download_from_url(
    self,
    url: str,
    destination_path: str,
    chunk_size: int = 8192,
    timeout: int = 300
) -> None
```

Download file from CloudFront or presigned URL to local path.

**Arguments:**
 - **url:**  Full URL including any signed parameters
 - **destination_path:**  Local path to save the file
 - **chunk_size:**  Download chunk size in bytes (default: 8192)
 - **timeout:**  Request timeout in seconds (default: 300)

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPFileError:**  If download fails or file cannot be written

#### `download_from_url_to_fileobj`

```python
def download_from_url_to_fileobj(
    self,
    url: str,
    fileobj: <class 'BinaryIO'>,
    chunk_size: int = 8192,
    timeout: int = 300
) -> None
```

Download file from CloudFront or presigned URL to file-like object.

**Arguments:**
 - **url:**  Full URL including any signed parameters
 - **fileobj:**  File-like object to write to (must be opened in binary write mode)
 - **chunk_size:**  Download chunk size in bytes (default: 8192)
 - **timeout:**  Request timeout in seconds (default: 300)

**Raises:**
 - **IRPValidationError:**  If parameters are invalid
 - **IRPFileError:**  If download fails or write fails

---

## `irp_integration.reference_data`

Reference data management operations.

Handles retrieval and creation of reference data including model profiles, output profiles, event rate schemes, currencies, and tags.

### `class ReferenceDataManager`

Manager for reference data operations.

#### `__init__`

```python
def __init__(self, irp: irp_integration.IRPClient)
```

Initialize reference data manager.

**Arguments:**
 - **irp:**  Owning IRP client instance

#### `get_model_profiles`

```python
def get_model_profiles(self) -> Dict[str, Any]
```

Retrieve all model profiles.

**Returns:**
> Dict containing model profile list

**Raises:**
 - **IRPAPIError:**  If request fails

#### `get_model_profile_by_name`

```python
def get_model_profile_by_name(self, profile_name: str) -> Dict[str, Any]
```

Retrieve model profile by name.

**Arguments:**
 - **profile_name:**  Model profile name

**Returns:**
> Dict containing model profile details

**Raises:**
 - **IRPValidationError:**  If profile_name is invalid
 - **IRPAPIError:**  If request fails

#### `get_output_profiles`

```python
def get_output_profiles(self) -> List[Dict[str, Any]]
```

Retrieve all output profiles.

**Returns:**
> List of output profile dicts

**Raises:**
 - **IRPAPIError:**  If request fails

#### `get_output_profile_by_name`

```python
def get_output_profile_by_name(self, profile_name: str) -> List[Dict[str, Any]]
```

Retrieve output profile by name.

**Arguments:**
 - **profile_name:**  Output profile name

**Returns:**
> List of matching output profile dicts

**Raises:**
 - **IRPValidationError:**  If profile_name is invalid
 - **IRPAPIError:**  If request fails

#### `get_event_rate_schemes`

```python
def get_event_rate_schemes(self) -> Dict[str, Any]
```

Retrieve all active event rate schemes.

**Returns:**
> Dict containing event rate scheme list

**Raises:**
 - **IRPAPIError:**  If request fails

#### `get_event_rate_scheme_by_name`

```python
def get_event_rate_scheme_by_name(
    self,
    scheme_name: str,
    peril_code: Optional[str] = None,
    model_region_code: Optional[str] = None
) -> Dict[str, Any]
```

Retrieve event rate scheme by name with optional peril and region filtering.

When the same event rate scheme name exists for multiple peril/region combinations,
use the peril_code and model_region_code parameters to filter to the correct one.
These values can be obtained from the corresponding model profile.

**Arguments:**
 - **scheme_name:**  Event rate scheme name
 - **peril_code:**  Optional peril code (e.g., "CS", "WS") to filter results
 - **model_region_code:**  Optional model region code (e.g., "NACS", "NAWS") to filter results

**Returns:**
> Dict containing event rate scheme details

**Raises:**
 - **IRPValidationError:**  If scheme_name is invalid
 - **IRPAPIError:**  If request fails

#### `search_currencies`

```python
def search_currencies(
    self,
    where_clause: str = '',
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    sort: str = '',
    sort_order: Optional[int] = None
) -> Dict[str, Any]
```

Search currencies with optional filtering, sorting, and pagination.

**Arguments:**
 - **where_clause:**  Optional filter clause
 - **limit:**  Maximum number of records to return
 - **offset:**  Number of records to skip
 - **sort:**  Optional field to sort by
 - **sort_order:**  Sort direction — 1 for ascending, -1 for descending

**Returns:**
> Dict containing currencies (with an 'items' list)

**Raises:**
 - **IRPValidationError:**  If limit, offset, or sort_order is invalid
 - **IRPAPIError:**  If request fails

#### `search_currency_schemes`

```python
def search_currency_schemes(
    self,
    where_clause: str = '',
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    sort: str = '',
    sort_order: Optional[int] = None
) -> Dict[str, Any]
```

Search currency schemes with optional filtering, sorting, and pagination.

**Arguments:**
 - **where_clause:**  Optional filter clause
 - **limit:**  Maximum number of records to return
 - **offset:**  Number of records to skip
 - **sort:**  Optional field to sort by
 - **sort_order:**  Sort direction — 1 for ascending, -1 for descending

**Returns:**
> Dict containing currency schemes (with an 'items' list)

**Raises:**
 - **IRPValidationError:**  If limit, offset, or sort_order is invalid
 - **IRPAPIError:**  If request fails

#### `search_currency_scheme_vintages`

```python
def search_currency_scheme_vintages(
    self,
    where_clause: str = '',
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    sort: str = '',
    sort_order: Optional[int] = None
) -> Dict[str, Any]
```

Search currency scheme vintages with optional filtering, sorting, and pagination.

**Arguments:**
 - **where_clause:**  Optional filter clause
 - **limit:**  Maximum number of records to return
 - **offset:**  Number of records to skip
 - **sort:**  Optional field to sort by
 - **sort_order:**  Sort direction — 1 for ascending, -1 for descending

**Returns:**
> Dict containing currency scheme vintages

**Raises:**
 - **IRPValidationError:**  If limit, offset, or sort_order is invalid
 - **IRPAPIError:**  If request fails

#### `get_latest_currency_scheme_vintage`

```python
def get_latest_currency_scheme_vintage(self) -> Dict[str, Any]
```

Get the latest RMS currency scheme vintage by effective date.

**Returns:**
> Dict containing the currency scheme vintage with the most recent effectiveDate

**Raises:**
 - **IRPAPIError:**  If request fails or no vintages found

#### `get_analysis_currency`

```python
def get_analysis_currency(self) -> Dict[str, str]
```

Get currency dict for analysis requests.

Attempts to get the latest RMS currency scheme vintage from the API.
Falls back to default values if the API call fails.

**Returns:**
> Currency dict with asOfDate, code, scheme, and vintage

#### `get_currency_by_name`

```python
def get_currency_by_name(self, currency_name: str) -> Dict[str, Any]
```

Retrieve currency by name.

**Arguments:**
 - **currency_name:**  Currency name

**Returns:**
> Dict containing currency details

**Raises:**
 - **IRPValidationError:**  If currency_name is invalid
 - **IRPAPIError:**  If request fails

#### `get_tag_by_name`

```python
def get_tag_by_name(self, tag_name: str) -> List[Dict[str, Any]]
```

Retrieve tag by name.

**Arguments:**
 - **tag_name:**  Tag name

**Returns:**
> List of dicts containing tag details

**Raises:**
 - **IRPValidationError:**  If tag_name is invalid
 - **IRPAPIError:**  If request fails

#### `create_tag`

```python
def create_tag(self, tag_name: str) -> Dict[str, str]
```

Create new tag.

**Arguments:**
 - **tag_name:**  Tag name

**Returns:**
> Dict with tag ID

**Raises:**
 - **IRPValidationError:**  If tag_name is invalid
 - **IRPAPIError:**  If request fails

#### `get_tag_ids_from_tag_names`

```python
def get_tag_ids_from_tag_names(self, tag_names: List[str]) -> List[int]
```

Get or create tags by names and return their IDs.

This method will create tags if they don't already exist.

**Arguments:**
 - **tag_names:**  List of tag names

**Returns:**
> List of tag IDs

**Raises:**
 - **IRPValidationError:**  If tag_names is empty
 - **IRPAPIError:**  If request fails

#### `get_all_simulation_sets`

```python
def get_all_simulation_sets(self) -> List[Dict[str, Any]]
```

Get all active simulation sets.

Simulation sets map event-rate scheme IDs to simulation-set IDs for
ELT analyses. Grouping inspection matches the partition fields and
requires each simulation set's ``eventRateSchemeId`` to resolve in the
active EventRateScheme response. Risk Modeler uses the relationship to
omit climate-conditioned choices. The relationship does not restrict
the caller's event-rate selection.

**Returns:**
> List of simulation set dicts

**Raises:**
 - **IRPAPIError:**  If request fails

#### `get_simulation_set_by_event_rate_scheme_id`

```python
def get_simulation_set_by_event_rate_scheme_id(self, event_rate_scheme_id: int) -> Dict[str, Any]
```

Get simulation set by event rate scheme ID.

Raises when zero or more than one active simulation set carries
``event_rate_scheme_id``. Grouping inspection does not use this lookup;
it reads all active simulation sets because Risk Modeler allows a
simulation set to be selected independently of the event-rate scheme.

**Arguments:**
 - **event_rate_scheme_id:**  Event rate scheme ID from analysis regions

**Returns:**
> Dict containing simulation set details with 'id' being the simulationSetId

**Raises:**
 - **IRPAPIError:**  If request fails or simulation set not found

#### `get_simulation_set_exact`

```python
def get_simulation_set_exact(
    self,
    *,
    event_rate_scheme_id: int,
    model_region_code: str,
    model_version: str
) -> Dict[str, Any]
```

Return one exact simulation-set mapping.

**Arguments:**
 - **event_rate_scheme_id:**  Positive event-rate scheme ID
 - **model_region_code:**  Exact broad model region code, such as ``NAWS``
 - **model_version:**  Exact model version code

**Returns:**
> The sole matching simulation-set row

**Raises:**
 - **IRPValidationError:**  If an argument is malformed
 - **IRPAPIError:**  If zero or multiple rows match

#### `get_simulation_set_by_region_peril_and_engine`

```python
def get_simulation_set_by_region_peril_and_engine(
    self,
    region_code: str,
    peril_code: str,
    engine_version: str
) -> Dict[str, Any]
```

Get simulation set by regionCode, perilCode, and engineVersion.

The lookup uses regionCode + perilCode to build the broader modelRegionCode
(e.g., "NA" + "WS" = "NAWS") since SimulationSet entries use broader regional
codes, not sub-region-specific codes like "HTWS".

The method rejects multiple matches. Grouping inspection does not use
this helper because Risk Modeler presents every simulation set for the
peril, region, and model version as a caller choice.

**Arguments:**
 - **region_code:**  Region code (e.g., "NA", "US", "CB")
 - **peril_code:**  Peril code (e.g., "WS", "EQ", "FL")
 - **engine_version:**  Engine version (e.g., "RL23", "HDv2.0")

**Returns:**
> Dict containing simulation set details with 'id' being the simulationSetId

**Raises:**
 - **IRPValidationError:**  If inputs are invalid
 - **IRPAPIError:**  If request fails or simulation set not found

#### `get_all_pet_metadata`

```python
def get_all_pet_metadata(self) -> List[Dict[str, Any]]
```

Get all PET (Probabilistic Event Table) metadata.

PET metadata describes the available PLT/HD simulation sets. A PET ID
can occur in more than one model version or model region.

**Returns:**
> List of PET metadata dicts

**Raises:**
 - **IRPAPIError:**  If request fails

#### `get_pet_metadata_by_id`

```python
def get_pet_metadata_by_id(self, pet_id: int) -> Dict[str, Any]
```

Get PET metadata by PET ID.

Raises when ``pet_id`` occurs in zero or more than one metadata row. A
PET ID can occur once per model version, so use
``get_pet_metadata_exact`` when model qualifiers are available.

**Arguments:**
 - **pet_id:**  PET ID from analysis regions

**Returns:**
> Dict containing PET metadata details

**Raises:**
 - **IRPValidationError:**  If pet_id is invalid
 - **IRPAPIError:**  If request fails or PET not found

#### `get_pet_metadata_exact`

```python
def get_pet_metadata_exact(
    self,
    *,
    pet_id: int,
    model_version: str,
    model_region_code: Optional[str] = None
) -> Dict[str, Any]
```

Return one qualified PET metadata row.

**Arguments:**
 - **pet_id:**  Positive PET ID from an analysis region
 - **model_version:**  Exact model version code
 - **model_region_code:**  Optional exact model region code

**Returns:**
> The sole PET metadata row matching every supplied qualifier

**Raises:**
 - **IRPValidationError:**  If an argument is malformed
 - **IRPAPIError:**  If zero or multiple rows match

#### `get_all_software_model_version_map`

```python
def get_all_software_model_version_map(self) -> List[Dict[str, Any]]
```

Get all active software model version mappings.

This maps engine versions to model versions for grouping requests.

**Returns:**
> List of version map dicts

**Raises:**
 - **IRPAPIError:**  If request fails

#### `get_model_version_by_engine_version`

```python
def get_model_version_by_engine_version(self, engine_version: str) -> str
```

Get model version for a given engine version.

Note: This method returns the first entry matching the softwareVersionCode.
For more precise matching, use ``get_model_version_by_engine_region_peril``.

**Arguments:**
 - **engine_version:**  Engine version string (e.g., "HDv2.0", "RL23")

**Returns:**
> Model version string (e.g., "2.0", "23.0")

**Raises:**
 - **IRPValidationError:**  If engine_version is invalid
 - **IRPAPIError:**  If request fails or mapping not found

#### `get_model_version_by_engine_region_peril`

```python
def get_model_version_by_engine_region_peril(self, engine_version: str, region_code: str, peril_code: str) -> str
```

Get model version for a given engine version, region code, and peril code.

This provides a precise lookup using the broader modelRegionCode (e.g., "NAWS")
built from regionCode + perilCode, since SoftwareModelVersionMap uses broader
codes, not sub-region-specific codes like "HTWS".

**Arguments:**
 - **engine_version:**  Engine version string (e.g., "HDv2.0", "RL23")
 - **region_code:**  Region code (e.g., "NA", "US", "CB")
 - **peril_code:**  Peril code (e.g., "WS", "EQ", "FL")

**Returns:**
> Model version string (e.g., "2.0", "11.0")

**Raises:**
 - **IRPValidationError:**  If inputs are invalid
 - **IRPAPIError:**  If request fails or mapping not found

---

## `irp_integration.databridge`

Data Bridge (SQL Server) operations.

Provides SQL Server connectivity via pyodbc with named connections, parameterized query execution using {{ param }} template syntax, and file-based SQL execution. Designed for interacting with Moody's Data Bridge databases.

**Connection Management:**

Supports multiple named MSSQL connections configured via environment variables. Each connection requires:

MSSQL_{CONNECTION_NAME}_SERVER   - Server hostname or IP (required) MSSQL_{CONNECTION_NAME}_USER     - SQL auth username (required) MSSQL_{CONNECTION_NAME}_PASSWORD - SQL auth password (required) MSSQL_{CONNECTION_NAME}_PORT     - Port (optional, defaults to 1433)

Global settings: MSSQL_DRIVER     - ODBC driver name (default: 'ODBC Driver 18 for SQL Server') MSSQL_TRUST_CERT - Trust server certificate (default: 'yes') MSSQL_TIMEOUT    - Connection timeout in seconds (default: '30')

**Parameter Substitution:**

SQL queries support named parameters using {{ param_name }} syntax. Parameters are context-aware: identifiers (inside brackets or as part of table names) are substituted raw, while values are escaped with SQL injection protection.

### `class ExpressionTemplate`

*Bases:* `string.Template`

Custom Template class for SQL parameter substitution.

Uses {{ PARAM }} syntax with space padding to avoid conflicts with SQL syntax. Example: SELECT * FROM table WHERE id = {{ ID }}

### `class DataBridgeManager`

Manager for SQL Server (Data Bridge) operations.

Unlike other managers, DataBridgeManager does not depend on the HTTP Client. It connects directly to SQL Server via pyodbc. It can be used standalone or attached to IRPClient as client.databridge.

**Args:**

default_connection: Default connection name used when no connection

is specified in method calls. Defaults to 'DATABRIDGE'.

**Environment Variables (per connection):**

MSSQL_{CONNECTION_NAME}_SERVER   - Server hostname or IP (required) MSSQL_{CONNECTION_NAME}_USER     - SQL auth username (required) MSSQL_{CONNECTION_NAME}_PASSWORD - SQL auth password (required) MSSQL_{CONNECTION_NAME}_PORT     - Port (optional, defaults to 1433)

**Global Environment Variables:**

MSSQL_DRIVER     - ODBC driver name (default: 'ODBC Driver 18 for SQL Server') MSSQL_TRUST_CERT - Trust server certificate (default: 'yes') MSSQL_TIMEOUT    - Connection timeout in seconds (default: '30')

**Example:**

# Via IRPClient from irp_integration import IRPClient client = IRPClient() df = client.databridge.execute_query(

"SELECT * FROM portfolios WHERE value > {{ min_value }}", params={'min_value': 1000000}, connection='DATABRIDGE', database='DataWarehouse'

)

# Standalone from irp_integration.databridge import DataBridgeManager db = DataBridgeManager(default_connection='DATABRIDGE') df = db.execute_query("SELECT 1 AS test", database='master')

#### `__init__`

```python
def __init__(self, default_connection: str = 'DATABRIDGE')
```

Initialize the Data Bridge manager.

**Arguments:**
 - **default_connection:**  Name of the connection used when a query does
   not specify one (default: "DATABRIDGE")

#### `get_connection_config`

```python
def get_connection_config(self, connection_name: Optional[str] = None) -> Dict[str, str]
```

Get connection configuration for a named MSSQL connection.

Reads configuration from environment variables following the pattern
MSSQL_{CONNECTION_NAME}_{SETTING}.

**Arguments:**
 - **connection_name:**  Name of the connection (e.g., 'DATABRIDGE', 'ANALYTICS').
   Defaults to the manager's default_connection.

**Returns:**
> Dictionary with connection parameters: server, port, driver,
> trust_cert, timeout, user, password.

**Raises:**
 - **IRPValidationError:**  If required environment variables are missing.

**Example:**
> config = db.get_connection_config('DATABRIDGE')
> # Returns: {'server': 'db.company.com', 'user': 'svc', ...}

#### `build_connection_string`

```python
def build_connection_string(
    self,
    connection_name: Optional[str] = None,
    database: Optional[str] = None
) -> str
```

Build ODBC connection string for SQL Server.

**Arguments:**
 - **connection_name:**  Name of the connection. Defaults to the
   manager's default_connection.
 - **database:**  Optional database name to connect to.

**Returns:**
> ODBC connection string.

**Example:**
> conn_str = db.build_connection_string('DATABRIDGE', database='MyDB')

#### `get_connection`

```python
def get_connection(
    self,
    connection_name: Optional[str] = None,
    database: Optional[str] = None
)
```

Context manager for SQL Server database connections.

Automatically handles connection lifecycle: opens connection,
yields it for use, and closes on exit (even if exception occurs).

**Arguments:**
 - **connection_name:**  Name of the connection to use. Defaults to
   the manager's default_connection.
 - **database:**  Optional database name to connect to.

**Yields:**
> pyodbc.Connection object.

**Raises:**
 - **IRPDataBridgeConnectionError:**  If connection fails.

**Example:**
> with db.get_connection('DATABRIDGE', database='MyDB') as conn:
>     cursor = conn.cursor()
>     cursor.execute("SELECT * FROM portfolios")
>     rows = cursor.fetchall()

#### `test_connection`

```python
def test_connection(self, connection_name: Optional[str] = None) -> bool
```

Test if a SQL Server connection is working.

**Arguments:**
 - **connection_name:**  Name of the connection to test. Defaults to
   the manager's default_connection.

**Returns:**
> True if connection successful, False otherwise.

**Example:**
> if db.test_connection('DATABRIDGE'):
>     print("Connection successful!")

#### `execute_query`

```python
def execute_query(
    self,
    query: str,
    params: Optional[Dict[str, Any]] = None,
    connection: Optional[str] = None,
    database: Optional[str] = None
) -> pandas.DataFrame
```

Execute SELECT query and return results as DataFrame.

**Arguments:**
 - **query:**  SQL SELECT query (supports {{ param_name }} placeholders).
 - **params:**  Query parameters as dictionary.
 - **connection:**  Name of the SQL Server connection to use.
   Defaults to the manager's default_connection.
 - **database:**  Optional database name to connect to.

**Returns:**
> pandas DataFrame with query results.

**Raises:**
 - **IRPDataBridgeQueryError:**  If query execution fails.

**Example:**
> df = db.execute_query(
>     "SELECT * FROM portfolios WHERE value > {{ min_value }}",
>     params={'min_value': 1000000},
>     connection='DATABRIDGE',
>     database='DataWarehouse'
> )

#### `execute_scalar`

```python
def execute_scalar(
    self,
    query: str,
    params: Optional[Dict[str, Any]] = None,
    connection: Optional[str] = None,
    database: Optional[str] = None
) -> Any
```

Execute query and return single scalar value (first column of first row).

**Arguments:**
 - **query:**  SQL query returning single value.
 - **params:**  Query parameters.
 - **connection:**  Name of the SQL Server connection to use.
   Defaults to the manager's default_connection.
 - **database:**  Optional database name to connect to.

**Returns:**
> Single value from query result (or None if no results).

**Raises:**
 - **IRPDataBridgeQueryError:**  If query execution fails.

**Example:**
> count = db.execute_scalar(
>     "SELECT COUNT(*) FROM portfolios WHERE value > {{ min_value }}",
>     params={'min_value': 1000000},
>     connection='DATABRIDGE',
>     database='DataWarehouse'
> )

#### `execute_command`

```python
def execute_command(
    self,
    query: str,
    params: Optional[Dict[str, Any]] = None,
    connection: Optional[str] = None,
    database: Optional[str] = None
) -> int
```

Execute non-query command (INSERT, UPDATE, DELETE) and return rows affected.

**Arguments:**
 - **query:**  SQL command.
 - **params:**  Query parameters.
 - **connection:**  Name of the SQL Server connection to use.
   Defaults to the manager's default_connection.
 - **database:**  Optional database name to connect to.

**Returns:**
> Number of rows affected.

**Raises:**
 - **IRPDataBridgeQueryError:**  If command execution fails.

**Example:**
> rows = db.execute_command(
>     "UPDATE portfolios SET status = {{ status }} WHERE value < {{ min_value }}",
>     params={'status': 'INACTIVE', 'min_value': 100000},
>     connection='DATABRIDGE',
>     database='DataWarehouse'
> )
> print(f"Updated {rows} rows")

#### `execute_query_from_file`

```python
def execute_query_from_file(
    self,
    file_path: str,
    params: Optional[Dict[str, Any]] = None,
    connection: Optional[str] = None,
    database: Optional[str] = None
) -> List[pandas.DataFrame]
```

Execute SQL query from file and return results as list of DataFrames.

Handles both single-statement queries and multi-statement scripts
(e.g., scripts with USE statements followed by SELECT). Each result
set is returned as a separate DataFrame in the list.

**Arguments:**
 - **file_path:**  Path to SQL file (absolute or relative to cwd).
 - **params:**  Query parameters (supports {{ param_name }} placeholders).
 - **connection:**  Name of the SQL Server connection to use.
   Defaults to the manager's default_connection.
 - **database:**  Optional database name to connect to.

**Returns:**
> List of pandas DataFrames, one per result set.

**Raises:**
 - **IRPValidationError:**  If SQL file does not exist.
 - **IRPDataBridgeQueryError:**  If query execution fails.

**Example:**
> results = db.execute_query_from_file(
>     'C:/sql/extract_policies.sql',
>     params={'cycle_name': 'Q1-2025', 'run_date': '2025-01-15'},
>     connection='DATABRIDGE',
>     database='AnalyticsDB'
> )
> df = results[0]  # First result set

---

## `irp_integration.exceptions`

Custom exception classes for IRP Integration module.

These exceptions provide clear, structured error handling for different failure scenarios when interacting with Moody's Risk Modeler API.

### `class IRPIntegrationError`

*Bases:* `builtins.Exception`

Base exception for all IRP integration errors.

``status_code`` holds the HTTP status of the failed response, or ``None`` when no response came back or the error does not concern an HTTP response.

#### `__init__`

```python
def __init__(self, message: str, status_code: Optional[int] = None)
```

Initialize the error with an optional HTTP status code.

**Arguments:**
 - **message:**  Error message
 - **status_code:**  HTTP status of the failed response, if one came back

### `class IRPAPIError`

*Bases:* `IRPIntegrationError`

API request or response errors.

Raised when HTTP requests fail, responses are malformed, or API returns unexpected status codes.

``status_code`` is set when an HTTP response came back. It is ``None`` for connection errors, timeouts, configuration errors and malformed responses.

### `class IRPAuthenticationError`

*Bases:* `IRPIntegrationError`

Bearer-token authentication errors.

Raised when bearer-token login or token refresh fails (bad credentials, missing access token in the response, etc.).

``status_code`` is ``401`` when ``Client.request()`` still gets a ``401`` after re-logging in. It is the login response's status when the bearer login gets a non-OK response. It is ``None`` for login request errors, a non-JSON login response and a missing ``accessToken``.

### `class IRPValidationError`

*Bases:* `IRPIntegrationError`

Input validation errors.

Raised when method parameters fail validation checks (e.g., empty strings, invalid IDs, missing files).

### `class IRPGroupingValidationError`

*Bases:* `IRPValidationError`

Rule-based grouping validation errors with structured problems.

#### `__init__`

```python
def __init__(self, problems: Sequence[irp_integration.grouping.GroupingProblem])
```

Initialize the error from one or more grouping problems.

**Arguments:**
 - **problems:**  Structured problems that prevented grouping submission

### `class IRPWorkflowError`

*Bases:* `IRPIntegrationError`

Workflow execution errors.

Raised when workflows fail to complete successfully, timeout, or return error status.

### `class IRPReferenceDataError`

*Bases:* `IRPIntegrationError`

Reference data lookup errors.

Raised when required reference data (treaty types, currencies, etc.) cannot be found or retrieved.

### `class IRPFileError`

*Bases:* `IRPIntegrationError`

File operation errors.

Raised when file operations fail (file not found, invalid format, upload errors, etc.).

### `class IRPJobError`

*Bases:* `IRPIntegrationError`

Job management errors.

Raised when job submission, status retrieval, or result fetching encounters issues.

### `class IRPDataBridgeError`

*Bases:* `IRPIntegrationError`

Data Bridge (SQL Server) operation errors.

Base exception for all SQL Server / Data Bridge failures including connection, configuration, and query errors.

### `class IRPDataBridgeConnectionError`

*Bases:* `IRPDataBridgeError`

Data Bridge connection errors.

Raised when SQL Server connection fails (bad credentials, unreachable server, driver not installed).

### `class IRPDataBridgeQueryError`

*Bases:* `IRPDataBridgeError`

Data Bridge query execution errors.

Raised when SQL query execution fails, parameter substitution fails, or SQL file cannot be read.

---

## `irp_integration.validators`

Input validation utilities for IRP Integration module.

Provides reusable validation functions that raise descriptive IRPValidationError exceptions when validation fails.

### Functions

#### `validate_non_empty_string`

```python
def validate_non_empty_string(value: Any, param_name: str) -> None
```

Validate that a value is a non-empty string.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If value is not a non-empty string

#### `validate_positive_int`

```python
def validate_positive_int(value: Any, param_name: str) -> None
```

Validate that a value is a positive integer.

``bool`` is rejected; see ``_is_int``.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If value is not a positive integer

#### `validate_non_negative_int`

```python
def validate_non_negative_int(value: Any, param_name: str) -> None
```

Validate that a value is a non-negative integer.

``bool`` is rejected; see ``_is_int``.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If value is not a non-negative integer

#### `validate_sort_order`

```python
def validate_sort_order(value: Any, param_name: str) -> None
```

Validate that a value is a valid sort order: 1 (ascending) or -1 (descending).

``bool`` is rejected; see ``_is_int``.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If value is not 1 or -1

#### `validate_max_length`

```python
def validate_max_length(value: Any, param_name: str, max_length: int) -> None
```

Validate that a string is no longer than a server-side limit.

Raises rather than truncating: a shortened value can collide two distinct
inputs into one.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message
 - **max_length:**  Maximum permitted number of characters

**Raises:**
 - **IRPValidationError:**  If value is not a string, or is longer than
   max_length

#### `validate_file_exists`

```python
def validate_file_exists(file_path: str, param_name: str = 'file_path') -> None
```

Validate that a file exists at the given path.

**Arguments:**
 - **file_path:**  Path to file
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If file does not exist

#### `validate_import_file_extension`

```python
def validate_import_file_extension(file_path: str, param_name: str = 'file_path') -> str
```

Validate that a path names a database file an import job accepts.

**Arguments:**
 - **file_path:**  Path to the database file
 - **param_name:**  Parameter name for error message

**Returns:**
> The extension without its leading dot, lowercased, in the form
> ``properties.fileExtension`` expects: "bak" or "mdf"

**Raises:**
 - **IRPValidationError:**  If the extension is not one of
   IMPORT_FILE_EXTENSIONS

#### `validate_list_not_empty`

```python
def validate_list_not_empty(value: Any, param_name: str) -> None
```

Validate that a value is a non-empty list.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If value is not a non-empty list

#### `validate_geohaz_layers`

```python
def validate_geohaz_layers(value: Any, param_name: str = 'layers') -> None
```

Validate GeoHaz geocode and hazard layer request dictionaries.

Product names, engine types, versions, and additional fields pass through
so callers can use API additions without waiting for a package release.

**Arguments:**
 - **value:**  GeoHaz layer dictionaries to validate
 - **param_name:**  Parameter name for error messages (default: "layers")

**Raises:**
 - **IRPValidationError:**  If the list, a layer, or required layer options are
   invalid

#### `validate_list_of_positive_ints`

```python
def validate_list_of_positive_ints(value: Any, param_name: str) -> None
```

Validate that a value is a list containing only positive integers.

An empty list is accepted; callers that require at least one element
should enforce that themselves.

``bool`` is rejected; see ``_is_int``.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If value is not a list, or any element is not a
   positive integer

#### `validate_positive_float`

```python
def validate_positive_float(value: Any, param_name: str) -> None
```

Validate that a value is a positive float.

An int is accepted; ``bool`` is not.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If value is not a positive float

#### `validate_non_negative_float`

```python
def validate_non_negative_float(value: Any, param_name: str) -> None
```

Validate that a value is a non-negative float.

An int is accepted; ``bool`` is not.

**Arguments:**
 - **value:**  Value to validate
 - **param_name:**  Parameter name for error message

**Raises:**
 - **IRPValidationError:**  If value is not a non-negative float

---

## `irp_integration.utils`

Utility functions for IRP Integration module.

Provides common helper functions for response parsing, data extraction, and reference data lookup operations.

### Functions

#### `get_location_header`

```python
def get_location_header(
    response: requests.models.Response,
    error_context: str = 'response'
) -> str
```

Get Location header from response.

**Arguments:**
 - **response:**  HTTP response object
 - **error_context:**  Context description for error message

**Returns:**
> Location header value

**Raises:**
 - **IRPAPIError:**  If the Location header is missing

#### `extract_id_from_location_header`

```python
def extract_id_from_location_header(
    response: requests.models.Response,
    error_context: str = 'response'
) -> str
```

Extract ID from Location header in HTTP response.

**Arguments:**
 - **response:**  HTTP response object
 - **error_context:**  Context description for error message

**Returns:**
> Extracted ID string

**Raises:**
 - **IRPAPIError:**  If Location header is missing

#### `decode_base64_field`

```python
def decode_base64_field(encoded_value: str, field_name: str) -> str
```

Decode a base64-encoded field value.

**Arguments:**
 - **encoded_value:**  Base64-encoded string
 - **field_name:**  Field name for error message

**Returns:**
> Decoded string

**Raises:**
 - **IRPAPIError:**  If decoding fails

#### `decode_presign_params`

```python
def decode_presign_params(presign_params: Dict[str, Any]) -> Dict[str, str]
```

Decode base64 credentials from MRI import file credentials response.

**Arguments:**
 - **presign_params:**  Response JSON containing encoded credentials

**Returns:**
> Dict with decoded credential fields

**Raises:**
 - **IRPAPIError:**  If required fields missing or decoding fails

#### `paginate_search`

```python
def paginate_search(
    fetch: Callable[[int, int], List[Any]],
    description: str,
    limit: int = 100,
    max_pages: int = 1000
) -> List[Any]
```

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

**Arguments:**
 - **fetch:**  Callable taking (limit, offset) and returning one page of results
 - **description:**  Phrase naming the search, used in log and error messages
 - **limit:**  Page size to request (default: 100)
 - **max_pages:**  Hard ceiling on requests before giving up (default: 1000)

**Returns:**
> Every record the operation returned, in page order

**Raises:**
 - **IRPAPIError:**  If the walk cannot establish that it read every page,
   because the operation repeated a page or because ``max_pages`` was
   exhausted with pages still coming back full
 - Whatever ``fetch`` raises, unchanged

#### `extract_analysis_id_from_workflow_response`

```python
def extract_analysis_id_from_workflow_response(workflow: Dict[str, Any]) -> Optional[str]
```

Extract analysis ID from workflow response.

**Arguments:**
 - **workflow:**  Workflow response dict

**Returns:**
> Analysis ID if found, None otherwise

**Raises:**
 - **IRPAPIError:**  If required fields are missing from workflow response

---

## `irp_integration.constants`

API endpoint constants and status/code maps for the Risk Modeler API.

**Defines:**

- Endpoint path templates, grouped by area. Most contain ``str.format`` placeholders (e.g. ``{exposureId}``, ``{jobId}``) that callers fill in with resource IDs before issuing the request.
- Workflow status groupings: ``WORKFLOW_COMPLETED_STATUSES`` (terminal) and ``WORKFLOW_IN_PROGRESS_STATUSES`` (non-terminal). See ``client.py`` for how these drive polling and the terminal-status contract.
- Code maps that translate human-readable names to the short API codes: ``TREATY_TYPES``, ``TREATY_ATTACHMENT_BASES``, and ``TREATY_ATTACHMENT_LEVELS``.
- ``PERSPECTIVE_CODES``: the financial perspective codes the analysis result endpoints accept as ``perspectiveCode``. See ``analysis.py`` for how ``get_elt()``, ``get_ep()``, ``get_stats()``, and ``get_plt()`` validate against it.
- ``EXPOSURE_RESOURCE_TYPES``: the values the same endpoints accept as ``exposureResourceType``; the four getters validate their ``exposure_resource_type`` keyword against it.
- ``ANALYSIS_TYPES``: the values ``CREATE_ANALYSIS_JOB`` accepts as the job ``type``; ``submit_portfolio_analysis_job()`` validates its ``analysis_type`` keyword against it.

---
