"""idempotency_keys.key 길이를 계약(255자)에 맞춘다

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("idempotency_keys", "key", type_=sa.String(255), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("idempotency_keys", "key", type_=sa.String(), existing_nullable=False)
