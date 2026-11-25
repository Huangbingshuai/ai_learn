"""Store live topic intent and search budget on generation jobs."""

import sqlalchemy as sa

from alembic import op

revision = "0007_live_topics"
down_revision = "0006_record_takeaway_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("generation_jobs", "topic_key", nullable=True)
    op.alter_column("generation_jobs", "topic_version", nullable=True)
    op.add_column("generation_jobs", sa.Column("topic_input", sa.String(80)))
    op.add_column("generation_jobs", sa.Column("topic_title", sa.String(100)))
    op.add_column(
        "generation_jobs",
        sa.Column("search_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.execute("""
        UPDATE generation_jobs
        SET topic_key = COALESCE(topic_key, 'unavailable'),
            topic_version = COALESCE(topic_version, 'v0')
        WHERE topic_key IS NULL OR topic_version IS NULL
    """)
    op.alter_column("generation_jobs", "topic_key", nullable=False)
    op.alter_column("generation_jobs", "topic_version", nullable=False)
    op.drop_column("generation_jobs", "search_count")
    op.drop_column("generation_jobs", "topic_title")
    op.drop_column("generation_jobs", "topic_input")
