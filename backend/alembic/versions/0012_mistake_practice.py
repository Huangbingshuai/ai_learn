"""Support focused practice attempts without completion records."""

import sqlalchemy as sa

from alembic import op

revision = "0012_mistake_practice"
down_revision = "0011_mistake_answer_events"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_generation_source", "generation_jobs", type_="check")
    op.create_check_constraint(
        "ck_generation_source",
        "generation_jobs",
        "source IN ('TOPIC', 'FIXED', 'PRACTICE')",
    )
    op.add_column(
        "attempts",
        sa.Column("purpose", sa.String(12), server_default="LEARNING", nullable=False),
    )
    op.create_check_constraint(
        "ck_attempt_purpose", "attempts", "purpose IN ('LEARNING', 'PRACTICE')"
    )


def downgrade():
    op.drop_constraint("ck_attempt_purpose", "attempts", type_="check")
    op.drop_column("attempts", "purpose")
    op.drop_constraint("ck_generation_source", "generation_jobs", type_="check")
    op.create_check_constraint(
        "ck_generation_source", "generation_jobs", "source IN ('TOPIC', 'FIXED')"
    )
