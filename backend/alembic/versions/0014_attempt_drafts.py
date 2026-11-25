"""Persist the current unanswered selection for cross-device resume."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_attempt_drafts"
down_revision = "0013_saved_topic_original_input"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "attempts",
        sa.Column(
            "draft_option_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade():
    op.drop_column("attempts", "draft_option_ids")
