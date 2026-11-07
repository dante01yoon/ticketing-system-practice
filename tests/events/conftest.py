"""10장 알림 확인 픽스처 (docs/events.md ⑤).

- Kafka(compose events 프로필)가 안 떠 있으면 skip 이 아니라 실패한다. 공용 DB 를 지우고 kafka 를 멈추므로
  다른 자리의 pytest·tests/infra 와 동시에 돌리지 않는다.
- backend 는 픽스처·테스트 안에서 import 한다 (미구현이면 이 디렉터리만 실패).
- 토픽에는 이전 테스트 메시지가 남으므로 Notifier 를 쓰는 테스트는 시작할 때 그룹을 끝까지 따라잡게 한다 (caught_up).
"""
import pytest

import eventslib
from apihelpers import Api, Clock


@pytest.fixture(autouse=True)
def _kafka_must_be_up():
    if not eventslib.kafka_up():
        pytest.fail(
            "Kafka 가 떠 있지 않음: docker compose --profile events up -d --wait kafka (skip 하지 않고 실패)", pytrace=False
        )


@pytest.fixture(autouse=True)
def _big_queue_batch(monkeypatch):
    monkeypatch.setenv("QUEUE_BATCH", "100000")  # 입장권을 기다림 없이 받는다


@pytest.fixture
def api():
    from backend.app.deps import get_now
    from backend.app.main import create_app
    from backend.app.seed import reset_db

    reset_db(seed=True)
    app = create_app()
    clock = Clock()
    app.dependency_overrides[get_now] = lambda: clock.now
    return Api(app, clock).discover()


@pytest.fixture
def caught_up(api):
    """이전 테스트가 토픽에 남긴 메시지를 먼저 소비해 둔다 (api 로 DB 를 비운 뒤)."""
    eventslib.drain_notifier()


@pytest.fixture
def make_notifier(caught_up):
    """Notifier 를 만들어 주고, 테스트가 끝나면 닫는다."""
    from backend.app.notifier import Notifier

    made = []

    def make():
        n = Notifier()
        made.append(n)
        return n

    yield make
    for n in made:
        try:
            n.close()
        except Exception:  # noqa: BLE001
            pass
