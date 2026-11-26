"""Add user-owned favorite questions."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0015_favorites"
down_revision = "0014_attempt_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "favorite_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("question_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("topic_key", sa.String(64), nullable=False),
        sa.Column("topic_title", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "fingerprint", name="uq_favorite_user_fingerprint"),
    )
    op.create_index("ix_favorite_items_user_id", "favorite_items", ["user_id"])


def downgrade():
    op.drop_table("favorite_items")
