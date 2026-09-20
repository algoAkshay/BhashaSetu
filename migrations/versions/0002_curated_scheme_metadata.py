"""Preserve researched metadata and explicitly unresolved conditions."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TEXT_COLUMNS = (
    "ministry_or_department", "raw_category", "secondary_source_url", "benefit_type",
    "benefit_summary", "benefit_amount", "application_method", "official_application_url",
    "confidence_notes", "other_eligibility_conditions",
)


def upgrade():
    # All additions nullable: existing scheme IDs, rules and values are preserved.
    for name in TEXT_COLUMNS:
        op.add_column("schemes", sa.Column(name, sa.Text(), nullable=True))
    op.add_column("schemes", sa.Column("government_level", sa.String(20), nullable=True))
    op.add_column("schemes", sa.Column("status_confidence", sa.String(30), nullable=True))
    op.add_column("schemes", sa.Column("last_checked_date", sa.Date(), nullable=True))
    op.add_column("schemes", sa.Column("manual_conditions", sa.JSON(), nullable=True))
    op.add_column("schemes", sa.Column("source_metadata", sa.JSON(), nullable=True))
    op.add_column("eligibility_rules", sa.Column("source_metadata", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("eligibility_rules", "source_metadata")
    for name in ("source_metadata", "manual_conditions", "last_checked_date", "status_confidence",
                 "government_level", *reversed(TEXT_COLUMNS)):
        op.drop_column("schemes", name)
