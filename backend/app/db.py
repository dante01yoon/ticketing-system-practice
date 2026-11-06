import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DEFAULT_DATABASE_URL = "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


engine = create_engine(database_url(), pool_size=20, max_overflow=20, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
