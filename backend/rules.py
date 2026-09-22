"""Pure AND-rule evaluation. No database, HTTP, or scheme-name branches."""

import operator
from typing import Any, Iterable

from pydantic import ValidationError

from backend.normalization import (
    RuleValidationError,
    SOFT_CATEGORICAL_FIELDS,
    normalize_attributes,
    normalize_field_value,
)
from backend.schemas import EligibilityResult, RuleCheck, RuleDefinition


OPERATORS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "IN": lambda actual, expected: actual in expected,
    "NOT_IN": lambda actual, expected: actual not in expected,
}


def validate_rule(rule: Any) -> RuleDefinition:
    try:
        # Revalidate even existing schema instances; never trust persisted rule data.
        value = (
            rule.model_dump()
            if isinstance(rule, RuleDefinition)
            else rule
        )
        return RuleDefinition.model_validate(value)

    except (ValidationError, ValueError):
        raise RuleValidationError(
            "Malformed eligibility rule; check field, operator, type and value."
        ) from None


def _evaluate_rule(
    rule: Any,
    attributes: dict[str, Any],
) -> RuleCheck:
    definition = validate_rule(rule)
    actual = attributes.get(definition.field)

    if definition.operator in {"IN", "NOT_IN"}:
        expected = [
            normalize_field_value(
                definition.field,
                value,
                definition.value_type,
            )
            for value in definition.value
        ]
    else:
        expected = normalize_field_value(
            definition.field,
            definition.value,
            definition.value_type,
        )

    if actual is None:
        passed = None

    else:
        passed = bool(
            OPERATORS[definition.operator](
                actual,
                expected,
            )
        )

        # Some extracted categorical values have broad/narrow
        # relationships and should not cause a conclusive rejection
        # merely because their text differs.
        #
        # Examples:
        #   Farmer vs Small and marginal farmer
        #   Graduate vs Undergraduate
        if (
            definition.field in SOFT_CATEGORICAL_FIELDS
            and definition.value_type == "string"
            and definition.operator in {"==", "IN"}
            and passed is False
        ):
            passed = None

    return RuleCheck(
        field=definition.field,
        actual=actual,
        operator=definition.operator,
        expected=expected,
        passed=passed,
    )


def evaluate_rule(
    rule: Any,
    user_data: dict[str, Any],
) -> RuleCheck:
    return _evaluate_rule(
        rule,
        normalize_attributes(user_data),
    )


def evaluate_scheme(
    scheme: Any,
    rules: Iterable[Any],
    user_data: dict[str, Any],
) -> EligibilityResult:
    attributes = normalize_attributes(user_data)

    if not scheme.is_active:
        return EligibilityResult(
            status="NOT_ELIGIBLE",
            scheme_id=scheme.id,
            scheme_name=scheme.name,
            checks=[],
            missing_fields=[],
            reason="inactive_scheme",
        )

    checks = [
        _evaluate_rule(rule, attributes)
        for rule in rules
    ]

    manual = list(
        getattr(
            scheme,
            "manual_conditions",
            None,
        )
        or []
    )

    other = getattr(
        scheme,
        "other_eligibility_conditions",
        None,
    )

    if other and other not in manual:
        manual.append(other)

    missing = list(
        dict.fromkeys(
            check.field
            for check in checks
            if check.passed is None
        )
    )

    # A known hard failure conclusively excludes a scheme.
    if any(
        check.passed is False
        for check in checks
    ):
        status = "NOT_ELIGIBLE"

    # Some machine-checkable information is still missing.
    elif missing or not checks:
        status = "NEED_MORE_INFORMATION"

    # All structured checks passed, but some conditions
    # still require manual/external verification.
    elif manual:
        status = "POTENTIALLY_ELIGIBLE"

    else:
        status = "ELIGIBLE"

    if status == "POTENTIALLY_ELIGIBLE":
        reason = "manual_review_required"

    elif not checks:
        reason = "no_rules"

    else:
        reason = None

    return EligibilityResult(
        status=status,
        scheme_id=scheme.id,
        scheme_name=scheme.name,
        checks=checks,
        missing_fields=missing,
        manual_conditions=manual,
        reason=reason,
    )