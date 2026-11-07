import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from . import cache
from .db import SessionLocal, database_url
from .models import Concert, Seat

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
ROWS = "ABCDEFGHIJ"


def migrate_head() -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url().replace("%", "%%"))
    command.upgrade(cfg, "head")


def insert_seed() -> None:
    with SessionLocal() as db, db.begin():
        concert = Concert(
            id=str(uuid.uuid4()),
            title="Practice Live 2030",
            starts_at=datetime(2030, 6, 1, 18, 0, tzinfo=timezone.utc),
        )
        db.add(concert)
        db.flush()
        db.add_all(
            Seat(id=str(uuid.uuid4()), concert_id=concert.id, number=f"{r}{n}", status="AVAILABLE")
            for r in ROWS
            for n in range(1, 11)
        )


def _seat_number(i: int) -> str:
    """0 -> A1, 9 -> A10, 10 -> B1 ... 26열(Z) 이후는 AA, AB ... 로 이어진다."""
    row, col = divmod(i, 10)
    letters = ""
    row += 1
    while row:
        row, rem = divmod(row - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{col + 1}"


def add_concert(title: str, starts_at: datetime, seats: int = 0) -> str:
    """공연 하나와 좌석 seats개(A1, A2, ... 열당 10석 순)를 추가하고 공연 ID를 돌려준다."""
    with SessionLocal() as db, db.begin():
        concert = Concert(id=str(uuid.uuid4()), title=title, starts_at=starts_at)
        db.add(concert)
        db.flush()
        db.add_all(
            Seat(id=str(uuid.uuid4()), concert_id=concert.id, number=_seat_number(i), status="AVAILABLE")
            for i in range(seats)
        )
        return concert.id


def reset_db(seed: bool = True) -> None:
    """마이그레이션을 head까지 적용하고 모든 데이터를 지운 뒤 seed=True면 시드를 넣는다."""
    migrate_head()
    with SessionLocal() as db, db.begin():
        db.execute(text("TRUNCATE idempotency_keys, bookings, seats, concerts, outbox, notifications"))
    cache.flush_db()
    if seed:
        insert_seed()


if __name__ == "__main__":
    reset_db(seed=True)
    print("seeded: 1 concert, 100 seats")
