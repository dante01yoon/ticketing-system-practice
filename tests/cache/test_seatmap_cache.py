"""docs/cache.md ④ 1·2: 좌석맵 캐시(MISS→HIT, 5초 TTL, 쓰기·만료 때 무효화)."""
import time

import pytest

from apihelpers import HOLD_TTL


def _gate_cache(r):
    assert r.status_code == 200, r.text
    return r.headers.get("X-Cache")


def _status(r, number):
    return {s["number"]: s["status"] for s in r.json()["seats"]}[number]


def test_first_miss_then_hit_with_same_body(api):
    r1 = api.seatmap()
    assert _gate_cache(r1) == "MISS"
    r2 = api.seatmap()
    assert _gate_cache(r2) == "HIT"
    assert r1.json() == r2.json()
    assert r1.content == r2.content  # 같은 JSON 내용(필드·순서·상태 표현 동일)


def test_hit_turns_into_miss_after_5_seconds(api):
    assert _gate_cache(api.seatmap()) == "MISS"
    assert _gate_cache(api.seatmap()) == "HIT"
    time.sleep(5.3)  # Redis 실제 시간 TTL
    r = api.seatmap()
    assert _gate_cache(r) == "MISS"
    assert _gate_cache(api.seatmap()) == "HIT"  # 다시 채워진다


def test_unknown_concert_is_404_and_not_cached(api):
    for _ in range(2):
        r = api.seatmap(concert_id="no-such-concert")
        assert r.status_code == 404, r.text
        assert r.headers.get("X-Cache") != "HIT"


def test_hold_201_is_visible_immediately_despite_cache(api):
    a1 = api.sid("A1")
    assert _gate_cache(api.seatmap()) == "MISS"
    assert _gate_cache(api.seatmap()) == "HIT"
    assert api.hold(a1, "u1").status_code == 201
    r = api.seatmap()
    assert _status(r, "A1") == "HELD"
    assert r.headers.get("X-Cache") == "MISS"  # 홀드가 캐시를 지웠다


def test_success_callback_sold_is_visible_immediately(api):
    a1 = api.sid("A1")
    booking = api.pending(a1, "u1")
    assert _status(api.seatmap(), "A1") == "HELD"
    assert _gate_cache(api.seatmap()) == "HIT"  # 확정 요청은 좌석 불변: 캐시가 있어도 틀리지 않는다
    assert api.callback(booking["id"], "SUCCEEDED").status_code == 200
    r = api.seatmap()
    assert _status(r, "A1") == "SOLD"
    assert r.headers.get("X-Cache") == "MISS"


def test_fake_clock_at_hold_expiry_shows_available(api):
    a1 = api.sid("A1")
    assert api.hold(a1, "u1").status_code == 201
    assert _status(api.seatmap(), "A1") == "HELD"
    assert _gate_cache(api.seatmap()) == "HIT"  # 캐시에 HELD 가 들어 있다
    api.clock.advance(seconds=int(HOLD_TTL.total_seconds()) - 1)
    assert _status(api.seatmap(), "A1") == "HELD"  # 만료 직전
    api.clock.advance(seconds=1)  # 정확히 만료 시각: get_now >= 만료
    r = api.seatmap()
    assert _status(r, "A1") == "AVAILABLE"
    assert r.headers.get("X-Cache") == "MISS"


def test_expired_hold_rehold_by_other_user_is_visible(api):
    a1 = api.sid("A1")
    assert api.hold(a1, "u1").status_code == 201
    assert _gate_cache(api.seatmap()) == "MISS"
    api.clock.advance(seconds=int(HOLD_TTL.total_seconds()))
    assert api.hold(a1, "u2").status_code == 201  # 만료된 홀드 재홀드
    r = api.seatmap()
    assert _status(r, "A1") == "HELD"
    assert r.headers.get("X-Cache") == "MISS"
