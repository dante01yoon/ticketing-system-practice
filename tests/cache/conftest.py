"""8장 캐시·홀드 관문 확인 픽스처 (docs/cache.md ④).

- Redis 는 compose 의 `redis` 서비스(REDIS_URL, 기본 redis://localhost:6379/0). 안 떠 있으면 skip 이 아니라 실패한다.
- Redis 키 이름·형식은 가정하지 않는다. 필요하면 FLUSHDB 만 쓴다. 동작은 헤더·API 응답으로 본다.
- test_redis_down.py 는 `docker compose stop redis` 로 Redis 를 실제로 멈추고, 끝나면 반드시 다시 켠다.
  공용 DB(5432)·Redis(6379)를 초기화하므로 다른 자리의 pytest 와 동시에 돌리지 않는다.
- backend 는 픽스처 안에서 import 한다 (없으면 이 디렉터리만 ERROR).
"""
import pytest

import cachelib  # noqa: F401  (sys.path 설정)
from apihelpers import Api, Clock
from cachelib import REDIS_URL, redis_client, run_server, wait_redis


@pytest.fixture
def redis_on():
    """Redis 가 켜져 있음을 확인하고 DB 0 을 비운다 (FLUSHDB 만 사용)."""
    wait_redis(True, timeout=15)
    redis_client().flushdb()
    return redis_client()


# ---------- 인프로세스 앱 (가짜 시계) ----------
@pytest.fixture
def api(redis_on):
    from backend.app.deps import get_now
    from backend.app.main import create_app
    from backend.app.seed import reset_db

    reset_db(seed=True)
    redis_client().flushdb()
    app = create_app()
    clock = Clock()
    app.dependency_overrides[get_now] = lambda: clock.now
    api = Api(app, clock).discover()  # discover() 가 좌석맵을 조회해 캐시를 채운다
    redis_client().flushdb()  # 테스트 본문은 빈 캐시에서 시작
    return api


@pytest.fixture
def server_redis(redis_on):
    with run_server(REDIS_URL) as s:
        yield s
