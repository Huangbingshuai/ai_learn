"""Schedule AI summaries for existing completed records."""

from alembic import op

revision = "0003_summary_backfill"
down_revision = "0002_quizzes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO record_ai_summaries
            (record_id, status, next_run_at, call_count, lease_count, error_retryable,
             created_at, expires_at, model_name, rules_version, retry_count)
        SELECT id, 'QUEUED', now(), 0, 0, false, now(), now() + interval '25 minutes',
               'deepseek-flash', 'summary-v1', 0
        FROM records
        ON CONFLICT (record_id) DO NOTHING
    """)


def downgrade():
    # The backfilled rows are legitimate task state and may already have results.
    pass
