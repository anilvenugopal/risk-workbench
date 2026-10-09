"""
Portfolio management operations.

Handles portfolio creation, retrieval, and geocoding/hazard operations.
"""

import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING
from zoneinfo import ZoneInfo

from .constants import GET_PORTFOLIO_BY_ID, GET_PORTFOLIO_METADATA, CREATE_PORTFOLIO, GET_GEOHAZ_JOB, SEARCH_PORTFOLIOS, GEOHAZ_PORTFOLIO, WORKFLOW_COMPLETED_STATUSES, WORKFLOW_IN_PROGRESS_STATUSES, SEARCH_ACCOUNTS_BY_PORTFOLIO, SEARCH_ACCOUNTS, SEARCH_POLICIES, SEARCH_LOCATIONS, ADD_FILTERED_ACCOUNTS, MANAGE_ACCOUNTS_BY_PORTFOLIO, PORTFOLIO_NAME_MAX_LENGTH, PORTFOLIO_NUMBER_MAX_LENGTH
from .exceptions import IRPAPIError, IRPJobError, IRPValidationError
from .validators import validate_geohaz_layers, validate_list_not_empty, validate_list_of_positive_ints, validate_max_length, validate_non_empty_string, validate_non_negative_int, validate_positive_int
from .utils import extract_id_from_location_header, paginate_search

if TYPE_CHECKING:
    from . import IRPClient
    from .edm import EDMManager

logger = logging.getLogger(__name__)


