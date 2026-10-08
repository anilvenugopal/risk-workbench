"""
Reference data management operations.

Handles retrieval and creation of reference data including
model profiles, output profiles, event rate schemes, currencies, and tags.
"""

import logging
from typing import Dict, List, Any, Optional, TYPE_CHECKING
from .constants import (
    SEARCH_CURRENCIES, SEARCH_CURRENCY_SCHEMES, SEARCH_CURRENCY_SCHEME_VINTAGES, GET_TAGS, CREATE_TAG,
    GET_MODEL_PROFILES, GET_OUTPUT_PROFILES, GET_EVENT_RATE_SCHEME,
    SEARCH_SIMULATION_SETS, SEARCH_PET_METADATA, SEARCH_SOFTWARE_MODEL_VERSION_MAP
)
from .exceptions import IRPAPIError
from .validators import (
    validate_non_empty_string, validate_list_not_empty, validate_positive_int,
    validate_non_negative_int, validate_sort_order
)
from .utils import extract_id_from_location_header, paginate_search

if TYPE_CHECKING:
    from . import IRPClient

logger = logging.getLogger(__name__)


def _build_analysis_currency_dict(vintage: Dict[str, Any]) -> Dict[str, str]:
    """
    Build currency dict for analysis requests from a currency scheme vintage.

    Args:
        vintage: Currency scheme vintage dict from API with keys:
            - effectiveDate: ISO date string (e.g., "2025-05-28T00:00:00.000Z")
            - currencySchemeCode: Scheme code (e.g., "RMS")
            - vintage: Vintage code (e.g., "RL25")

    Returns:
        Currency dict with asOfDate (date only), code, scheme, and vintage
    """
    # Extract date portion only (API returns full timestamp but expects date only)
    effective_date = vintage["effectiveDate"].split("T")[0]
    return {
        "asOfDate": effective_date,
        "code": "USD",
        "scheme": vintage["currencySchemeCode"],
        "vintage": vintage["vintage"]
    }


def _build_default_analysis_currency_dict() -> Dict[str, str]:
    """
    Build default currency dict for analysis requests.

    Note: This is a fallback helper used when the currency scheme
    vintage cannot be retrieved from the API.

    Returns:
        Currency dict with default values
    """
    return {
        "asOfDate": "2025-05-28",
        "code": "USD",
        "scheme": "RMS",
        "vintage": "RL25"
    }


