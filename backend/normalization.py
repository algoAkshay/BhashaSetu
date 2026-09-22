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

NONNEGATIVE_FIELDS = {
    "age",
    "annual_income",
    "family_annual_income",
    "individual_monthly_income",
    "disability_percentage",
}

# Only true aliases belong here.
# Do NOT map broad categories to narrower ones.
STATE_ALIASES = {
    "up": "uttar pradesh",
    "u.p.": "uttar pradesh",
    "uttar pradesh": "uttar pradesh",

    "mp": "madhya pradesh",
    "m.p.": "madhya pradesh",
    "madhya pradesh": "madhya pradesh",

    "wb": "west bengal",
    "w.b.": "west bengal",
    "west bengal": "west bengal",

    "uk": "uttarakhand",
    "uttarakhand": "uttarakhand",

    "tn": "tamil nadu",
    "tamil nadu": "tamil nadu",

    "ap": "andhra pradesh",
    "andhra pradesh": "andhra pradesh",

    "hp": "himachal pradesh",
    "himachal pradesh": "himachal pradesh",

    "jk": "jammu and kashmir",
    "j&k": "jammu and kashmir",
    "jammu & kashmir": "jammu and kashmir",
    "jammu and kashmir": "jammu and kashmir",

    "nct delhi": "delhi",
    "delhi nct": "delhi",
    "new delhi": "delhi",
    "delhi": "delhi",

    "odissa": "odisha",
    "orissa": "odisha",
    "odisha": "odisha",
}

CATEGORY_ALIASES = {
    "scheduled caste": "sc",
    "sc": "sc",

    "scheduled tribe": "st",
    "st": "st",

    "other backward class": "obc",
    "other backward classes": "obc",
    "obc": "obc",

    "economically weaker section": "ews",
    "ews": "ews",

    "economically backward class": "ebc",
    "ebc": "ebc",

    "backward class": "bc",
    "bc": "bc",

    "denotified tribe": "dnt",
    "denotified tribes": "dnt",
    "dnt": "dnt",

    "general": "general",
    "unreserved": "general",
    "ur": "general",
}

FIELD_ALIASES = {
    "state": STATE_ALIASES,
    "state_or_ut": STATE_ALIASES,

    "social_category": CATEGORY_ALIASES,

    "gender": {
        "male": "male",
        "m": "male",
        "female": "female",
        "f": "female",
    },

    "rural_urban": {
        "rural": "rural",
        "village": "rural",
        "urban": "urban",
        "city": "urban",
    },

    "marital_status": {
        "widow": "widowed",
        "widowed": "widowed",
        "married": "married",
        "unmarried": "unmarried",
        "single": "unmarried",
        "divorced": "divorced",
    },
}

# These taxonomies have broad/narrow relationships.
#
# Example:
#   Farmer
#   Small and marginal farmer
#
# or:
#   Graduate
#   Undergraduate
#
# A text mismatch by itself is not enough evidence to reject someone.
SOFT_CATEGORICAL_FIELDS = {
    "occupation",
    "employment_status",
    "education_level",
}


class InputValidationError(ValueError):
    pass


class RuleValidationError(ValueError):
    pass


def normalize_value(value: Any, value_type: str) -> Any:
    if value_type == "integer":
        if type(value) is int:
            return value

        if isinstance(value, str) and re.fullmatch(
            r"[+-]?[0-9]+",
            value.strip(),
        ):
            return int(value.strip())

    elif value_type == "decimal":
        if type(value) in (int, float, str, Decimal):
            if (
                isinstance(value, str)
                and not re.fullmatch(
                    r"[+-]?[0-9]+(?:\.[0-9]+)?",
                    value.strip(),
                )
            ):
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


def normalize_field_value(
    field: str,
    value: Any,
    value_type: str,
) -> Any:
    """
    Normalise a primitive value first and then apply safe aliases
    for the specific eligibility field.
    """
    normalized = normalize_value(value, value_type)

    if value_type == "string":
        normalized = FIELD_ALIASES.get(
            field,
            {},
        ).get(normalized, normalized)

    return normalized


def normalize_attributes(
    attributes: dict[str, Any],
) -> dict[str, Any]:
    result = {}

    for field, value in attributes.items():
        if field not in FIELD_TYPES:
            raise InputValidationError(
                f"Unsupported attribute: {field}"
            )

        if value is None or (
            isinstance(value, str)
            and not value.strip()
        ):
            result[field] = None
            continue

        try:
            result[field] = normalize_field_value(
                field,
                value,
                FIELD_TYPES[field],
            )
        except ValueError:
            raise InputValidationError(
                f"Invalid value for {field}; "
                f"expected {FIELD_TYPES[field]}."
            ) from None

        if (
            field in NONNEGATIVE_FIELDS
            and result[field] < 0
        ):
            raise InputValidationError(
                f"{field} must not be negative."
            )

        if (
            field == "disability_percentage"
            and result[field] > 100
        ):
            raise InputValidationError(
                "disability_percentage must not exceed 100."
            )

    return result