"""Store the user's optional display nickname."""

import sqlalchemy as sa

from alembic import op

revision = "0009_user_profile"
down_revision = "0008_saved_topics"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("nickname", sa.String(30), nullable=True))
    op.add_column(
        "users", sa.Column("coin_balance", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_table(
        "coin_settlements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("question_fingerprint", sa.String(64), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("coin_delta", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("coin_delta BETWEEN -5 AND 10", name="ck_coin_delta"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "question_fingerprint", name="uq_coin_user_question"),
    )
    op.create_index("ix_coin_settlements_user_id", "coin_settlements", ["user_id"])


def downgrade():
    op.drop_table("coin_settlements")
    op.drop_column("users", "coin_balance")
    op.drop_column("users", "nickname")
