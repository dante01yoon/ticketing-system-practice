"""API 테스트 픽스처. 접점 이름은 docs/backend-interface.md 를 따른다.

- 실제 PostgreSQL 사용 (docker compose up -d --wait). 테스트마다 reset_db() 로 초기화.
- 시간은 app.dependency_overrides[get_now] 로 고정·이동한다 (5분을 실제로 기다리지 않는다).
- backend 는 픽스처 안에서 import 한다: 없으면 이 디렉터리 테스트만 ERROR 가 나고(skip 아님) 다른 테스트는 영향 없다.
"""
import pytest

from apihelpers import Api, Clock


def _build(seed):
    from backend.app.deps import get_now
    from backend.app.main import create_app
    from backend.app.seed import reset_db

    reset_db(seed=seed)
    app = create_app()
    clock = Clock()
    app.dependency_overrides[get_now] = lambda: clock.now
    return Api(app, clock)


@pytest.fixture
def api():
    return _build(seed=True).discover()


@pytest.fixture
def api_empty():
    return _build(seed=False)
