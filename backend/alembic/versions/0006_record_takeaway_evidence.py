"""Snapshot evidence cited by a completed record's takeaways."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_record_takeaway_evidence"
down_revision = "0005_summary_retry_keys"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "records",
        sa.Column(
            "takeaway_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute("""
        UPDATE records AS record
        SET takeaway_evidence = (
            SELECT COALESCE(jsonb_agg(matched.source ORDER BY matched.source->>'evidenceId'), '[]')
            FROM (
                SELECT DISTINCT ON (available.source->>'evidenceId') available.source
                FROM (
                    SELECT evidence.value AS source, 1 AS preference
                    FROM attempts AS attempt
                    JOIN quizzes AS quiz ON quiz.id = attempt.quiz_id
                    CROSS JOIN LATERAL jsonb_array_elements(quiz.evidence) AS evidence(value)
                    WHERE attempt.id = record.attempt_id
                    UNION ALL
                    SELECT evidence.value AS source, 2 AS preference
                    FROM jsonb_array_elements(record.answers) AS answer(value)
                    CROSS JOIN LATERAL jsonb_array_elements(answer.value->'evidence') AS evidence(value)
                ) AS available
                WHERE EXISTS (
                    SELECT 1 FROM jsonb_array_elements(record.takeaways) AS takeaway(value)
                    WHERE (takeaway.value->'evidenceIds') ? (available.source->>'evidenceId')
                )
                ORDER BY available.source->>'evidenceId', available.preference
            ) AS matched
        )
    """)


def downgrade():
    op.drop_column("records", "takeaway_evidence")
