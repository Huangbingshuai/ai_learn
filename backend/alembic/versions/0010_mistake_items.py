"""Add per-user mistake book snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_mistake_items"
down_revision = "0009_user_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mistake_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("question_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("latest_receipt", postgresql.JSONB(), nullable=False),
        sa.Column("topic_key", sa.String(64), nullable=False),
        sa.Column("topic_title", sa.String(100), nullable=False),
        sa.Column("wrong_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_wrong_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_wrong_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), server_default="TO_REVIEW", nullable=False),
        sa.Column("mastered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("wrong_count > 0", name="ck_mistake_wrong_count"),
        sa.CheckConstraint("status IN ('TO_REVIEW', 'MASTERED')", name="ck_mistake_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "fingerprint", name="uq_mistake_user_fingerprint"),
    )
    op.create_index(
        "ix_mistakes_user_recent",
        "mistake_items",
        ["user_id", sa.text("last_wrong_at DESC"), sa.text("fingerprint DESC")],
    )
    op.create_index("ix_mistakes_user_status", "mistake_items", ["user_id", "status"])
    op.create_index("ix_mistakes_user_topic", "mistake_items", ["user_id", "topic_key"])


def downgrade():
    op.drop_table("mistake_items")
