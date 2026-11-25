"""Keep each user's successful themes after the quiz expires."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0008_saved_topics"
down_revision = "0007_live_topics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "saved_topics",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("topic_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("scope", sa.String(240), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("evidence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "topic_key"),
    )
    op.create_index(
        "ix_saved_topics_recent", "saved_topics", ["user_id", "updated_at", "topic_key"]
    )


def downgrade():
    op.drop_table("saved_topics")
