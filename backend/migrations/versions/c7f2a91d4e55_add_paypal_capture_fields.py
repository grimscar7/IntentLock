"""add paypal capture fields to payment_attempts

Revision ID: c7f2a91d4e55
Revises: a3d4f827a209
"""
from alembic import op
import sqlalchemy as sa

revision = "c7f2a91d4e55"
down_revision = "a3d4f827a209"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_attempts", sa.Column("paypal_capture_id", sa.String(128), nullable=True))
    op.add_column("payment_attempts", sa.Column("last_error_code", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_attempts", "last_error_code")
    op.drop_column("payment_attempts", "paypal_capture_id")
