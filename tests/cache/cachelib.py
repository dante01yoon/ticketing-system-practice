"""8장 캐시 테스트 공용 유틸 (Redis 제어, 실서버 기동). 픽스처는 conftest.py."""
import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "concurrency"))

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"  # 아무도 듣지 않는 포트: 연결 거부
START_TIMEOUT_S = 60

# ---------- Redis ----------
def redis_client():
    import redis

    return redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=2)


def redis_up():
    try:
        return bool(redis_client().ping())
    except Exception:  # noqa: BLE001
        return False


def wait_redis(up, timeout=40.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if redis_up() == up:
            return
        time.sleep(0.3)
    pytest.fail(f"Redis 가 {timeout:.0f}초 안에 {'켜지지' if up else '꺼지지'} 않음 ({REDIS_URL})", pytrace=False)


def compose(*args, timeout=300):
    r = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        pytest.fail(f"docker compose {' '.join(args)} 실패: {r.stderr[-800:]}", pytrace=False)
    return r


@contextlib.contextmanager
def redis_stopped():
    """compose 의 redis 를 멈춘다. 나갈 때 반드시 다시 켜고 응답할 때까지 기다린다."""
    try:
        compose("stop", "redis")
        wait_redis(False)
        yield
    finally:
        subprocess.run(["docker", "compose", "up", "-d", "redis"], cwd=ROOT, capture_output=True, text=True, timeout=300)
        wait_redis(True)


# ---------- 실서버 (uvicorn 워커 여러 개) ----------
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _raise_fd_limit(want=8192):
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = want if hard == resource.RLIM_INFINITY else min(want, hard)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ImportError, ValueError, OSError):
        pass


class Server:
    def __init__(self, base_url, workers):
        self.base_url = base_url
        self.workers = workers

    def seat_ids(self):
        concerts = httpx.get(f"{self.base_url}/concerts", timeout=30).json()["items"]
        r = httpx.get(f"{self.base_url}/concerts/{concerts[0]['id']}/seats", timeout=30)
        assert r.status_code == 200, r.text
        return {s["number"]: s["id"] for s in r.json()["seats"]}


@contextlib.contextmanager
def run_server(redis_url, workers=4):
    """uvicorn 워커 `workers` 개를 REDIS_URL=redis_url 로 띄운다. 시작 전 reset_db(seed=True) + FLUSHDB."""
    _raise_fd_limit()
    from backend.app.seed import reset_db

    reset_db(seed=True)
    if redis_up():
        redis_client().flushdb()
    port = _free_port()
    env = dict(os.environ)
    env.setdefault("DATABASE_URL", "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing")
    env["REDIS_URL"] = redis_url
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
                pytest.fail(f"uvicorn 이 종료됨 (코드 {proc.returncode}):\n{log.read()[-2000:]}", pytrace=False)
            try:
                if httpx.get(f"{base_url}/concerts", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                log.seek(0)
                pytest.fail(f"uvicorn 이 {START_TIMEOUT_S}s 안에 준비되지 않음:\n{log.read()[-2000:]}", pytrace=False)
            time.sleep(0.3)
        yield Server(base_url, workers)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        try:
            os.unlink(log.name)
        except OSError:
            pass
