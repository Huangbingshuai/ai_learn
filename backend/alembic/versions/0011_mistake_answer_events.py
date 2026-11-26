"""Persist deduplicated wrong-answer source events."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_mistake_answer_events"
down_revision = "0010_mistake_items"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mistake_answer_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("question_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("receipt_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("topic_key", sa.String(64), nullable=False),
        sa.Column("topic_title", sa.String(100), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "attempt_id", "question_id", name="uq_mistake_answer_event"),
    )
    op.create_index(
        "ix_mistake_events_user_fingerprint",
        "mistake_answer_events",
        ["user_id", "fingerprint"],
    )


def downgrade():
    op.drop_table("mistake_answer_events")
