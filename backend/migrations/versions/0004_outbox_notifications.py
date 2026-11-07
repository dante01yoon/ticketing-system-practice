"""10장: outbox(확정 이벤트)와 notifications(알림함)

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("seq", sa.BigInteger, sa.Identity(), primary_key=True),  # 오래된 순서 전송용
        sa.Column("event_id", sa.String, nullable=False),  # 전역 유일(UUID)
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("booking_id", sa.String, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("event_id", name="uq_outbox_event_id"),
        # 확정마다 이벤트 하나: DB 제약으로도 보장
        sa.UniqueConstraint("booking_id", "event_type", name="uq_outbox_booking_event"),
    )
    op.create_index("ix_outbox_unsent", "outbox", ["seq"], postgresql_where=sa.text("sent_at IS NULL"))
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("event_id", sa.String, nullable=False),
        sa.Column("booking_id", sa.String, nullable=False),
        sa.Column("message", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_notifications_event_id"),
    )
    op.create_index("ix_notifications_user", "notifications", ["user_id", "id"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("outbox")
