from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.normalization import FIELD_TYPES, NONNEGATIVE_FIELDS, normalize_attributes, normalize_value

Operator = Literal["==", "!=", ">", ">=", "<", "<=", "IN", "NOT_IN"]
ValueType = Literal["integer", "decimal", "string", "boolean"]
Status = Literal["ELIGIBLE", "POTENTIALLY_ELIGIBLE", "NOT_ELIGIBLE", "NEED_MORE_INFORMATION"]


class RuleDefinition(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    field: str
    operator: Operator
    value: Any
    value_type: ValueType

    @model_validator(mode="after")
    def validate_rule(self):
        required_type = FIELD_TYPES.get(self.field)
        if required_type is None:
            raise ValueError("Unsupported rule field.")
        if self.value_type != required_type and not (
            required_type == "decimal" and self.value_type == "integer"
        ):
            raise ValueError("Rule type does not match its field.")
        if self.operator in {">", ">=", "<", "<="} and self.value_type not in {"integer", "decimal"}:
            raise ValueError("Ordered comparisons require numeric rules.")
        membership = self.operator in {"IN", "NOT_IN"}
        if membership and (not isinstance(self.value, list) or not self.value):
            raise ValueError("Membership rules require a nonempty list.")
        values = self.value if membership else [self.value]
        normalized = [normalize_value(value, self.value_type) for value in values]
        if self.field in NONNEGATIVE_FIELDS and any(value < 0 for value in normalized):
            raise ValueError("Numeric eligibility thresholds must not be negative.")
        if self.field == "disability_percentage" and any(value > 100 for value in normalized):
            raise ValueError("Disability percentage cannot exceed 100.")
        # Decimal strings retain precision in JSON on both PostgreSQL and test SQLite.
        stored = [format(value, "f") if isinstance(value, Decimal) else value for value in normalized]
        self.value = stored if membership else stored[0]
        return self


class EligibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value):
        return normalize_attributes(value)


class RuleCheck(BaseModel):
    field: str
    actual: Any
    operator: Operator
    expected: Any
    passed: bool | None


class EligibilityResult(BaseModel):
    status: Status
    scheme_id: int
    scheme_name: str
    checks: list[RuleCheck]
    missing_fields: list[str]
    reason: Literal["inactive_scheme", "no_rules", "manual_review_required"] | None = None
    manual_conditions: list[str] = Field(default_factory=list)


class RuleRead(RuleDefinition):
    id: int
    scheme_id: int
    source_metadata: dict | None = None


class SchemeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None
    category: str | None
    state: str | None
    official_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    rules: list[RuleRead]
    government_level: str | None = None
    ministry_or_department: str | None = None
    raw_category: str | None = None
    status_confidence: str | None = None
    secondary_source_url: str | None = None
    last_checked_date: date | None = None
    benefit_type: str | None = None
    benefit_summary: str | None = None
    benefit_amount: str | None = None
    application_method: str | None = None
    official_application_url: str | None = None
    confidence_notes: str | None = None
    other_eligibility_conditions: str | None = None
    manual_conditions: list[str] | None = None
    source_metadata: dict | None = None
