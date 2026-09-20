"""Strict extraction boundary; session/API gender spelling remains Male/Female."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Age = Annotated[int, Field(strict=True, ge=0, le=120)]
Income = Annotated[int, Field(strict=True, ge=0)]
Percentage = Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)]
Category = Annotated[str, Field(strict=True, min_length=1, max_length=150)]
Gender = Literal["Male", "Female"]


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              revalidate_instances="always")

    age: Age | None = None
    gender: Gender | None = None
    annual_income: Income | None = None
    state_or_ut: Category | None = None
    family_annual_income: Income | None = None
    individual_monthly_income: Income | None = None
    social_category: Category | None = None
    occupation: Category | None = None
    employment_status: Category | None = None
    student_status: bool | None = None
    education_level: Category | None = None
    farmer_status: bool | None = None
    disability_status: bool | None = None
    disability_percentage: Percentage | None = None
    bpl_status: bool | None = None
    rural_urban: Literal["Rural", "Urban"] | None = None
    widow_status: bool | None = None
    minority_status: bool | None = None
    marital_status: Category | None = None


class ProfileExtractionResult(UserProfile):
    # Required keys, nullable values: omitted keys are a malformed provider response.
    age: Age | None
    gender: Gender | None
    annual_income: Income | None
