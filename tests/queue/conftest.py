"""9장 가상 대기열 확인 픽스처 (docs/queue.md ⑥).

- Redis 는 compose 의 `redis`(REDIS_URL). 안 떠 있으면 skip 이 아니라 실패한다. 공용 DB·Redis 를 초기화하므로
  다른 자리의 pytest·tests/infra 와 동시에 돌리지 않는다.
- make_api(batch=3, interval=10, ttl=120): 가짜 시계 인프로세스 앱. 작은 설정은 create_app() 전에 env 로 넣는다.
- queue_server: 실서버(uvicorn 워커 4개), 작은 QUEUE_BATCH·긴 간격(전광판은 처음 한 묶음만 열린다).
- backend 는 픽스처 안에서 import 한다 (없으면 이 디렉터리만 ERROR).
"""
import pytest

import queuelib  # noqa: F401  (sys.path 설정)
from cachelib import REDIS_URL, redis_client, run_server, wait_redis
from queuelib import Clock, Q


@pytest.fixture
def redis_on():
    wait_redis(True, timeout=15)
    redis_client().flushdb()
    return redis_client()


@pytest.fixture
def make_api(redis_on, monkeypatch):
    def make(batch=3, interval=10, ttl=120):
        monkeypatch.setenv("QUEUE_BATCH", str(batch))
        monkeypatch.setenv("QUEUE_INTERVAL_SECONDS", str(interval))
        monkeypatch.setenv("ENTRY_TTL_SECONDS", str(ttl))
        from backend.app.deps import get_now
        from backend.app.main import create_app
        from backend.app.seed import reset_db

        reset_db(seed=True)
        redis_client().flushdb()
        app = create_app()
        clock = Clock()
        app.dependency_overrides[get_now] = lambda: clock.now
        return Q(app, clock)

    return make


@pytest.fixture
def q(make_api):
    return make_api()


@pytest.fixture(scope="module")
def queue_server():
    wait_redis(True, timeout=15)
    with run_server(REDIS_URL, workers=4, env_extra={"QUEUE_BATCH": "10", "QUEUE_INTERVAL_SECONDS": "3600"}) as s:
        yield s


@pytest.fixture
def fresh_queue_server(queue_server):
    """테스트마다 DB·Redis 를 비운다 (서버 프로세스는 모듈 범위로 유지)."""
    from backend.app.seed import reset_db

    reset_db(seed=True)
    redis_client().flushdb()
    return queue_server
