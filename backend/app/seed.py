import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from . import cache
from .db import SessionLocal, database_url, engine
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


def _schema_is_head() -> bool:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    heads = set(ScriptDirectory.from_config(cfg).get_heads())
    with engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    return current == heads


def wait_for_schema(timeout: float = 300.0, interval: float = 2.0) -> bool:
    """마이그레이션하지 않고, DB에 접속되고 alembic 리비전이 head가 될 때까지 기다린다. 시간 초과면 False."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if _schema_is_head():
                return True
        except Exception:  # 접속 실패·테이블 없음 등은 재시도
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def seed_if_empty() -> bool:
    """마이그레이션을 head까지 적용하고, 공연이 하나도 없을 때만 시드를 넣는다. 아무것도 지우지 않는다."""
    migrate_head()
    with SessionLocal() as db:
        has_concert = db.execute(select(Concert.id).limit(1)).first() is not None
    if has_concert:
        return False
    insert_seed()
    return True


if __name__ == "__main__":
    if "--wait-schema" in sys.argv[1:]:
        ok = wait_for_schema()
        print("schema ready" if ok else "timeout waiting for schema")
        sys.exit(0 if ok else 1)
    elif "--if-empty" in sys.argv[1:]:
        print("seeded: 1 concert, 100 seats" if seed_if_empty() else "skip: concerts already exist")
    else:
        reset_db(seed=True)
        print("seeded: 1 concert, 100 seats")
