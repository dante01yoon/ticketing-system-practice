"""two-step payment: booking status/expires_at, idempotency keys, one CONFIRMED per seat

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 실패·환불 필요 예매가 같은 좌석에 여러 건 생길 수 있으므로 seat_id UNIQUE 를 없앤다.
    op.drop_constraint("bookings_seat_id_key", "bookings", type_="unique")
    op.create_index("ix_bookings_seat_id", "bookings", ["seat_id"])
    op.add_column("bookings", sa.Column("status", sa.String, nullable=False, server_default="CONFIRMED"))
    op.add_column("bookings", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE bookings SET expires_at = created_at + interval '5 minutes'")  # 기존 행 보정
    op.alter_column("bookings", "expires_at", nullable=False)
    op.alter_column("bookings", "status", server_default=None)
    op.create_check_constraint(
        "ck_bookings_status", "bookings", "status IN ('PENDING','CONFIRMED','FAILED','REFUND_REQUIRED')"
    )
    # 좌석당 CONFIRMED 예매는 하나 (코드 잠금이 뚫려도 DB가 막는 두 번째 방어선)
    op.create_index(
        "uq_bookings_seat_confirmed",
        "bookings",
        ["seat_id"],
        unique=True,
        postgresql_where=sa.text("status = 'CONFIRMED'"),
    )
    # 멱등 키: 한 예매에 여러 키가 묶일 수 있으므로 예매와 별도 테이블. 처음 성공 응답을 저장한다.
    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("key", sa.String, nullable=False),
        sa.Column("seat_id", sa.String, nullable=False),
        sa.Column("response_status", sa.Integer, nullable=False),
        sa.Column("response_body", postgresql.JSONB, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("uq_bookings_seat_confirmed", table_name="bookings")
    op.drop_constraint("ck_bookings_status", "bookings", type_="check")
    op.drop_column("bookings", "expires_at")
    op.drop_column("bookings", "status")
    op.drop_index("ix_bookings_seat_id", table_name="bookings")
    op.create_unique_constraint("bookings_seat_id_key", "bookings", ["seat_id"])
