"""The only coercion boundary for rule values and user attributes."""
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any

# Extend this registry when supporting a new attribute; no scheme-specific logic.
FIELD_TYPES = {
    "age": "integer", "annual_income": "decimal", "gender": "string",
    "state": "string", "is_disabled": "boolean",
    "state_or_ut": "string", "family_annual_income": "decimal",
    "individual_monthly_income": "decimal", "social_category": "string",
    "occupation": "string", "employment_status": "string", "student_status": "boolean",
    "education_level": "string", "farmer_status": "boolean", "disability_status": "boolean",
    "disability_percentage": "decimal", "bpl_status": "boolean", "rural_urban": "string",
    "widow_status": "boolean", "minority_status": "boolean", "marital_status": "string",
}
NONNEGATIVE_FIELDS = {"age", "annual_income", "family_annual_income", "individual_monthly_income",
                      "disability_percentage"}


class InputValidationError(ValueError):
    pass


class RuleValidationError(ValueError):
    pass


def normalize_value(value: Any, value_type: str) -> Any:
    if value_type == "integer":
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
            return int(value.strip())
    elif value_type == "decimal":
        if type(value) in (int, float, str, Decimal):
            if isinstance(value, str) and not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value.strip()):
                raise ValueError("Expected a plain decimal number.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Expected a finite number.")
            try:
                number = Decimal(str(value).strip())
            except InvalidOperation:
                raise ValueError("Expected a decimal number.") from None
            if number.is_finite():
                return number
    elif value_type == "string":
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    elif value_type == "boolean":
        if type(value) is bool:
            return value
    raise ValueError(f"Expected a valid {value_type} value.")


def normalize_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for field, value in attributes.items():
        if field not in FIELD_TYPES:
            raise InputValidationError(f"Unsupported attribute: {field}")
        if value is None or (isinstance(value, str) and not value.strip()):
            result[field] = None
            continue
        try:
            result[field] = normalize_value(value, FIELD_TYPES[field])
        except ValueError:
            raise InputValidationError(f"Invalid value for {field}; expected {FIELD_TYPES[field]}.") from None
        if field in NONNEGATIVE_FIELDS and result[field] < 0:
            raise InputValidationError(f"{field} must not be negative.")
        if field == "disability_percentage" and result[field] > 100:
            raise InputValidationError("disability_percentage must not exceed 100.")
    return result
