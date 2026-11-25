"""Preserve idempotency keys for manual summary retries."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_summary_retry_keys"
down_revision = "0004_generation_checkpoint"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "record_ai_summaries",
        sa.Column(
            "retry_keys",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute("""
        UPDATE record_ai_summaries
        SET retry_keys = jsonb_build_array(retry_key)
        WHERE retry_key IS NOT NULL
    """)
    op.execute("""
        UPDATE record_ai_summaries
        SET retry_count = 0, error_retryable = true
        WHERE status = 'FAILED' AND error_code = 'RATE_LIMITED'
          AND retry_count = 1 AND call_count = 0
    """)


def downgrade():
    op.drop_column("record_ai_summaries", "retry_keys")
