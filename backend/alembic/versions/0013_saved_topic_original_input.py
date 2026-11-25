"""Keep the original prompt alongside an AI-resolved topic title."""

import sqlalchemy as sa

from alembic import op

revision = "0013_saved_topic_original_input"
down_revision = "0012_mistake_practice"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("saved_topics", sa.Column("original_input", sa.String(80), nullable=True))


def downgrade():
    op.drop_column("saved_topics", "original_input")
