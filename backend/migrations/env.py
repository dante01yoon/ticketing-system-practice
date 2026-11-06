import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
URL = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or (
    "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing"
)
# 같은 프로세스에서 reset_db()가 여러 번 호출돼도 동시에 돌지 않도록 어드바이저리 락을 잡는다.
LOCK_KEY = 727001

if context.is_offline_mode():
    context.configure(url=URL, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(URL, poolclass=pool.NullPool)
    with engine.connect() as conn:
        conn.exec_driver_sql(f"SELECT pg_advisory_lock({LOCK_KEY})")
        try:
            context.configure(connection=conn)
            with context.begin_transaction():
                context.run_migrations()
            conn.commit()
        finally:
            conn.exec_driver_sql(f"SELECT pg_advisory_unlock({LOCK_KEY})")
            conn.commit()
