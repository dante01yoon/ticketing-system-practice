"""docs/queue.md ⑥ 5: Redis 가 멈추면 대기열은 503 QUEUE_UNAVAILABLE, 받아 둔 입장권으로 좌석맵·홀드는 계속 동작.

!! `docker compose stop redis` 로 Redis 를 실제로 멈춘다(끝나면 반드시 다시 켠다). 공용 DB·Redis 를 초기화하므로
   다른 자리의 pytest·tests/infra 와 동시에 돌리지 않는다.
"""
import pytest

from apihelpers import assert_error
from cachelib import redis_stopped


@pytest.mark.story("US-08", "Redis 정지")
def test_queue_is_503_but_existing_entry_token_still_works(q):
    ticket = q.admit("u1")
    token = ticket["entry_token"]
    a1 = q.seat_id("A1")
    with redis_stopped():
        assert_error(q.join("u9"), 503, "QUEUE_UNAVAILABLE")  # 새 입장 불가
        assert_error(q.me("u1"), 503, "QUEUE_UNAVAILABLE")
        assert_error(q.join("u1"), 503, "QUEUE_UNAVAILABLE")
        # 받아 둔 입장권은 서명으로 검증하므로 좌석맵·홀드는 DB 로 계속 동작한다
        assert q.seatmap("u1", token).status_code == 200
        r = q.hold(a1, "u1", token)
        assert r.status_code == 201, r.text
        assert r.headers.get("X-Hold-Gate") == "DB"
        # 입장권이 없거나 틀린 요청은 Redis 가 없어도 그대로 거절된다
        assert_error(q.seatmap("u1", None), 403, "ENTRY_TOKEN_REQUIRED")
        assert_error(q.hold(q.seat_id("A2"), "u1", "garbage"), 403, "ENTRY_TOKEN_INVALID")
    # Redis 가 돌아오면 대기열도 다시 된다
    r = q.join("u9")
    assert r.status_code in (200, 201), r.text
