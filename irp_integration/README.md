# irp-integration

A Python client library for the [Moody's Intelligent Risk Platform (IRP) APIs](https://developer.rms.com/). Built to serve as a foundation for larger Moody's integration projects — use it with Jupyter Notebooks, Azure Functions, or any orchestration layer to build end-to-end risk analysis workflows.

Not all Moody's API functionality is covered yet, but the most common operations are available and the library is actively maintained. Contributions are welcome — feel free to fork and modify to fit your project's needs.

## Installation

```bash
pip install irp-integration
```

To include Data Bridge (SQL Server) support:

```bash
pip install irp-integration[databridge]
```

> **Note:** Data Bridge requires [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) to be installed on your system.

## Quick Start

```python
from irp_integration import IRPClient

# Requires environment variables (see Configuration below)
client = IRPClient()

# Search EDMs
edms = client.edm.search_edms(filter = f'exposureName = "my_edm"')

# Get portfolios for an EDM
edm = edms[0]
exposure_id = edm['exposureId']
portfolios = client.portfolio.search_portfolios(exposure_id = exposure_id)

# Run analysis on a portfolio
edm_name = edm['exposureName']
portfolio = portfolios[0]
portfolio_name = portfolio['portfolioName']
client.analysis.submit_portfolio_analysis_job(
    edm_name=edm_name,
    portfolio_name=portfolio_name,
    job_name="Readme Analysis",
    analysis_profile_name='US Hurricane HD',
    output_profile_name='Standard Output Profile',
    event_rate_scheme_name='RMS Default',
    treaty_names=['Working Excess Treaty 1'],
    tag_names=['Tag1', 'Tag2']
)
```

### Submit by id

A caller that already holds the Risk Modeler ids passes them in place of the
profile and scheme names. No reference-data request is made, so the job `type`
has to be given as `analysis_type`. A `'DLM'` job requires
`event_rate_scheme_id`; an `'HD'` job may omit it. Names and ids cannot be
mixed in one call.

```python
client.analysis.submit_portfolio_analysis_job(
    edm_name=edm_name,
    portfolio_name=portfolio_name,
    job_name="Readme Analysis by id",
    model_profile_id=123,
    output_profile_id=45,
    event_rate_scheme_id=678,
    analysis_type='HD',
)
```

### Analysis grouping

Grouping uses a two-step contract. Inspect exact Platform analysis IDs first,
render any returned event-rate choices or blocking problems, and pass the
inspection fingerprint back when submitting. Submission repeats every read and
creates no job if request-affecting facts changed.

```python
from irp_integration.grouping import (
    EventRateSelection,
    GroupingCurrency,
    GroupingSettings,
    SimulationPeriodsSelection,
    SimulationSetSelection,
)

inspection = client.grouping.inspect(analysis_ids=[12345, 12346])

# Populate both mappings from the caller's selections.
selected_event_rate_scheme_ids = {...}
selected_simulation_set_ids = {...}

selections = tuple(
    EventRateSelection(
        partition=partition.key,
        event_rate_scheme_id=selected_event_rate_scheme_ids[partition.key],
    )
    for partition in inspection.partitions
    if partition.event_rate_selection_required
)

simulation_selections = tuple(
    SimulationSetSelection(
        partition=partition.key,
        simulation_set_id=selected_simulation_set_ids[partition.key],
    )
    for partition in inspection.partitions
    if partition.simulation_set_selection_required
)

# Optional: one simulationPeriods value per partition of a PLT group.
periods_selections = tuple(
    SimulationPeriodsSelection(partition=partition.key, simulation_periods=50000)
    for partition in inspection.partitions
) if inspection.simulate_to_plt else ()

submission = client.grouping.submit(
    analysis_ids=inspection.analysis_ids,
    settings=GroupingSettings(
        analysis_name="Example Group",
        currency=GroupingCurrency(
            code="USD",
            scheme="RMS",
            vintage="RL25",
            as_of_date="2026-01-01",
        ),
        propagate_detailed_losses=True,
        num_of_simulations=50000,
    ),
    event_rate_selections=selections,
    expected_inspection_fingerprint=inspection.fingerprint,
    simulation_set_selections=simulation_selections,
    simulation_periods_selections=periods_selections,
)
```

Member event-rate schemes are facts used to detect a conflict. For a conflicting
partition whose ELT regions all come from DLM analyses, inspection returns every
active Risk Modeler event-rate scheme with the partition's `perilCode`,
`modelRegionCode`, and `modelVersionCode`, including applicable schemes not used
by a selected member. A conflicting partition with an HD ELT region retains the
peril and model-region comparison. Whether the group outputs ELT or PLT does not
change either comparison, so a DLM partition in a mixed HD and DLM group is
compared by `modelVersionCode`. A partition with one observed scheme keeps the
observed scheme and requires no caller selection. A conflicting partition that no applicable active scheme
matches returns `event_rate_scheme_mapping_missing` in
`inspection.blocking_problems`. Risk Modeler reference data provides the option
labels.

The package owns no event-rate or simulation-set preferences or defaults. The
package does not choose simulation counts, currency, detailed-loss settings,
windows, or a grouping set. Inspection returns the simulation-set choices Risk
Modeler presents for every ELT peril/region/model-version partition converted to
PLT. A simulation set's `eventRateSchemeId` is descriptive; the simulation-set
choice does not restrict the event-rate-scheme choice. A PLT member keeps its
own `petId` and does not require a simulation-set choice. A
`SimulationPeriodsSelection` sets
`regionPerilSimulationSet[].simulationPeriods` for one partition of a PLT group;
without one, a PLT row keeps its PET's period count and a converted ELT row keeps
the chosen set's `defaultPeriods`.

Inspection
compares loss-affecting terms for treaties that share a Treaty Number. Any
inconsistency is returned in `inspection.warnings`; it does not block grouping.
Treaty IDs, display names, producers, premiums, user-defined fields, tags, and
URIs are not part of the comparison.

## Configuration

The library reads configuration from environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `RISK_MODELER_BASE_URL` | Yes | Moody's Risk Modeler API base URL |
| `RISK_MODELER_RESOURCE_GROUP_ID` | Yes | Resource group ID for your organization |
| `RISK_MODELER_API_KEY` | Auth | API authentication key (API-key strategy) |
| `RISK_MODELER_TENANT_NAME` | Auth | Tenant name (bearer-login strategy) |
| `RISK_MODELER_USERNAME` | Auth | Username (bearer-login strategy) |
| `RISK_MODELER_PASSWORD` | Auth | Password (bearer-login strategy) |

See [Authentication](#authentication) for how a strategy is selected.

You can set these in your shell, or use a `.env` file with [python-dotenv](https://pypi.org/project/python-dotenv/):

```python
from dotenv import load_dotenv
load_dotenv()

from irp_integration import IRPClient
client = IRPClient()
```

### Authentication

`RISK_MODELER_BASE_URL` and `RISK_MODELER_RESOURCE_GROUP_ID` are always required.
On top of those, the client supports two authentication strategies and selects
one automatically based on which environment variables are populated:

- **API key** (default, preserves existing behavior): set `RISK_MODELER_API_KEY`.
  It is sent verbatim in the `Authorization` header.
- **Bearer login**: leave `RISK_MODELER_API_KEY` unset and set all three of
  `RISK_MODELER_TENANT_NAME`, `RISK_MODELER_USERNAME`, and `RISK_MODELER_PASSWORD`.
  The client logs in at construction to obtain a short-lived (1-hour) bearer token
  and sends `Authorization: Bearer {accessToken}`.

**Precedence:** if `RISK_MODELER_API_KEY` is set it always wins, even when the
login variables are also present. Bearer login is used only when the API key is
absent and the full login triple is configured. If neither complete set is
configured, `IRPClient()` raises an error naming both options.

**Token refresh** is reactive: when a request returns `401` in bearer mode, the
client re-logs in with the stored credentials and retries the request once. If
the retry still fails, the error propagates. There is no proactive expiry
tracking.

`client.export_job.download_export_results()` sends the configured
`Authorization` header when it requests the export job's `downloadUrl`, unless
`downloadUrl` is a presigned S3 URL. The download also uses the client's retry
policy.

### Data Bridge Configuration

The Data Bridge module (`client.databridge`) connects directly to Moody's SQL Server databases via ODBC. It requires separate setup from the REST API.

**Prerequisites:**

1. Install the optional dependency: `pip install irp-integration[databridge]`
2. Install [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server):
   - **Windows:** Download and run the MSI installer from Microsoft
   - **Linux (Debian/Ubuntu):** `sudo apt-get install -y unixodbc-dev && sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18`
   - **macOS:** `brew install microsoft/mssql-release/msodbcsql18`

**Environment variables (per connection):**

Each named connection uses the prefix `MSSQL_{CONNECTION_NAME}_`:

| Variable | Required | Description |
|----------|----------|-------------|
| `MSSQL_DATABRIDGE_SERVER` | Yes | Server hostname or IP |
| `MSSQL_DATABRIDGE_USER` | Yes | SQL Server username |
| `MSSQL_DATABRIDGE_PASSWORD` | Yes | SQL Server password |
| `MSSQL_DATABRIDGE_PORT` | No | Port (default: 1433) |

**Global settings:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MSSQL_DRIVER` | `ODBC Driver 18 for SQL Server` | ODBC driver name |
| `MSSQL_TRUST_CERT` | `yes` | Trust server certificate |
| `MSSQL_TIMEOUT` | `30` | Connection timeout in seconds |

**Example:**

```bash
# .env file
MSSQL_DATABRIDGE_SERVER=databridge.company.com
MSSQL_DATABRIDGE_USER=svc_account
MSSQL_DATABRIDGE_PASSWORD=secretpassword
```

```python
from irp_integration.databridge import DataBridgeManager

dbm = DataBridgeManager()

# Inline query with parameters
df = dbm.execute_query(
    "SELECT * FROM portfolios WHERE value > {{ min_value }}",
    params={'min_value': 1000000},
    database='DataWarehouse'
)

# Execute SQL script from file
results = dbm.execute_query_from_file(
    'C:/sql/extract_policies.sql',
    params={'cycle_name': 'Q1-2025'},
    database='AnalyticsDB'
)
```

## Features

- **Automatic retry** with exponential backoff for transient errors (429, 5xx)
- **Workflow polling** — submit long-running operations and automatically poll to completion
- **Batch workflow execution** — run multiple workflows in parallel and wait for all to finish
- **Structured logging** via Python's `logging` module for visibility into API calls and workflow progress
- **Connection pooling** via persistent HTTP sessions
- **Input validation** with descriptive error messages
- **Custom exception hierarchy** for structured error handling
- **S3 upload/download** with multipart transfer support
- **Data Bridge (SQL Server)** — direct SQL execution against Moody's Data Bridge with parameterized queries and file-based scripts
- **Type hints** on all public methods

## Modules

| Manager | Description |
|---------|-------------|
| `client.edm` | Exposure Data Manager — create, upgrade, duplicate, and delete EDMs |
| `client.portfolio` | Portfolio CRUD, geocoding, and hazard processing |
| `client.mri_import` | MRI (CSV) data import workflow — bucket creation, file upload, mapping, and execution |
| `client.treaty` | Reinsurance treaty creation, LOB assignment, and reference data |
| `client.analysis` | Risk analysis execution, profiles, event rate schemes, and results |
| `client.grouping` | Rules-based analysis-group inspection, submission, and job status |
| `client.rdm` | Results Data Mart — export analysis results to RDM |
| `client.risk_data_job` | Risk data job status tracking |
| `client.import_job` | Platform import job management (EDM/RDM imports) |
| `client.export_job` | Platform export job management — status, polling, and result download |
| `client.databridge` | Data Bridge (SQL Server) — parameterized queries, file-based SQL execution |
| `client.reference_data` | Tags, currencies, and other reference data lookups |

## Error Handling

The library uses a custom exception hierarchy:

```python
from irp_integration.exceptions import (
    IRPIntegrationError,          # Base exception
    IRPAPIError,                  # HTTP/API errors
    IRPValidationError,           # Input validation failures
    IRPWorkflowError,             # Workflow execution failures
    IRPReferenceDataError,        # Reference data lookup failures
    IRPFileError,                 # File operation failures
    IRPJobError,                  # Job management errors
    IRPDataBridgeError,           # Data Bridge base error
    IRPDataBridgeConnectionError, # SQL Server connection failures
    IRPDataBridgeQueryError,      # SQL query execution failures
)
```

## API Documentation

For detailed API documentation, see [docs/api.md](https://github.com/premiumiq/irp-integration/blob/main/docs/api.md).

`docs/api.md` is generated from the source docstrings and type hints, so it never drifts from the code. To regenerate it after changing docstrings:

```bash
pip install "irp-integration[dev,databridge]"
python docs/generate_api_docs.py
```

The `databridge` extra is required: the generator introspects every module, including `databridge`, so its optional dependencies must be importable. CI regenerates with the same extras and fails if the committed `docs/api.md` differs.

## License

This project is licensed under the MIT License — see the [LICENSE](https://github.com/premiumiq/irp-integration/blob/main/LICENSE) file for details.