class ReferenceDataManager:
    """Manager for reference data operations."""

    def __init__(self, irp: "IRPClient") -> None:
        """
        Initialize reference data manager.

        Args:
            irp: Owning IRP client instance
        """
        self._irp = irp
        self.client = irp.client


    def get_model_profiles(self) -> Dict[str, Any]:
        """
        Retrieve all model profiles.

        Returns:
            Dict containing model profile list

        Raises:
            IRPAPIError: If request fails
        """
        try:
            response = self.client.request('GET', GET_MODEL_PROFILES)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get model profiles: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def get_model_profile_by_name(self, profile_name: str) -> Dict[str, Any]:
        """
        Retrieve model profile by name.

        Args:
            profile_name: Model profile name

        Returns:
            Dict containing model profile details

        Raises:
            IRPValidationError: If profile_name is invalid
            IRPAPIError: If request fails
        """
        validate_non_empty_string(profile_name, "profile_name")

        params = {'name': profile_name}

        try:
            response = self.client.request('GET', GET_MODEL_PROFILES, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get model profile '{profile_name}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def get_output_profiles(self) -> List[Dict[str, Any]]:
        """
        Retrieve all output profiles.

        Returns:
            List of output profile dicts

        Raises:
            IRPAPIError: If request fails
        """
        try:
            response = self.client.request('GET', GET_OUTPUT_PROFILES)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get output profiles: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def get_output_profile_by_name(self, profile_name: str) -> List[Dict[str, Any]]:
        """
        Retrieve output profile by name.

        Args:
            profile_name: Output profile name

        Returns:
            List of matching output profile dicts

        Raises:
            IRPValidationError: If profile_name is invalid
            IRPAPIError: If request fails
        """
        validate_non_empty_string(profile_name, "profile_name")

        params = {'name': profile_name}

        try:
            response = self.client.request('GET', GET_OUTPUT_PROFILES, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get output profile '{profile_name}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def get_event_rate_schemes(self) -> Dict[str, Any]:
        """
        Retrieve all active event rate schemes.

        Returns:
            Dict containing event rate scheme list

        Raises:
            IRPAPIError: If request fails
        """
        params = {'where': 'isActive=True'}

        try:
            response = self.client.request('GET', GET_EVENT_RATE_SCHEME, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get event rate schemes: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def get_event_rate_scheme_by_name(
        self,
        scheme_name: str,
        peril_code: Optional[str] = None,
        model_region_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieve event rate scheme by name with optional peril and region filtering.

        When the same event rate scheme name exists for multiple peril/region combinations,
        use the peril_code and model_region_code parameters to filter to the correct one.
        These values can be obtained from the corresponding model profile.

        Args:
            scheme_name: Event rate scheme name
            peril_code: Optional peril code (e.g., "CS", "WS") to filter results
            model_region_code: Optional model region code (e.g., "NACS", "NAWS") to filter results

        Returns:
            Dict containing event rate scheme details

        Raises:
            IRPValidationError: If scheme_name is invalid
            IRPAPIError: If request fails
        """
        validate_non_empty_string(scheme_name, "scheme_name")

        # Build where clause with optional peril and region filters
        where_parts = [f'eventRateSchemeName="{scheme_name}"']
        if peril_code:
            where_parts.append(f'perilCode="{peril_code}"')
        if model_region_code:
            where_parts.append(f'modelRegionCode="{model_region_code}"')

        params = {'where': ' AND '.join(where_parts)}

        try:
            response = self.client.request('GET', GET_EVENT_RATE_SCHEME, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get event rate scheme '{scheme_name}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def search_currencies(
        self,
        where_clause: str = "",
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        sort: str = "",
        sort_order: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Search currencies with optional filtering, sorting, and pagination.

        Args:
            where_clause: Optional filter clause
            limit: Maximum number of records to return
            offset: Number of records to skip
            sort: Optional field to sort by
            sort_order: Sort direction — 1 for ascending, -1 for descending

        Returns:
            Dict containing currencies (with an 'items' list)

        Raises:
            IRPValidationError: If limit, offset, or sort_order is invalid
            IRPAPIError: If request fails
        """
        if limit is not None:
            validate_positive_int(limit, "limit")
        if offset is not None:
            validate_non_negative_int(offset, "offset")
        if sort_order is not None:
            validate_sort_order(sort_order, "sort_order")

        params: Dict[str, Any] = {}
        if where_clause:
            params['where'] = where_clause
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        if sort:
            params['sort'] = sort
        if sort_order is not None:
            params['sortOrder'] = sort_order

        try:
            response = self.client.request('GET', SEARCH_CURRENCIES, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to search currencies: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def search_currency_schemes(
        self,
        where_clause: str = "",
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        sort: str = "",
        sort_order: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Search currency schemes with optional filtering, sorting, and pagination.

        Args:
            where_clause: Optional filter clause
            limit: Maximum number of records to return
            offset: Number of records to skip
            sort: Optional field to sort by
            sort_order: Sort direction — 1 for ascending, -1 for descending

        Returns:
            Dict containing currency schemes (with an 'items' list)

        Raises:
            IRPValidationError: If limit, offset, or sort_order is invalid
            IRPAPIError: If request fails
        """
        if limit is not None:
            validate_positive_int(limit, "limit")
        if offset is not None:
            validate_non_negative_int(offset, "offset")
        if sort_order is not None:
            validate_sort_order(sort_order, "sort_order")

        params: Dict[str, Any] = {}
        if where_clause:
            params['where'] = where_clause
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        if sort:
            params['sort'] = sort
        if sort_order is not None:
            params['sortOrder'] = sort_order

        try:
            response = self.client.request('GET', SEARCH_CURRENCY_SCHEMES, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to search currency schemes: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def search_currency_scheme_vintages(
        self,
        where_clause: str = "",
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        sort: str = "",
        sort_order: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Search currency scheme vintages with optional filtering, sorting, and pagination.

        Args:
            where_clause: Optional filter clause
            limit: Maximum number of records to return
            offset: Number of records to skip
            sort: Optional field to sort by
            sort_order: Sort direction — 1 for ascending, -1 for descending

        Returns:
            Dict containing currency scheme vintages

        Raises:
            IRPValidationError: If limit, offset, or sort_order is invalid
            IRPAPIError: If request fails
        """
        if limit is not None:
            validate_positive_int(limit, "limit")
        if offset is not None:
            validate_non_negative_int(offset, "offset")
        if sort_order is not None:
            validate_sort_order(sort_order, "sort_order")

        params: Dict[str, Any] = {}
        if where_clause:
            params['where'] = where_clause
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        if sort:
            params['sort'] = sort
        if sort_order is not None:
            params['sortOrder'] = sort_order

        try:
            response = self.client.request('GET', SEARCH_CURRENCY_SCHEME_VINTAGES, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to search currency scheme vintages: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def get_latest_currency_scheme_vintage(self) -> Dict[str, Any]:
        """
        Get the latest RMS currency scheme vintage by effective date.

        Returns:
            Dict containing the currency scheme vintage with the most recent effectiveDate

        Raises:
            IRPAPIError: If request fails or no vintages found
        """
        where_clause = "currencySchemeCode=\"RMS\""
        response = self.search_currency_scheme_vintages(where_clause)

        try:
            items = response['items']
            if not items:
                raise IRPAPIError("No RMS currency scheme vintages found")
            latest = max(items, key=lambda x: x['effectiveDate'])
            return latest
        except KeyError as e:
            raise IRPAPIError(f"Failed to extract items from currency scheme vintages response: {e}") from e


    def get_analysis_currency(self) -> Dict[str, str]:
        """
        Get currency dict for analysis requests.

        Attempts to get the latest RMS currency scheme vintage from the API.
        Falls back to default values if the API call fails.

        Returns:
            Currency dict with asOfDate, code, scheme, and vintage
        """
        try:
            latest_vintage = self.get_latest_currency_scheme_vintage()
            return _build_analysis_currency_dict(latest_vintage)
        except IRPAPIError:
            logger.warning("Failed to get currency scheme vintage from API, using defaults")
            return _build_default_analysis_currency_dict()


    def get_currency_by_name(self, currency_name: str) -> Dict[str, Any]:
        """
        Retrieve currency by name.

        Args:
            currency_name: Currency name

        Returns:
            Dict containing currency details

        Raises:
            IRPValidationError: If currency_name is invalid
            IRPAPIError: If request fails
        """
        validate_non_empty_string(currency_name, "currency_name")
        where_clause = f"currencyName=\"{currency_name}\""
        currencies_response = self.search_currencies(where_clause)
        try:
            currency = currencies_response['items'][0]
            return currency
        except (KeyError, IndexError, TypeError) as e:
            raise IRPAPIError(
                f"Failed to extract currency '{currency_name}' from search response: {e}"
            ) from e


    def get_tag_by_name(self, tag_name: str) -> List[Dict[str, Any]]:
        """
        Retrieve tag by name.

        Args:
            tag_name: Tag name

        Returns:
            List of dicts containing tag details

        Raises:
            IRPValidationError: If tag_name is invalid
            IRPAPIError: If request fails
        """
        validate_non_empty_string(tag_name, "tag_name")

        params = {
            "isActive": True,
            "filter": f"TAGNAME = '{tag_name}'"
        }

        try:
            response = self.client.request('GET', GET_TAGS, params=params)
            return response.json()
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get tag '{tag_name}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def create_tag(self, tag_name: str) -> Dict[str, str]:
        """
        Create new tag.

        Args:
            tag_name: Tag name

        Returns:
            Dict with tag ID

        Raises:
            IRPValidationError: If tag_name is invalid
            IRPAPIError: If request fails
        """
        validate_non_empty_string(tag_name, "tag_name")

        data = {"tagName": tag_name}

        try:
            logger.info("Creating tag '%s'", tag_name)
            response = self.client.request('POST', CREATE_TAG, json=data)
            tag_id = extract_id_from_location_header(response, "tag creation")
            logger.info("Tag created — ID: %s", tag_id)
            return {"id": tag_id}
        except Exception as e:
            raise IRPAPIError(
                f"Failed to create tag '{tag_name}': {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e


    def get_tag_ids_from_tag_names(self, tag_names: List[str]) -> List[int]:
        """
        Get or create tags by names and return their IDs.

        This method will create tags if they don't already exist.

        Args:
            tag_names: List of tag names

        Returns:
            List of tag IDs

        Raises:
            IRPValidationError: If tag_names is empty
            IRPAPIError: If request fails
        """
        validate_list_not_empty(tag_names, "tag_names")

        logger.debug("Resolving tag IDs for: %s", tag_names)
        tag_ids = []
        for tag_name in tag_names:
            tag_search_response = self.get_tag_by_name(tag_name)
            if len(tag_search_response) > 0:
                try:
                    tag_id = tag_search_response[0]['tagId']
                except (KeyError, IndexError, TypeError) as e:
                    raise IRPAPIError(
                        f"Failed to extract tag ID from search response for '{tag_name}': {e}"
                    ) from e
                tag_ids.append(int(tag_id))
            else:
                created_tag = self.create_tag(tag_name)
                try:
                    tag_id = created_tag['id']
                except (KeyError, TypeError) as e:
                    raise IRPAPIError(
                        f"Failed to extract tag ID from created tag response for '{tag_name}': {e}"
                    ) from e
                tag_ids.append(int(tag_id))

        return tag_ids

    def get_all_simulation_sets(self) -> List[Dict[str, Any]]:
        """
        Get all active simulation sets.

        Simulation sets map event-rate scheme IDs to simulation-set IDs for
        ELT analyses. Grouping inspection matches the partition fields and
        requires each simulation set's ``eventRateSchemeId`` to resolve in the
        active EventRateScheme response. Risk Modeler uses the relationship to
        omit climate-conditioned choices. The relationship does not restrict
        the caller's event-rate selection.

        Returns:
            List of simulation set dicts

        Raises:
            IRPAPIError: If request fails
        """
        def fetch(limit: int, offset: int) -> List[Dict[str, Any]]:
            params = {
                'isActive': True,
                'isActivePEQ': True,
                'sort': 'id',
                'sortOrder': 1,
                'where': 'isActive=true',
                'limit': limit,
                'offset': offset,
            }
            response = self.client.request('GET', SEARCH_SIMULATION_SETS, params=params)
            return response.json().get('items', [])

        try:
            return paginate_search(fetch, "simulation-set search", limit=500)
        except IRPAPIError:
            raise
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get simulation sets: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def get_simulation_set_by_event_rate_scheme_id(self, event_rate_scheme_id: int) -> Dict[str, Any]:
        """
        Get simulation set by event rate scheme ID.

        Raises when zero or more than one active simulation set carries
        ``event_rate_scheme_id``. Grouping inspection does not use this lookup;
        it reads all active simulation sets because Risk Modeler allows a
        simulation set to be selected independently of the event-rate scheme.

        Args:
            event_rate_scheme_id: Event rate scheme ID from analysis regions

        Returns:
            Dict containing simulation set details with 'id' being the simulationSetId

        Raises:
            IRPAPIError: If request fails or simulation set not found
        """
        simulation_sets = self.get_all_simulation_sets()

        matches = [
            sim_set for sim_set in simulation_sets
            if sim_set.get('eventRateSchemeId') == event_rate_scheme_id
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise IRPAPIError(
                f"No simulation set found for event rate scheme ID {event_rate_scheme_id}"
            )
        raise IRPAPIError(
            f"Multiple simulation sets found for event rate scheme ID {event_rate_scheme_id}"
        )

    def get_simulation_set_exact(
        self,
        *,
        event_rate_scheme_id: int,
        model_region_code: str,
        model_version: str,
    ) -> Dict[str, Any]:
        """Return one exact simulation-set mapping.

        Args:
            event_rate_scheme_id: Positive event-rate scheme ID
            model_region_code: Exact broad model region code, such as ``NAWS``
            model_version: Exact model version code

        Returns:
            The sole matching simulation-set row

        Raises:
            IRPValidationError: If an argument is malformed
            IRPAPIError: If zero or multiple rows match
        """
        validate_positive_int(event_rate_scheme_id, "event_rate_scheme_id")
        validate_non_empty_string(model_region_code, "model_region_code")
        validate_non_empty_string(model_version, "model_version")
        matches = []
        for sim_set in self.get_all_simulation_sets():
            row_version = sim_set.get('modelVersionCode', sim_set.get('modelVersion'))
            if (
                sim_set.get('eventRateSchemeId') == event_rate_scheme_id
                and sim_set.get('modelRegionCode') == model_region_code
                and str(row_version) == model_version
            ):
                matches.append(sim_set)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise IRPAPIError(
                "No simulation set found for event rate scheme ID "
                f"{event_rate_scheme_id}, model region '{model_region_code}', "
                f"and model version '{model_version}'"
            )
        raise IRPAPIError(
            "Multiple simulation sets found for event rate scheme ID "
            f"{event_rate_scheme_id}, model region '{model_region_code}', "
            f"and model version '{model_version}'"
        )

    def get_simulation_set_by_region_peril_and_engine(
        self, region_code: str, peril_code: str, engine_version: str
    ) -> Dict[str, Any]:
        """
        Get simulation set by regionCode, perilCode, and engineVersion.

        The lookup uses regionCode + perilCode to build the broader modelRegionCode
        (e.g., "NA" + "WS" = "NAWS") since SimulationSet entries use broader regional
        codes, not sub-region-specific codes like "HTWS".

        The method rejects multiple matches. Grouping inspection does not use
        this helper because Risk Modeler presents every simulation set for the
        peril, region, and model version as a caller choice.

        Args:
            region_code: Region code (e.g., "NA", "US", "CB")
            peril_code: Peril code (e.g., "WS", "EQ", "FL")
            engine_version: Engine version (e.g., "RL23", "HDv2.0")

        Returns:
            Dict containing simulation set details with 'id' being the simulationSetId

        Raises:
            IRPValidationError: If inputs are invalid
            IRPAPIError: If request fails or simulation set not found
        """
        validate_non_empty_string(region_code, "region_code")
        validate_non_empty_string(peril_code, "peril_code")
        validate_non_empty_string(engine_version, "engine_version")

        # Build the broader modelRegionCode for SimulationSet lookup
        # e.g., "NA" + "WS" = "NAWS"
        sim_set_model_region_code = region_code + peril_code

        simulation_sets = self.get_all_simulation_sets()

        # Find matching simulation sets
        matching_sets = []
        for sim_set in simulation_sets:
            if sim_set.get('modelRegionCode') == sim_set_model_region_code:
                # Check if engineVersion is in the rlVersion list
                rl_version_str = sim_set.get('rlVersion', '')
                # rlVersion is comma-separated with spaces: "RL16, RL17, RL18"
                rl_versions = [v.strip() for v in rl_version_str.split(',')]
                if engine_version in rl_versions:
                    matching_sets.append(sim_set)

        if not matching_sets:
            raise IRPAPIError(
                f"No simulation set found for regionCode '{region_code}', "
                f"perilCode '{peril_code}', engineVersion '{engine_version}'"
            )

        # If only one match, return it
        if len(matching_sets) == 1:
            return matching_sets[0]

        raise IRPAPIError(
            f"Multiple simulation sets found for regionCode '{region_code}', "
            f"perilCode '{peril_code}', engineVersion '{engine_version}'"
        )

    def get_all_pet_metadata(self) -> List[Dict[str, Any]]:
        """
        Get all PET (Probabilistic Event Table) metadata.

        PET metadata describes the available PLT/HD simulation sets. A PET ID
        can occur in more than one model version or model region.

        Returns:
            List of PET metadata dicts

        Raises:
            IRPAPIError: If request fails
        """
        def fetch(limit: int, offset: int) -> List[Dict[str, Any]]:
            response = self.client.request(
                'GET', SEARCH_PET_METADATA, params={'limit': limit, 'offset': offset}
            )
            return response.json().get('items', [])

        try:
            return paginate_search(fetch, "PET metadata search", limit=500)
        except IRPAPIError:
            raise
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get PET metadata: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def get_pet_metadata_by_id(self, pet_id: int) -> Dict[str, Any]:
        """
        Get PET metadata by PET ID.

        Raises when ``pet_id`` occurs in zero or more than one metadata row. A
        PET ID can occur once per model version, so use
        ``get_pet_metadata_exact`` when model qualifiers are available.

        Args:
            pet_id: PET ID from analysis regions

        Returns:
            Dict containing PET metadata details

        Raises:
            IRPValidationError: If pet_id is invalid
            IRPAPIError: If request fails or PET not found
        """
        validate_positive_int(pet_id, "pet_id")

        pet_metadata_list = self.get_all_pet_metadata()

        matches = [pet for pet in pet_metadata_list if pet.get('id') == pet_id]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise IRPAPIError(f"No PET metadata found for PET ID {pet_id}")
        raise IRPAPIError(f"Multiple PET metadata rows found for PET ID {pet_id}")

    def get_pet_metadata_exact(
        self,
        *,
        pet_id: int,
        model_version: str,
        model_region_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return one qualified PET metadata row.

        Args:
            pet_id: Positive PET ID from an analysis region
            model_version: Exact model version code
            model_region_code: Optional exact model region code

        Returns:
            The sole PET metadata row matching every supplied qualifier

        Raises:
            IRPValidationError: If an argument is malformed
            IRPAPIError: If zero or multiple rows match
        """
        validate_positive_int(pet_id, "pet_id")
        validate_non_empty_string(model_version, "model_version")
        if model_region_code is not None:
            validate_non_empty_string(model_region_code, "model_region_code")

        matches = []
        for pet in self.get_all_pet_metadata():
            row_version = pet.get('modelVersionCode', pet.get('modelVersion'))
            if pet.get('id') != pet_id or str(row_version) != model_version:
                continue
            if (
                model_region_code is not None
                and pet.get('modelRegionCode') != model_region_code
            ):
                continue
            matches.append(pet)

        qualifiers = f"PET ID {pet_id} and model version '{model_version}'"
        if model_region_code is not None:
            qualifiers += f" and model region '{model_region_code}'"
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise IRPAPIError(f"No PET metadata found for {qualifiers}")
        raise IRPAPIError(f"Multiple PET metadata rows found for {qualifiers}")

    def get_all_software_model_version_map(self) -> List[Dict[str, Any]]:
        """
        Get all active software model version mappings.

        This maps engine versions to model versions for grouping requests.

        Returns:
            List of version map dicts

        Raises:
            IRPAPIError: If request fails
        """
        params = {'isActive': True}

        try:
            response = self.client.request('GET', SEARCH_SOFTWARE_MODEL_VERSION_MAP, params=params)
            return response.json().get('items', [])
        except Exception as e:
            raise IRPAPIError(
                f"Failed to get software model version map: {e}",
                status_code=getattr(e, 'status_code', None),
            ) from e

    def get_model_version_by_engine_version(self, engine_version: str) -> str:
        """
        Get model version for a given engine version.

        Note: This method returns the first entry matching the softwareVersionCode.
        For more precise matching, use ``get_model_version_by_engine_region_peril``.

        Args:
            engine_version: Engine version string (e.g., "HDv2.0", "RL23")

        Returns:
            Model version string (e.g., "2.0", "23.0")

        Raises:
            IRPValidationError: If engine_version is invalid
            IRPAPIError: If request fails or mapping not found
        """
        validate_non_empty_string(engine_version, "engine_version")

        version_maps = self.get_all_software_model_version_map()

        for version_map in version_maps:
            if version_map.get('softwareVersionCode') == engine_version:
                return version_map['modelVersionCode']

        raise IRPAPIError(
            f"No model version mapping found for engine version '{engine_version}'"
        )

    def get_model_version_by_engine_region_peril(
        self, engine_version: str, region_code: str, peril_code: str
    ) -> str:
        """
        Get model version for a given engine version, region code, and peril code.

        This provides a precise lookup using the broader modelRegionCode (e.g., "NAWS")
        built from regionCode + perilCode, since SoftwareModelVersionMap uses broader
        codes, not sub-region-specific codes like "HTWS".

        Args:
            engine_version: Engine version string (e.g., "HDv2.0", "RL23")
            region_code: Region code (e.g., "NA", "US", "CB")
            peril_code: Peril code (e.g., "WS", "EQ", "FL")

        Returns:
            Model version string (e.g., "2.0", "11.0")

        Raises:
            IRPValidationError: If inputs are invalid
            IRPAPIError: If request fails or mapping not found
        """
        validate_non_empty_string(engine_version, "engine_version")
        validate_non_empty_string(region_code, "region_code")
        validate_non_empty_string(peril_code, "peril_code")

        # Build the broader modelRegionCode for lookup (e.g., "NA" + "WS" = "NAWS")
        broader_model_region_code = region_code + peril_code

        version_maps = self.get_all_software_model_version_map()

        matches = [
            version_map for version_map in version_maps
            if version_map.get('softwareVersionCode') == engine_version
            and version_map.get('modelRegionCode') == broader_model_region_code
        ]
        if len(matches) == 1:
            return matches[0]['modelVersionCode']
        if not matches:
            raise IRPAPIError(
                f"No model version mapping found for engine version '{engine_version}', "
                f"region code '{region_code}', peril code '{peril_code}'"
            )
        raise IRPAPIError(
            f"Multiple model version mappings found for engine version '{engine_version}', "
            f"region code '{region_code}', peril code '{peril_code}'"
        )
