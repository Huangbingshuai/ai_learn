"""Keep generated candidates while a review is retried."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_generation_checkpoint"
down_revision = "0003_summary_backfill"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "generation_jobs",
        sa.Column("checkpoint", postgresql.JSONB(none_as_null=True), nullable=True),
    )


def downgrade():
    op.drop_column("generation_jobs", "checkpoint")
