"""Add delivery hardening state and task recovery metadata."""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
    op.create_table(
        "app_state",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    # Versions before 0.2.0 had no administrator bootstrap. Preserve access to
    # upgraded installations by promoting the oldest account only when no
    # administrator already exists.
    op.execute(
        sa.text(
            """
            UPDATE users
            SET role = 'admin'
            WHERE id = (
                SELECT id FROM users ORDER BY created_at ASC, id ASC LIMIT 1
            )
            AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'admin')
            """
        )
    )


def downgrade() -> None:
    op.drop_table("app_state")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("attempt_count")
