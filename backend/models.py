"""Portable ORM types, with PostgreSQL as the runtime database."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Scheme(Timestamps, Base):
    __tablename__ = "schemes"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Import identity is separate from editable names; null for manually created schemes.
    source_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    official_url: Mapped[str | None] = mapped_column(Text)
    government_level: Mapped[str | None] = mapped_column(String(20))
    ministry_or_department: Mapped[str | None] = mapped_column(Text)
    raw_category: Mapped[str | None] = mapped_column(Text)
    status_confidence: Mapped[str | None] = mapped_column(String(30))
    secondary_source_url: Mapped[str | None] = mapped_column(Text)
    last_checked_date: Mapped[date | None] = mapped_column(Date)
    benefit_type: Mapped[str | None] = mapped_column(Text)
    benefit_summary: Mapped[str | None] = mapped_column(Text)
    benefit_amount: Mapped[str | None] = mapped_column(Text)
    application_method: Mapped[str | None] = mapped_column(Text)
    official_application_url: Mapped[str | None] = mapped_column(Text)
    confidence_notes: Mapped[str | None] = mapped_column(Text)
    other_eligibility_conditions: Mapped[str | None] = mapped_column(Text)
    manual_conditions: Mapped[list[str] | None] = mapped_column(JSON)
    source_metadata: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    rules: Mapped[list["EligibilityRule"]] = relationship(
        back_populates="scheme", cascade="all, delete-orphan", passive_deletes=True,
        order_by="EligibilityRule.id", lazy="raise",
    )
    __table_args__ = (Index("ix_schemes_is_active", "is_active"),)


class EligibilityRule(Timestamps, Base):
    __tablename__ = "eligibility_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    scheme_id: Mapped[int] = mapped_column(ForeignKey("schemes.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(100))
    operator: Mapped[str] = mapped_column(String(10))
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    value_type: Mapped[str] = mapped_column(String(20))
    source_metadata: Mapped[dict | None] = mapped_column(JSON)
    scheme: Mapped[Scheme] = relationship(back_populates="rules")
