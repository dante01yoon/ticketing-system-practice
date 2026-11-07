"""docs/cache.md ④ 3·4: Redis 홀드 관문(실서버 200명 동시 홀드)과 되돌리기."""
from datetime import timedelta

import pytest

from apihelpers import HOLD_TTL, T0, assert_error
from cachelib import DEAD_REDIS_URL, redis_client, run_server
from helpers import gates, hold_burst, statuses

USERS = 200


def _check_burst(results, expected_gates):
    info = f"상태 {statuses(results)} · 관문 {gates(results)}"
    assert statuses(results) == {201: 1, 409: USERS - 1}, info
    bad = [r["text"][:100] for r in results if r["status"] == 409 and (r["body"] or {}).get("code") != "SEAT_ALREADY_HELD"]
    assert not bad, f"409 code 가 SEAT_ALREADY_HELD 가 아님: {bad[:3]} / {info}"
    assert gates(results) == expected_gates, info


def test_200_users_one_wins_and_redis_rejects_199_before_db(server_redis):
    sid = server_redis.seat_ids()["A1"]
    assert server_redis.workers >= 2
    results = hold_burst(server_redis.base_url, sid, USERS)
    _check_burst(results, {"DB": 1, "REDIS": USERS - 1})


def test_same_burst_without_redis_all_go_to_db(redis_on):
    with run_server(DEAD_REDIS_URL) as s:  # 닿지 않는 REDIS_URL: 앱은 시작에 성공해야 한다
        sid = s.seat_ids()["A1"]
        results = hold_burst(s.base_url, sid, USERS)
        _check_burst(results, {"DB": USERS})


# ---- 되돌리기 (Redis 표시가 틀려도 DB 결과가 이긴다) ----
def test_stale_marker_does_not_block_after_holder_expires(api):
    a1 = api.sid("A1")
    assert api.hold(a1, "A").status_code == 201  # T0, A 만료 T0+5분
    redis_client().flushdb()  # 관문이 A 를 모르는 상태
    api.clock.advance(minutes=2)
    rb = api.hold(a1, "B")  # 표시가 없으니 DB 까지 가서 거절된다
    assert_error(rb, 409, "SEAT_ALREADY_HELD")
    assert rb.headers.get("X-Hold-Gate") == "DB"
    # A 의 만료 시각. B 가 남긴 표시(만료 T0+7분)가 있었다면 C 를 막는다 -> 되돌렸으면 통과
    api.clock.now = T0 + HOLD_TTL
    rc = api.hold(a1, "C")
    assert rc.status_code == 201, rc.text
    assert rc.headers.get("X-Hold-Gate") == "DB"
    assert {s["number"]: s["status"] for s in api.seatmap().json()["seats"]}["A1"] == "HELD"


def test_404_leaves_no_marker(api):
    for user in ("u1", "u2", "u1"):
        r = api.hold("no-such-seat", user)
        assert_error(r, 404, "SEAT_NOT_FOUND")
        # 표시가 남았다면 다음 요청이 Redis 에서 거절/통과 판단을 하겠지만, 어쨌든 DB 까지 가야 404 가 난다
        assert r.headers.get("X-Hold-Gate") == "DB"


def test_failed_hold_marker_does_not_poison_other_seats_or_users(api):
    a1, a2 = api.sid("A1"), api.sid("A2")
    assert api.hold(a1, "A").status_code == 201
    assert_error(api.hold(a1, "B"), 409, "SEAT_ALREADY_HELD")
    assert api.hold(a2, "B").status_code == 201  # B 의 실패가 B 의 다른 좌석을 막지 않는다
    assert api.hold(a1, "A").status_code == 200  # A 의 자기 재홀드는 DB 가 200 으로 판단


@pytest.mark.parametrize("flush", [False, True])
def test_sold_seat_rehold_is_sold_not_held(api, flush):
    a1 = api.sid("A1")
    api.buy(a1, "A")
    if flush:
        redis_client().flushdb()
    for user in ("B", "C"):
        r = api.hold(a1, user)
        assert_error(r, 409, "SEAT_ALREADY_SOLD")
        assert r.headers.get("X-Hold-Gate") in ("REDIS", "DB")
    if flush:
        # 비운 뒤 첫 요청은 관문이 모르니 DB 까지 간다 (그 뒤 표시를 어떻게 하든 SOLD 가 유지돼야 한다)
        assert_error(api.hold(a1, "D"), 409, "SEAT_ALREADY_SOLD")


def test_gate_header_present_on_every_hold_response_after_401(api):
    a1 = api.sid("A1")
    assert api.hold(a1, None).status_code == 401  # X-User-Id 없음: 관문 이전
    r1 = api.hold(a1, "A")
    assert r1.status_code == 201 and r1.headers.get("X-Hold-Gate") == "DB"
    r2 = api.hold(a1, "A")
    assert r2.status_code == 200 and r2.headers.get("X-Hold-Gate") == "DB"  # 자기 재홀드는 통과 -> DB
    r3 = api.hold(a1, "B")
    assert r3.status_code == 409 and r3.headers.get("X-Hold-Gate") in ("REDIS", "DB")


def test_owner_rehold_without_marker_does_not_extend_marker_past_db_expiry(api):
    """리뷰 P1: 표시가 없을 때 소유자의 자기 재홀드(DB 200)가 표시를 now+5분으로 남기면,
    DB 기준 만료(T0+5분) 뒤에도 다른 사용자가 Redis 에서 잘못 409 로 거절된다."""
    a1 = api.sid("A1")
    first = api.hold(a1, "A")
    assert first.status_code == 201, first.text
    redis_client().flushdb()  # 관문이 A 를 모르는 상태
    api.clock.now = T0 + HOLD_TTL - timedelta(seconds=1)  # T0+4분59초
    again = api.hold(a1, "A")
    assert again.status_code == 200, again.text
    assert again.json()["expires_at"] == first.json()["expires_at"]  # 만료 시각 불변
    api.clock.now = T0 + HOLD_TTL  # DB 기준 만료 시각
    rb = api.hold(a1, "B")
    assert rb.status_code == 201, f"{rb.status_code} {rb.text} (X-Hold-Gate={rb.headers.get('X-Hold-Gate')})"
    assert rb.headers.get("X-Hold-Gate") == "DB"
