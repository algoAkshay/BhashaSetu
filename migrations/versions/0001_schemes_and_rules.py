"""Create schemes and data-driven eligibility rules."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schemes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(100), unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("category", sa.String(100)),
        sa.Column("state", sa.String(100)),
        sa.Column("official_url", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_schemes_is_active", "schemes", ["is_active"])
    op.create_table(
        "eligibility_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scheme_id", sa.Integer(), sa.ForeignKey("schemes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field", sa.String(100), nullable=False),
        sa.Column("operator", sa.String(10), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("value_type", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_eligibility_rules_scheme_id", "eligibility_rules", ["scheme_id"])


def downgrade():
    op.drop_table("eligibility_rules")
    op.drop_table("schemes")
