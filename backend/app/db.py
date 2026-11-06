import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DEFAULT_DATABASE_URL = "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


# 연결 수 예산: 워커 수 × (DB_POOL_SIZE + DB_MAX_OVERFLOW) 는 Postgres max_connections(기본 100)보다 작아야 한다.
# 기본 12/워커 → 워커 4개면 48. 이 합계에는 서버 워커 외의 연결(테스트·벤치·마이그레이션 프로세스)과
# Postgres 가 예약해 두는 연결(superuser_reserved_connections 등)도 더해서 따져야 한다.
# 풀이 가득 차면 요청은 최대 DB_POOL_TIMEOUT 초(기본 60) 기다린 뒤 SQLAlchemy TimeoutError 로 실패하며,
# 현재 그 오류는 처리되지 않아 500 으로 응답된다 (503 등으로 바꾸지는 않았다).
engine = create_engine(
    database_url(),
    pool_size=int(os.environ.get("DB_POOL_SIZE", "8")),
    max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "4")),
    pool_timeout=float(os.environ.get("DB_POOL_TIMEOUT", "60")),
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
