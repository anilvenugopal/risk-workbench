# AGENTS.md

Orientation for coding agents and contributors browsing this repository. The
authoritative guidance travels in the source itself — this file only points at it.

## Where things live

- **Entry point & manager map:** `irp_integration/__init__.py` (module docstring).
- **Cross-cutting HTTP / workflow contracts:** `irp_integration/client.py` (module docstring).
- **Per-area operations:** one manager module each — `edm.py`, `portfolio.py`,
  `mri_import.py`, `treaty.py`, `analysis.py`, `rdm.py`, `reference_data.py`, etc.
- **Generated API reference:** `irp_integration/docs/api.md` — regenerate with
  `uv run python irp_integration/docs/generate_api_docs.py` (never edit by hand).
- **Configuration and environment variables:** `irp_integration/README.md`
  (§Configuration, §Authentication, §Data Bridge Configuration).

## Contracts to respect (detailed in `client.py`)

- **Every request goes through the client** — call `Client.request()` or
  `Client.execute_workflow()`. Manager methods build the payload and read the
  response; they do not construct their own `requests` call or session.
- **Don't double-wrap retries** — the HTTP session already retries 429/5xx with
  exponential backoff across all methods.
- **Check terminal status after polling** — a poll returns on any terminal state
  (`FINISHED`, `FAILED`, or `CANCELLED`); inspect the returned `status` rather
  than assuming success.
- **Names resolve to IDs** — high-level methods accept human-readable names (EDM,
  portfolio, profile, treaty) and look up the IDs internally.
  `submit_portfolio_analysis_job()` also accepts `model_profile_id`,
  `output_profile_id`, `event_rate_scheme_id` and `analysis_type` in place of
  the profile and scheme names.

## Conventions & checks

- Docstrings and type hints are the single source of truth; `docs/api.md` is
  generated from them.
- Google-style docstrings, summary on the second line; `py.typed` ships with the
  package.
- Lint with the Workbench's ruff config in the root `pyproject.toml`:
  `uv run ruff check irp_integration`.
- Endpoint paths and status values come from `constants.py`, not from string
  literals at the call site.
- Resource IDs come from the `location` header — use
  `extract_id_from_location_header()` in `utils.py`. Do not read the ID out of
  the response body.
- Validate arguments at function entry with the `validate_*` helpers in
  `validators.py`.
- Raise the exceptions defined in `exceptions.py`. Each message names what it
  concerns — the EDM, portfolio, analysis, or job ID, and the status.
- Imports go at the top of the module. Move an import into a function only to
  break an import cycle.
- Prefer the straightforward implementation over an abstraction that hides what
  the API does.

## Writing Style

Follow the Writing Style section of the Workbench [AGENTS.md](../AGENTS.md).
In this package also:

- Name the manager, method, endpoint constant, workflow, job, or module instead
  of a structural metaphor.
- Avoid `entity`, `component`, `layer`, and `handle` as vague stand-ins.
- Name the EDM, exposure set, portfolio, account, policy, location, treaty,
  analysis, workflow, job, manager method, endpoint constant, response field, or
  environment variable directly.
- Use the API's own terms when writing about API behavior: `exposureId`,
  `location` header, `FINISHED`, `allowDeepFilters`. Do not paraphrase them.
- Be specific: the parameter name, the status value, the limit.

Bad:

> `client.py` is the backbone of the package and the managers are the glue.

Better:

> `client.py` owns the HTTP session, the retry policy, and workflow polling. Each manager module builds request payloads and calls `Client.request()` or `Client.execute_workflow()`.

Bad:

> `execute_workflow()` returns the object once the operation settles.

Better:

> `execute_workflow()` polls until the workflow reaches `FINISHED`, `FAILED`, or `CANCELLED`, then returns the workflow response. Check `status` — a terminal state is not a success.

Bad:

> Retries are handled at the appropriate layer.

Better:

> The `requests.Session` in `client.py` retries 429, 500, 502, 503, and 504 with exponential backoff. Manager methods do not retry.

Bad:

> The existing flow already handles this.

Better:

> `poll_workflow_batch_to_completion()` polls every workflow ID in one loop; call it instead of looping over `poll_workflow_to_completion()`.

Bad:

> Searches return a limited number of results per call.

Better:

> `search_portfolios()` returns at most `limit` portfolios per call (default 100). `search_portfolios_paginated()` advances `offset` until a short page comes back.

Bad:

> Update this to reflect the decision above.

Better:

> Update the `search_locations()` docstring to state that `state` is filtered through the Search Locations route, not through `allowDeepFilters`.

Bad:

> The manager surfaces a friendly error when the lookup fails.

Better:

> `get_analysis_by_name()` raises `IRPValidationError` when `analysis_name` or `edm_name` is empty, and `IRPAPIError` when the search returns zero or more than one analysis. Both messages name the analysis and the EDM.
