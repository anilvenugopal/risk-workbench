"""
Tests for the ``bool`` rejection in ``validators``.

``bool`` subclasses ``int``, so a bare isinstance check lets ``True`` through as
an account ID, an exposure ID or a treaty limit — where it is then serialized as
JSON ``true``, not as a number. The rest of these validators are plain type and
range checks and are left untested.
"""

import pytest

from irp_integration.exceptions import IRPValidationError
from irp_integration.validators import (
    validate_list_of_positive_ints,
    validate_non_negative_float,
    validate_non_negative_int,
    validate_positive_float,
    validate_positive_int,
)


@pytest.mark.parametrize("validator", [
    validate_positive_int,
    validate_non_negative_int,
    validate_positive_float,
    validate_non_negative_float,
])
@pytest.mark.parametrize("value", [True, False])
def test_scalar_validators_reject_bool(validator, value):
    with pytest.raises(IRPValidationError, match="got bool"):
        validator(value, "exposure_id")


def test_list_validator_rejects_bool_and_names_the_index():
    with pytest.raises(IRPValidationError, match=r"marked_accounts\[2\] must be an integer, got bool"):
        validate_list_of_positive_ints([1, 2, True, 4], "marked_accounts")
