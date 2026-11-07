"""실서버 동시성 테스트 픽스처: uvicorn 워커 여러 개를 별도 프로세스로 띄워 HTTP 로 요청한다.

- DB 주소는 DATABASE_URL (서버 프로세스에도 같은 env 를 넘긴다). 시작 전 reset_db(seed=True).
- 워커 수: env CONCURRENCY_WORKERS (기본 4).
- DB·서버가 안 뜨면 skip 이 아니라 실패한다.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

from entrylib import QUEUE_ENV, EntryMixin

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
START_TIMEOUT_S = 60


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _raise_fd_limit(want=8192):
    # macOS 기본 소프트 한도(256)로는 연결 수백 개를 못 연다
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = want if hard == resource.RLIM_INFINITY else min(want, hard)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ImportError, ValueError, OSError):
        pass


class Server(EntryMixin):
    """서버 프로세스 핸들. 공연·좌석 ID 와 입장권은 캐시하지 않고 매번 API 로 찾는다
    (다른 테스트의 reset_db 로 ID 가 바뀌어도 순서와 무관하게 동작하도록)."""

    def __init__(self, base_url, workers):
        self.base_url = base_url
        self.workers = workers

    def seat_status(self, seat_id):
        return next(s["status"] for s in self.seatmap() if s["id"] == seat_id)


@pytest.fixture(scope="session")
def server():
    _raise_fd_limit()
    workers = int(os.environ.get("CONCURRENCY_WORKERS", "4"))
    from backend.app.seed import reset_db  # 없으면 이 디렉터리 테스트만 ERROR (skip 아님)

    reset_db(seed=True)
    port = _free_port()
    env = dict(os.environ)
    env.setdefault("DATABASE_URL", "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing")
    env.update(QUEUE_ENV)  # 9장: 기다림 없이 입장
    log = tempfile.NamedTemporaryFile("w+", suffix=".uvicorn.log", delete=False)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--workers", str(workers), "--port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + START_TIMEOUT_S
        while True:
            if proc.poll() is not None:
                log.seek(0)
                pytest.fail(f"uvicorn 이 종료됨 (코드 {proc.returncode}):\n{log.read()[-2000:]}")
            try:
                if httpx.get(f"{base_url}/concerts", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                log.seek(0)
                pytest.fail(f"uvicorn 이 {START_TIMEOUT_S}s 안에 준비되지 않음:\n{log.read()[-2000:]}")
            time.sleep(0.3)
        yield Server(base_url, workers)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        # 서버 로그의 예외 요약을 남긴다 (5xx 원인 확인용)
        try:
            log.seek(0)
            from collections import Counter

            errs = Counter(l.strip() for l in log.read().splitlines() if l.startswith(("sqlalchemy.", "psycopg", "Exception", "ValueError", "RuntimeError")) or "Error" in l.split(":")[0][-20:])
            if errs:
                print("\n[uvicorn 로그 예외 요약]")
                for line, n in errs.most_common(5):
                    print(f"  {n}x {line[:200]}")
        except Exception:  # noqa: BLE001
            pass
        log.close()
        try:
            os.unlink(log.name)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def fresh_db(server):
    """테스트마다 시작할 때 DB 를 초기화한다 (서버 프로세스는 session 범위로 유지)."""
    from backend.app.seed import reset_db

    reset_db(seed=True)
