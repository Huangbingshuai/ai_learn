"""Create application identities, sessions and rate limits."""

from alembic import op
import sqlalchemy as sa

revision = "0001_sessions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("app_id", sa.String(80), nullable=False),
        sa.Column("openid", sa.String(128), nullable=False),
        sa.UniqueConstraint("app_id", "openid", name="uq_users_wechat_identity"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.String(180), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_limits_expires_at", "rate_limits", ["expires_at"])


def downgrade():
    op.drop_table("rate_limits")
    op.drop_table("sessions")
    op.drop_table("users")
