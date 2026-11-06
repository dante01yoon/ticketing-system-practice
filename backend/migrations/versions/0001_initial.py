"""initial: concerts, seats, bookings

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "concerts",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "seats",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("concert_id", sa.String, sa.ForeignKey("concerts.id"), nullable=False, index=True),
        sa.Column("number", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="AVAILABLE"),
        sa.Column("held_by", sa.String),
        sa.Column("held_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("concert_id", "number"),
        sa.CheckConstraint("status IN ('AVAILABLE','HELD','SOLD')", name="ck_seats_status"),
    )
    op.create_table(
        "bookings",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("seat_id", sa.String, sa.ForeignKey("seats.id"), nullable=False, unique=True),
        sa.Column("concert_id", sa.String, sa.ForeignKey("concerts.id"), nullable=False),
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bookings")
    op.drop_table("seats")
    op.drop_table("concerts")
