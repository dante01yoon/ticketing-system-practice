from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Concert(Base):
    __tablename__ = "concerts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Seat(Base):
    """status: AVAILABLE | HELD | SOLD (저장값). 만료 여부는 조회 시 get_now()로 판단한다."""

    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("concert_id", "number"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    concert_id: Mapped[str] = mapped_column(ForeignKey("concerts.id"), nullable=False, index=True)
    number: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="AVAILABLE")
    held_by: Mapped[str | None] = mapped_column(String)
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    seat_id: Mapped[str] = mapped_column(ForeignKey("seats.id"), nullable=False, index=True)
    concert_id: Mapped[str] = mapped_column(ForeignKey("concerts.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyKey(Base):
    """사용자별 멱등 키. 처음 성공 응답(상태 코드·본문)을 저장해 그대로 돌려준다."""

    __tablename__ = "idempotency_keys"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    seat_id: Mapped[str] = mapped_column(String, nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
