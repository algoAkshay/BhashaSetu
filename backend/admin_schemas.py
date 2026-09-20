"""Admin inputs deliberately exclude import identity and raw provenance."""
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from backend.schemas import RuleDefinition, SchemeRead

Text = Annotated[str, Field(max_length=20000)]
ShortText = Annotated[str, Field(max_length=100)]
Confidence = Literal["VERIFIED", "LIKELY_ACTIVE", "NEEDS_REVIEW"]


class SchemePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    description: Text | None = None
    category: ShortText | None = None
    state: ShortText | None = None
    government_level: Literal["CENTRAL", "STATE"] | None = None
    ministry_or_department: Text | None = None
    raw_category: Text | None = None
    status_confidence: Confidence | None = None
    official_url: Text | None = None
    secondary_source_url: Text | None = None
    last_checked_date: date | None = None
    benefit_type: Text | None = None
    benefit_summary: Text | None = None
    benefit_amount: Text | None = None
    application_method: Text | None = None
    official_application_url: Text | None = None
    confidence_notes: Text | None = None
    other_eligibility_conditions: Text | None = None
    is_active: StrictBool | None = None

    @field_validator("official_url", "secondary_source_url", "official_application_url")
    @classmethod
    def safe_url(cls, value):
        if value is not None:
            try:
                parsed = urlsplit(value)
                valid = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
                parsed.port
            except ValueError:
                valid = False
            if not valid or any(char.isspace() for char in value):
                raise ValueError("Use an absolute HTTP or HTTPS URL without credentials.")
        return value

    @model_validator(mode="after")
    def required_values(self):
        for name in ("name", "is_active"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class AdminRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: RuleDefinition
    confidence: Annotated[str, Field(max_length=50)] | None = None
    notes: Text | None = None


class SchemePage(BaseModel):
    items: list[SchemeRead]
    total: int
    page: int
    page_size: int