class PortfolioManager:
    """Manager for portfolio operations."""

    def __init__(self, irp: "IRPClient") -> None:
        """
        Initialize portfolio manager.

        Args:
            irp: Owning IRP client instance
        """
        self._irp = irp
        self.client = irp.client

    @property
    def edm_manager(self) -> "EDMManager":
        """Return the owning client's EDM manager."""
        return self._irp.edm


    def get_portfolio_by_id(self, exposure_id: int, portfolio_id: int) -> Dict[str, Any]:
        """
        Retrieve portfolio details by portfolio ID.

        Args:
            exposure_id: Exposure ID
            portfolio_id: Portfolio ID

        Returns:
            Dict containing portfolio details

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(portfolio_id, "portfolio_id")

        try:
            response = self.client.request('GET', GET_PORTFOLIO_BY_ID.format(exposureId=exposure_id, id=portfolio_id))
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get portfolio details for exposure ID '{exposure_id}' and portfolio ID '{portfolio_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e
        
    def get_portfolio_metadata(self, exposure_id: int, portfolio_id: int) -> Dict[str, Any]:
        """
        Retrieve portfolio metadata by portfolio ID.

        Args:
            exposure_id: Exposure ID
            portfolio_id: Portfolio ID

        Returns:
            Dict containing portfolio metadata details

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(portfolio_id, "portfolio_id")

        try:
            response = self.client.request('GET', GET_PORTFOLIO_METADATA.format(exposureId=exposure_id, id=portfolio_id))
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get portfolio metadata for exposure ID '{exposure_id}' and portfolio ID '{portfolio_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e
    
    def search_portfolios(self, exposure_id: int, filter: str = "", limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Search portfolios within an exposure.

        Args:
            exposure_id: Exposure ID
            filter: Optional filter string for portfolio names
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of portfolio dictionaries
        """
        validate_positive_int(exposure_id, "exposure_id")

        params: Dict[str, Any] = {'limit': limit, 'offset': offset}
        if filter:
            params['filter'] = filter

        try:
            response = self.client.request(
                'GET',
                SEARCH_PORTFOLIOS.format(exposureId=exposure_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to search portfolios for exposure ID '{exposure_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def search_portfolios_paginated(self, exposure_id: int, filter: str = "") -> List[Dict[str, Any]]:
        """
        Search all portfolios within an exposure with automatic pagination.

        Fetches all pages of results matching the filter criteria, paging via
        ``paginate_search``.

        Args:
            exposure_id: Exposure ID
            filter: Optional filter string for portfolio names

        Returns:
            Complete list of all matching portfolios across all pages

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        validate_positive_int(exposure_id, "exposure_id")

        return paginate_search(
            lambda limit, offset: self.search_portfolios(
                exposure_id=exposure_id,
                filter=filter,
                limit=limit,
                offset=offset
            ),
            f"Portfolio search for exposure ID {exposure_id}"
        )


    def search_accounts_by_portfolio(
        self,
        exposure_id: int,
        portfolio_id: int,
        filter: str = "",
        sort: str = "",
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve accounts within a portfolio.

        Filterable properties are a closed list: accountid (the API's own
        spelling), accountName, accountNumber, branchName, cedantName,
        ownerName, producerName, underwriterName. Line of business and state are
        not among them; see ``search_policies`` and ``search_locations``.

        Args:
            exposure_id: Exposure ID
            portfolio_id: Portfolio ID
            filter: Optional filter expression over the properties listed above
            sort: Optional comma-delimited sort properties, each optionally
                suffixed with ASC or DESC
            limit: Maximum results per page; omitted from the request when
                None, since the API does not declare limit/offset for this
                operation (default: None)
            offset: Offset for pagination; omitted from the request when None
                (default: None)

        Returns:
            List of account dicts

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(portfolio_id, "portfolio_id")
        if limit is not None:
            validate_positive_int(limit, "limit")
        if offset is not None:
            validate_non_negative_int(offset, "offset")

        params: Dict[str, Any] = {}
        if filter:
            params['filter'] = filter
        if sort:
            params['sort'] = sort
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset

        try:
            response = self.client.request(
                'GET',
                SEARCH_ACCOUNTS_BY_PORTFOLIO.format(exposureId=exposure_id, id=portfolio_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to search portfolio accounts for exposure ID '{exposure_id}' and portfolio ID '{portfolio_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def search_accounts_by_portfolio_paginated(
        self,
        exposure_id: int,
        portfolio_id: int,
        filter: str = "",
        sort: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all accounts within a portfolio with automatic pagination.

        Fetches all pages of results matching the filter criteria, paging via
        ``paginate_search``. This operation does not declare limit/offset, so a
        response larger than the requested limit means it ignored them and was
        already complete; ``paginate_search`` returns that response as-is.

        Args:
            exposure_id: Exposure ID
            portfolio_id: Portfolio ID
            filter: Optional filter expression; see
                ``search_accounts_by_portfolio`` for supported properties
            sort: Optional comma-delimited sort properties

        Returns:
            Complete list of all matching accounts across all pages

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(portfolio_id, "portfolio_id")

        return paginate_search(
            lambda limit, offset: self.search_accounts_by_portfolio(
                exposure_id=exposure_id,
                portfolio_id=portfolio_id,
                filter=filter,
                sort=sort,
                limit=limit,
                offset=offset
            ),
            f"Account search for portfolio ID {portfolio_id}"
        )

    def search_accounts(
        self,
        exposure_id: int,
        filter: str = "",
        sort: str = "",
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
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

        Args:
            exposure_id: Exposure ID
            filter: Optional filter expression over the account properties
            sort: Optional comma-delimited sort properties, each optionally
                suffixed with ASC or DESC
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of account dicts

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(limit, "limit")
        validate_non_negative_int(offset, "offset")

        # Sent explicitly, and only ever false: see the docstring on why deep
        # filters are not offered. Lowercase because requests renders a Python
        # bool as "True"/"False", which the API rejects.
        params: Dict[str, Any] = {
            'limit': limit,
            'offset': offset,
            'allowDeepFilters': 'false'
        }
        if filter:
            params['filter'] = filter
        if sort:
            params['sort'] = sort

        try:
            response = self.client.request(
                'GET',
                SEARCH_ACCOUNTS.format(exposureId=exposure_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to search accounts for exposure ID '{exposure_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def search_accounts_paginated(
        self,
        exposure_id: int,
        filter: str = "",
        sort: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Search all accounts within an EDM with automatic pagination.

        Fetches all pages of results matching the filter criteria, paging via
        ``paginate_search``, which documents what the API's ``offset`` counts
        and the guards around that.

        Args:
            exposure_id: Exposure ID
            filter: Optional filter expression; see ``search_accounts`` for the
                supported properties and grammar
            sort: Optional comma-delimited sort properties

        Returns:
            Complete list of all matching accounts across all pages

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        validate_positive_int(exposure_id, "exposure_id")

        return paginate_search(
            lambda limit, offset: self.search_accounts(
                exposure_id=exposure_id,
                filter=filter,
                sort=sort,
                limit=limit,
                offset=offset
            ),
            f"Account search for exposure ID {exposure_id}"
        )

    def search_policies(
        self,
        exposure_id: int,
        filter: str = "",
        sort: str = "",
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
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

        Args:
            exposure_id: Exposure ID
            filter: Optional filter expression; see ``search_accounts`` for the
                grammar and the searchPolicies reference for the filterable
                properties
            sort: Optional comma-delimited sort properties, each optionally
                suffixed with ASC or DESC
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of policy dicts, each carrying accountId and a nested
            lob dict of {lobId, lobName, uri}

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(limit, "limit")
        validate_non_negative_int(offset, "offset")

        params: Dict[str, Any] = {'limit': limit, 'offset': offset}
        if filter:
            params['filter'] = filter
        if sort:
            params['sort'] = sort

        try:
            response = self.client.request(
                'GET',
                SEARCH_POLICIES.format(exposureId=exposure_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to search policies for exposure ID '{exposure_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def search_policies_paginated(
        self,
        exposure_id: int,
        filter: str = "",
        sort: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Search all policies within an EDM with automatic pagination.

        Fetches all pages of results matching the filter criteria, paging via
        ``paginate_search``.

        Args:
            exposure_id: Exposure ID
            filter: Optional filter expression; see ``search_policies`` for the
                supported properties
            sort: Optional comma-delimited sort properties

        Returns:
            Complete list of all matching policies across all pages

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        validate_positive_int(exposure_id, "exposure_id")

        return paginate_search(
            lambda limit, offset: self.search_policies(
                exposure_id=exposure_id,
                filter=filter,
                sort=sort,
                limit=limit,
                offset=offset
            ),
            f"Policy search for exposure ID {exposure_id}"
        )

    def search_locations(
        self,
        exposure_id: int,
        filter: str = "",
        sort: str = "",
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
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

        Args:
            exposure_id: Exposure ID
            filter: Optional filter expression; see ``search_accounts`` for the
                grammar and the searchLocations reference for the filterable
                properties
            sort: Optional comma-delimited sort properties, each optionally
                suffixed with ASC or DESC
            limit: Maximum results per page (default: 100)
            offset: Offset for pagination (default: 0)

        Returns:
            List of location search item dicts, each wrapping a nested
            location dict

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(limit, "limit")
        validate_non_negative_int(offset, "offset")

        params: Dict[str, Any] = {'limit': limit, 'offset': offset}
        if filter:
            params['filter'] = filter
        if sort:
            params['sort'] = sort

        try:
            response = self.client.request(
                'GET',
                SEARCH_LOCATIONS.format(exposureId=exposure_id),
                params=params
            )
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to search locations for exposure ID '{exposure_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def search_locations_paginated(
        self,
        exposure_id: int,
        filter: str = "",
        sort: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Search all locations within an EDM with automatic pagination.

        Fetches all pages of results matching the filter criteria, paging via
        ``paginate_search``. Because that helper detects progress by hashing
        page content, it does not depend on this operation's nested response
        shape being what the spec describes.

        Args:
            exposure_id: Exposure ID
            filter: Optional filter expression; see ``search_locations`` for
                the supported properties
            sort: Optional comma-delimited sort properties

        Returns:
            Complete list of all matching location search items across all
            pages

        Raises:
            IRPValidationError: If parameters are invalid
            IRPAPIError: If a request fails, or if pagination cannot be shown to
                have read every page
        """
        validate_positive_int(exposure_id, "exposure_id")

        return paginate_search(
            lambda limit, offset: self.search_locations(
                exposure_id=exposure_id,
                filter=filter,
                sort=sort,
                limit=limit,
                offset=offset
            ),
            f"Location search for exposure ID {exposure_id}"
        )

    def add_filtered_accounts(
        self,
        exposure_id: int,
        portfolio_id: int,
        *,
        marked_accounts: Optional[List[int]] = None,
        query_filter: str = "",
        select_all: bool = False,
        manage_existing_accounts: bool = False
    ) -> Dict[str, Any]:
        """
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

        Args:
            exposure_id: Exposure ID
            portfolio_id: Portfolio ID
            marked_accounts: Account IDs to add (default: None)
            query_filter: Expression selecting the accounts to add, passed
                through unmodified (default: "")
            select_all: Add every account matched by query_filter, or every
                account in the EDM when query_filter is empty. Overrides
                marked_accounts (default: False)
            manage_existing_accounts: Restrict the operation to accounts
                already in the portfolio. Discards marked_accounts and
                query_filter, so it cannot be combined with either
                (default: False)

        Returns:
            Parsed response body, or an empty dict when the response has none

        Raises:
            IRPValidationError: If parameters are invalid, if no accounts were
                selected by any means, or if manage_existing_accounts is
                combined with a selection it would discard
            IRPAPIError: If the request fails or returns an unexpected status
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(portfolio_id, "portfolio_id")

        if marked_accounts is None:
            marked_accounts = []
        validate_list_of_positive_ints(marked_accounts, "marked_accounts")

        # An empty filter with selectAll would add every account in the EDM,
        # so that has to be asked for explicitly rather than fallen into
        if not marked_accounts and not query_filter and not select_all:
            raise IRPValidationError(
                "No accounts selected: provide marked_accounts or query_filter, "
                "or pass select_all=True to add every account in the EDM"
            )

        # The API answers 200 and adds nothing for this combination
        if manage_existing_accounts and (marked_accounts or query_filter or select_all):
            raise IRPValidationError(
                "manage_existing_accounts discards marked_accounts and query_filter, "
                "so this call would add nothing; omit it to add accounts"
            )

        data: Dict[str, Any] = {
            "selectAll": select_all,
            "queryFilter": query_filter,
            "markedAccounts": marked_accounts,
            "manageExistingAccounts": manage_existing_accounts,
        }

        try:
            logger.info(
                "Adding filtered accounts to portfolio ID %s (marked accounts: %s, select all: %s)",
                portfolio_id, len(marked_accounts), select_all
            )
            response = self.client.request(
                'PUT',
                ADD_FILTERED_ACCOUNTS.format(exposureId=exposure_id, id=portfolio_id),
                json=data
            )
        except Exception as e:
            raise IRPAPIError(
                f"Failed to add filtered accounts to portfolio ID '{portfolio_id}' in exposure ID '{exposure_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

        if response.status_code != 200:
            raise IRPAPIError(
                f"Unexpected status {response.status_code} adding filtered accounts to portfolio ID "
                f"'{portfolio_id}' (expected 200); location header: {response.headers.get('location')}"
            )

        try:
            return response.json()
        except ValueError:
            # The documented 200 carries no body
            return {}

    def manage_portfolio_accounts(
        self,
        exposure_id: int,
        portfolio_id: int,
        *,
        accounts_to_add: Optional[List[int]] = None,
        accounts_to_remove: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
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

        Args:
            exposure_id: Exposure ID
            portfolio_id: Portfolio ID
            accounts_to_add: Account IDs to add to the portfolio
                (default: None)
            accounts_to_remove: Account IDs to remove from the portfolio
                (default: None)

        Returns:
            Dict of add/remove counts as shown above, or an empty dict when the
            response has no body

        Raises:
            IRPValidationError: If parameters are invalid, or if both account
                lists are empty
            IRPAPIError: If the request fails or returns an unexpected status
        """
        validate_positive_int(exposure_id, "exposure_id")
        validate_positive_int(portfolio_id, "portfolio_id")

        if accounts_to_add is None:
            accounts_to_add = []
        if accounts_to_remove is None:
            accounts_to_remove = []
        validate_list_of_positive_ints(accounts_to_add, "accounts_to_add")
        validate_list_of_positive_ints(accounts_to_remove, "accounts_to_remove")

        if not accounts_to_add and not accounts_to_remove:
            raise IRPValidationError(
                "No accounts specified: provide at least one account ID in "
                "accounts_to_add or accounts_to_remove"
            )

        # Both fields are required by the API, so send both even when empty
        data: Dict[str, Any] = {
            "accountsToAdd": accounts_to_add,
            "accountsToRemove": accounts_to_remove,
        }

        try:
            logger.info(
                "Managing accounts on portfolio ID %s (adding: %s, removing: %s)",
                portfolio_id, len(accounts_to_add), len(accounts_to_remove)
            )
            response = self.client.request(
                'PATCH',
                MANAGE_ACCOUNTS_BY_PORTFOLIO.format(exposureId=exposure_id, id=portfolio_id),
                json=data
            )
        except Exception as e:
            raise IRPAPIError(
                f"Failed to manage accounts on portfolio ID '{portfolio_id}' in exposure ID '{exposure_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

        if response.status_code != 200:
            raise IRPAPIError(
                f"Unexpected status {response.status_code} managing accounts on portfolio ID "
                f"'{portfolio_id}' (expected 200); location header: {response.headers.get('location')}"
            )

        try:
            result = response.json()
        except ValueError:
            return {}

        logger.info("Portfolio ID %s accounts managed — %s", portfolio_id, result)
        return result


    def create_portfolios(self, portfolio_data_list: List[Dict[str, Any]]) -> List[int]:
        """
        Create multiple portfolios.

        Args:
            portfolio_data_list: List of portfolio data dicts, each containing:
                - edm_name: str
                - portfolio_name: str (at most 40 characters)
                - portfolio_number: str (at most 20 characters)
                - description: str

        Returns:
            List of portfolio IDs

        Raises:
            IRPValidationError: If portfolio_data_list is empty or invalid, if a
                name field exceeds its length limit, or if any portfolio name is
                already taken in its EDM
            IRPAPIError: If portfolio creation fails
        """
        validate_list_not_empty(portfolio_data_list, "portfolio_data_list")

        portfolio_ids = []
        for portfolio_data in portfolio_data_list:
            try:
                edm_name = portfolio_data['edm_name']
                portfolio_name = portfolio_data['portfolio_name']
                portfolio_number = portfolio_data['portfolio_number']
                description = portfolio_data['description']
            except (KeyError, TypeError) as e:
                raise IRPAPIError(
                    f"Missing value in create portfolio data: {e}"
                ) from e
            
            # Returns tuple of (portfolio_id, request_body) - we only need portfolio_id here
            portfolio_id, _ = self.create_portfolio(
                edm_name=edm_name,
                portfolio_name=portfolio_name,
                portfolio_number=portfolio_number,
                description=description
            )
            portfolio_ids.append(portfolio_id)

        return portfolio_ids


    def create_portfolio(
        self,
        edm_name: str,
        portfolio_name: str,
        portfolio_number: str = "",
        description: str = ""
    ) -> Tuple[int, Dict[str, Any]]:
        """
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

        Args:
            edm_name: Name of EDM datasource
            portfolio_name: Name for new portfolio, at most 40 characters
            portfolio_number: Portfolio number, at most 20 characters; defaults
                to portfolio_name when empty, which then has to be within that
                limit too (default: "")
            description: Portfolio description; an auto-generated description is
                used when empty (default: "")

        Returns:
            Tuple of (portfolio_id, request_body) where request_body is the HTTP request payload

        Raises:
            IRPValidationError: If inputs are invalid, if either name field
                exceeds its length limit, or if a portfolio with this name
                already exists in the EDM
            IRPAPIError: If the EDM lookup or the creation request fails
        """
        validate_non_empty_string(edm_name, "edm_name")
        validate_non_empty_string(portfolio_name, "portfolio_name")
        validate_max_length(portfolio_name, "portfolio_name", PORTFOLIO_NAME_MAX_LENGTH)

        # Checked before the lookups below so an over-long name costs no requests
        if portfolio_number:
            validate_max_length(portfolio_number, "portfolio_number", PORTFOLIO_NUMBER_MAX_LENGTH)
        elif len(portfolio_name) > PORTFOLIO_NUMBER_MAX_LENGTH:
            raise IRPValidationError(
                f"portfolio_number was not supplied, so it defaults to portfolio_name, "
                f"which is {len(portfolio_name)} characters and exceeds the "
                f"{PORTFOLIO_NUMBER_MAX_LENGTH}-character limit. Pass portfolio_number "
                f"explicitly."
            )

        edms = self.edm_manager.search_edms(filter=f"exposureName=\"{edm_name}\"")
        if (len(edms) != 1):
            raise IRPAPIError(f"Expected 1 EDM with name {edm_name}, found {len(edms)}")
        try:
            exposure_id = edms[0]['exposureId']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(
                f"Failed to extract exposure ID for EDM '{edm_name}': {e}"
            ) from e

        portfolios = self.search_portfolios(exposure_id=exposure_id, filter=f"portfolioName=\"{portfolio_name}\"")
        if (len(portfolios) > 0):
            raise IRPValidationError(f"{len(portfolios)} portfolios found with name {portfolio_name}, please use a unique name")

        if not portfolio_number:
            portfolio_number = portfolio_name
        if not description:
            description = "Portfolio created via API on " + datetime.now(tz=ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S %Z")

        data = {
            "portfolioName": portfolio_name,
            "portfolioNumber": portfolio_number,
            "description": description,
        }

        try:
            logger.info("Creating portfolio '%s' in exposure ID %s", portfolio_name, exposure_id)
            response = self.client.request('POST', CREATE_PORTFOLIO.format(exposureId=exposure_id), json=data)
            portfolio_id = extract_id_from_location_header(response, "portfolio creation")
            logger.info("Portfolio created — ID: %s", portfolio_id)
            return int(portfolio_id), data
        except Exception as e:
            raise IRPAPIError(
                f"Failed to create portfolio '{portfolio_name}' in exposure id '{exposure_id}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def submit_geohaz_jobs(self, geohaz_data_list: List[Dict[str, Any]]) -> List[int]:
        """
        Submit multiple geohaz jobs (geocoding and hazard operations).

        Args:
            geohaz_data_list: List of geohaz data dicts, each containing:
                - edm_name: str
                - portfolio_name: str
                - layers: list of GeoHaz layer dictionaries

        Returns:
            List of job IDs

        Raises:
            IRPValidationError: If geohaz_data_list is empty or invalid
            IRPAPIError: If job submission fails or resources not found
        """
        validate_list_not_empty(geohaz_data_list, "geohaz_data_list")

        job_ids = []
        for index, geohaz_data in enumerate(geohaz_data_list):
            entry_name = f"geohaz_data_list[{index}]"
            if not isinstance(geohaz_data, dict):
                raise IRPValidationError(
                    f"{entry_name} must be a dictionary, got "
                    f"{type(geohaz_data).__name__}"
                )
            for field in ("edm_name", "portfolio_name", "layers"):
                if field not in geohaz_data:
                    raise IRPValidationError(
                        f"{entry_name} is missing required field '{field}'"
                    )

            # Returns tuple of (job_id, request_body) - we only need job_id here
            job_id, _ = self.submit_geohaz_job(
                portfolio_name=geohaz_data['portfolio_name'],
                edm_name=geohaz_data['edm_name'],
                layers=geohaz_data['layers']
            )
            job_ids.append(job_id)

        return job_ids
        

    def submit_geohaz_job(self,
                          portfolio_name: str,
                          edm_name: str,
                          layers: List[Dict[str, Any]]
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Execute geocoding and/or hazard operations on portfolio.

        The returned job ID is served by ``/platform/geohaz/v1/jobs``, so poll it
        with ``poll_geohaz_job_to_completion`` (or ``get_geohaz_job`` for one
        status check), not with ``import_job.poll_import_job_to_completion``,
        which answers ``404 Invalid job id`` for a GeoHaz job that is running
        normally.

        Geocoding populates each location's ``admin1Name``, which arrives empty
        from an MRI import; see ``search_locations``.

        Args:
            portfolio_name: Name of the portfolio
            edm_name: Name of the EDM containing the portfolio
            layers: Non-empty list of geocode and/or hazard layer dictionaries.
                Every layer specifies ``type``, ``name``, ``engineType``,
                ``version``, and ``layerOptions``. Geocode options must include
                ``geoLicenseType``, ``aggregateTriggerEnabled``, and
                ``skipPrevGeocoded``. Hazard options must include
                ``overrideUserDef`` and ``skipPrevHazard``.

        Returns:
            Tuple of (job_id, request_body), where job_id is a GeoHaz job ID and
            request_body is the HTTP request payload

        Raises:
            IRPValidationError: If inputs are invalid
            IRPAPIError: If workflow fails or times out
        """
        validate_non_empty_string(portfolio_name, "portfolio_name")
        validate_non_empty_string(edm_name, "edm_name")
        validate_geohaz_layers(layers)

        # Look up EDM to get exposure_id
        edms = self.edm_manager.search_edms(filter=f"exposureName=\"{edm_name}\"")
        if len(edms) != 1:
            raise IRPAPIError(f"Expected 1 EDM with name '{edm_name}', found {len(edms)}")
        try:
            exposure_id = edms[0]['exposureId']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(f"Failed to extract exposure ID for EDM '{edm_name}': {e}") from e

        # Look up portfolio to get portfolio_uri and portfolio_id
        portfolios = self.search_portfolios(exposure_id=exposure_id, filter=f"portfolioName=\"{portfolio_name}\"")
        if len(portfolios) != 1:
            raise IRPAPIError(f"Expected 1 portfolio with name '{portfolio_name}', found {len(portfolios)}")
        try:
            portfolio_uri = portfolios[0]['uri']
            portfolio_id = portfolios[0]['portfolioId']
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(f"Failed to extract portfolio details for portfolio '{portfolio_name}': {e}") from e

        # Check if portfolio has locations to GeoHaz
        accounts = self.search_accounts_by_portfolio(exposure_id=exposure_id, portfolio_id=portfolio_id)
        if len(accounts) == 0:
            raise IRPAPIError(f"Portfolio '{portfolio_name}' does not have any Accounts/Locations to be GeoHaz'd")

        # Validate locations count
        try:
            locations_count = 0
            for account in accounts:
                locations_count += account['locationsCount']
                if locations_count > 0:
                    break
        except (KeyError, TypeError, IndexError) as e:
            raise IRPAPIError(f"Failed to validate locations count for portfolio '{portfolio_name}': {e}") from e

        if locations_count == 0:
            raise IRPAPIError(f"Portfolio '{portfolio_name}' has accounts but no locations to be GeoHaz'd")

        data = {
            "resourceUri": portfolio_uri,
            "resourceType": "portfolio",
            "settings": {
                "layers": layers
            }
        }

        try:
            logger.info("Submitting GeoHaz job for portfolio '%s'", portfolio_name)
            response = self.client.request(
                'POST',
                GEOHAZ_PORTFOLIO,
                json=data
            )
            job_id = extract_id_from_location_header(response, "portfolio geohaz")
            logger.info("GeoHaz job submitted — job ID: %s", job_id)
            return int(job_id), data
        except Exception as e:
            raise IRPAPIError(
                f"Failed to execute geohaz for portfolio '{portfolio_uri}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e
        
    
    def get_geohaz_job(self, job_id: int) -> Dict[str, Any]:
        """
        Retrieve geohaz job status by job ID.

        Args:
            job_id: Job ID

        Returns:
            Dict containing job status details

        Raises:
            IRPValidationError: If job_id is invalid
            IRPAPIError: If request fails
        """
        validate_positive_int(job_id, "job_id")

        try:
            response = self.client.request('GET', GET_GEOHAZ_JOB.format(jobId=job_id))
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get geohaz job status for job ID {job_id}: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def poll_geohaz_job_to_completion(
        self,
        job_id: int,
        interval: int = 10,
        timeout: int = 600000
    ) -> Dict[str, Any]:
        """
        Poll geohaz job until completion or timeout.

        Args:
            job_id: Job ID
            interval: Polling interval in seconds (default: 10)
            timeout: Maximum timeout in seconds (default: 600000)

        Returns:
            Final job status details

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If job times out
            IRPAPIError: If polling fails
        """
        validate_positive_int(job_id, "job_id")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling GeoHaz job ID %s", job_id)
            job_data = self.get_geohaz_job(job_id)
            try:
                status = job_data['status']
                progress = job_data['progress']
            except (KeyError, TypeError) as e:
                raise IRPAPIError(
                    f"Missing 'status' or 'progress' in job response for job ID {job_id}: {e}"
                ) from e
            logger.info("GeoHaz job %s status: %s; progress: %s", job_id, status, progress)
            if status in WORKFLOW_COMPLETED_STATUSES:
                return job_data

            if time.time() - start > timeout:
                logger.error("GeoHaz job %s timed out after %s seconds. Last status: %s", job_id, timeout, status)
                raise IRPJobError(
                    f"GeoHaz job ID {job_id} did not complete within {timeout} seconds. Last status: {status}"
                )
            time.sleep(interval)


    def poll_geohaz_job_batch_to_completion(
            self,
            job_ids: List[int],
            interval: int = 20,
            timeout: int = 600000
    ) -> List[Dict[str, Any]]:
        """
        Poll multiple geohaz jobs until all complete or timeout.

        Args:
            job_ids: List of job IDs
            interval: Polling interval in seconds (default: 20)
            timeout: Maximum timeout in seconds (default: 600000)

        Returns:
            List of final job status details for all jobs

        Raises:
            IRPValidationError: If parameters are invalid
            IRPJobError: If jobs time out
            IRPAPIError: If polling fails
        """
        validate_list_not_empty(job_ids, "job_ids")
        validate_positive_int(interval, "interval")
        validate_positive_int(timeout, "timeout")

        start = time.time()
        while True:
            logger.info("Polling batch GeoHaz job IDs: %s", ",".join(str(item) for item in job_ids))

            all_completed = False
            all_jobs = []
            for job_id in job_ids:
                workflow_response = self.get_geohaz_job(job_id)
                all_jobs.append(workflow_response)
                try:
                    status = workflow_response['status']
                except (KeyError, TypeError) as e:
                    raise IRPAPIError(
                        f"Missing 'status' in workflow response for job ID {job_id}: {e}"
                    ) from e
                if status in WORKFLOW_IN_PROGRESS_STATUSES:
                    all_jobs = []
                    break
                all_completed = True

            if all_completed:
                return all_jobs
            
            if time.time() - start > timeout:
                logger.error("Batch GeoHaz jobs timed out after %s seconds", timeout)
                raise IRPJobError(
                    f"Batch geohaz jobs did not complete within {timeout} seconds"
                )
            time.sleep(interval)
