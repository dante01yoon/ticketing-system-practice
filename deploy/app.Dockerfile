FROM python:3.12.16-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /srv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
ENV PATH="/srv/.venv/bin:$PATH" APP_ID=app
EXPOSE 8000
# 스키마·시드는 만들지 않는다(호스트의 reset_db/alembic). 워커 1개 — 연결 예산은 backend/app/db.py 주석 참고.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
