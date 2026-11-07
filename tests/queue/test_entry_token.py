"""docs/queue.md ⑥ 4: 입장권 만료(2분 정각)·남의 입장권·변조·다른 공연, 거절된 홀드는 좌석 무변화, 만료 뒤 재입장.
가짜 시계 인프로세스 앱, QUEUE_BATCH=3 · 간격 10초 · 입장권 120초."""
from datetime import timedelta

import pytest

from apihelpers import assert_error
from queuelib import T0, parse_dt, tamper


def _two_admitted(q):
    return q.admit("u1")["entry_token"], q.admit("u2")["entry_token"]


@pytest.mark.story("US-08", "만료")
def test_expires_exactly_at_two_minutes_1m59s_still_passes(q):
    t1, _ = _two_admitted(q)
    a1, a2, a3 = q.seat_id("A1"), q.seat_id("A2"), q.seat_id("A3")
    q.clock.now = T0 + timedelta(seconds=119)
    assert q.seatmap("u1", t1).status_code == 200
    assert q.hold(a1, "u1", t1).status_code == 201
    q.clock.now = T0 + timedelta(seconds=120)  # get_now >= 만료 시각
    assert_error(q.seatmap("u1", t1), 403, "ENTRY_TOKEN_EXPIRED")
    for seat in (a2, a3):
        r = q.hold(seat, "u1", t1)
        assert_error(r, 403, "ENTRY_TOKEN_EXPIRED")
        assert r.headers.get("X-Hold-Gate") is None  # 입장권 403 은 Redis 관문 전
        assert q.seat_raw_status(seat) == "AVAILABLE", "거절된 홀드가 좌석을 바꿈"


@pytest.mark.story("US-08", "만료")
def test_hold_and_booking_survive_entry_expiry(q):
    t1, _ = _two_admitted(q)
    a1 = q.seat_id("A1")
    q.clock.now = T0 + timedelta(seconds=100)
    assert q.hold(a1, "u1", t1).status_code == 201
    q.clock.now = T0 + timedelta(seconds=130)  # 입장권은 만료, 홀드(5분)는 유효
    assert_error(q.seatmap("u1", t1), 403, "ENTRY_TOKEN_EXPIRED")
    r = q.confirm(a1, "u1", "k-after-expiry")
    assert r.status_code == 201, r.text
    cb = q.callback(r.json()["id"])
    assert cb.status_code == 200 and cb.json()["status"] == "CONFIRMED", cb.text


@pytest.mark.story("US-08", "만료")
def test_after_expiry_status_is_expired_and_joining_again_gives_new_last_number(q):
    t1, _ = _two_admitted(q)
    old = q.me("u1").json()
    assert old["number"] == 1
    q.clock.now = T0 + timedelta(seconds=119)
    assert q.join("u1").status_code == 200  # 유효한 동안은 같은 번호
    q.clock.now = T0 + timedelta(seconds=120)
    b = q.me("u1").json()
    assert (b["status"], b["entry_token"], b["entry_expires_at"], b["ahead"], b["retry_after"]) == ("EXPIRED", None, None, 0, None), b
    r = q.join("u1")
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["number"] == 3, "만료 뒤 다시 서면 맨 뒤의 새 번호(1, 2 다음)"
    assert new["status"] == "ADMITTED" and new["entry_token"] != t1  # 한참 쉰 뒤라 바로 입장
    assert parse_dt(new["entry_expires_at"]) == T0 + timedelta(seconds=240)
    assert q.seatmap("u1", new["entry_token"]).status_code == 200
    assert_error(q.seatmap("u1", t1), 403, "ENTRY_TOKEN_EXPIRED")  # 옛 입장권은 그대로 만료


@pytest.mark.story("US-08", "남의 입장권")
def test_other_users_entry_token_is_403_invalid_and_changes_nothing(q):
    t1, t2 = _two_admitted(q)
    a1 = q.seat_id("A1")
    assert_error(q.seatmap("u1", t2), 403, "ENTRY_TOKEN_INVALID")
    r = q.hold(a1, "u1", t2)
    assert_error(r, 403, "ENTRY_TOKEN_INVALID")
    assert r.headers.get("X-Hold-Gate") is None
    assert q.seat_raw_status(a1) == "AVAILABLE"
    assert q.hold(a1, "u2", t2).status_code == 201  # 제 주인이 쓰면 통과


@pytest.mark.story("US-08", "변조")
@pytest.mark.parametrize("where", ["first", "middle", "last"])
def test_tampered_entry_token_is_403_invalid(q, where):
    t1, _ = _two_admitted(q)
    a1 = q.seat_id("A1")
    bad = tamper(t1, where)
    assert bad != t1
    assert_error(q.seatmap("u1", bad), 403, "ENTRY_TOKEN_INVALID")
    assert_error(q.hold(a1, "u1", bad), 403, "ENTRY_TOKEN_INVALID")
    assert q.seat_raw_status(a1) == "AVAILABLE"


@pytest.mark.story("US-08", "변조")
@pytest.mark.parametrize("bad", ["garbage", "x", "a.b.c", "0" * 64, "%00"])
def test_malformed_entry_token_is_403_invalid(q, bad):
    _two_admitted(q)
    a1 = q.seat_id("A1")
    assert_error(q.seatmap("u1", bad), 403, "ENTRY_TOKEN_INVALID")
    assert_error(q.hold(a1, "u1", bad), 403, "ENTRY_TOKEN_INVALID")
    assert q.seat_raw_status(a1) == "AVAILABLE"


@pytest.mark.story("US-08", "다른 공연")
def test_entry_token_of_another_concert_is_403_invalid(q):
    from backend.app.seed import add_concert

    t1, _ = _two_admitted(q)
    other = add_concert("두 번째 공연", T0 + timedelta(days=30), seats=3)
    other_seat = q.seat_id("A1", other)
    assert_error(q.seatmap("u1", t1, other), 403, "ENTRY_TOKEN_INVALID")
    r = q.hold(other_seat, "u1", t1)  # 좌석의 공연 ≠ 입장권의 공연
    assert_error(r, 403, "ENTRY_TOKEN_INVALID")
    assert r.headers.get("X-Hold-Gate") is None  # 다른 공연의 입장권은 관문 전에 판단한다
    assert q.seat_raw_status(other_seat) == "AVAILABLE"


@pytest.mark.story("US-08", "판단 순서")
def test_check_order_user_then_concert_then_entry_token(q):
    t1, _ = _two_admitted(q)
    a1 = q.seat_id("A1")
    assert_error(q.seatmap(None, t1), 401, "USER_ID_REQUIRED")  # 사용자 없음: 입장권이 있어도 401
    assert_error(q.seatmap("", t1), 401, "USER_ID_REQUIRED")
    assert_error(q.seatmap("u1", None, "no-such-concert"), 404, "CONCERT_NOT_FOUND")  # 공연 없음이 입장권보다 먼저
    assert_error(q.seatmap("u1", "garbage", "abc%00def"), 404, "CONCERT_NOT_FOUND")
    assert_error(q.hold(a1, None, t1), 401, "USER_ID_REQUIRED")
    assert_error(q.hold("no-such-seat", "u1", None), 403, "ENTRY_TOKEN_REQUIRED")  # 입장권이 DB(404)보다 먼저
    assert_error(q.hold("no-such-seat", "u1", "garbage"), 403, "ENTRY_TOKEN_INVALID")
    r = q.hold("no-such-seat", "u1", t1)  # 입장권이 맞으면 DB 단계의 404
    assert_error(r, 404, "SEAT_NOT_FOUND")
    assert r.headers.get("X-Hold-Gate") == "DB"


# ---------- 다른 공연의 입장권은 좌석 상태·Redis 표시와 무관하게 언제나 403 INVALID (관문 전, X-Hold-Gate 없음) ----------
def _other_concert(q):
    from backend.app.seed import add_concert

    return add_concert("두 번째 공연", T0 + timedelta(days=30), seats=3)


@pytest.mark.story("US-08", "다른 공연")
@pytest.mark.parametrize("flush_redis", [False, True], ids=["marker", "no_marker"])
def test_other_concert_token_on_held_seat_is_403_and_hold_unchanged(q, flush_redis):
    from cachelib import redis_client

    b = _other_concert(q)
    b_token = q.admit("owner-b", b)["entry_token"]
    a_token = q.admit("owner-a")["entry_token"]
    seat = q.seat_id("A1", b)
    first = q.hold(seat, "owner-b", b_token)
    assert first.status_code == 201, first.text  # 관문 표시가 남는다
    if flush_redis:
        redis_client().flushdb()  # 표시 없음 (입장권은 서명이라 그대로 유효)
    r = q.hold(seat, "owner-a", a_token)
    assert_error(r, 403, "ENTRY_TOKEN_INVALID")
    assert r.headers.get("X-Hold-Gate") is None, "다른 공연 입장권은 Redis 관문 전에 거절돼야 함(409·REDIS 가 아니라)"
    assert q.seat_raw_status(seat) == "HELD"
    again = q.hold(seat, "owner-b", b_token)  # 주인의 홀드는 그대로 (만료 시각 불변)
    assert again.status_code == 200 and again.json() == first.json(), again.text


@pytest.mark.story("US-08", "다른 공연")
@pytest.mark.parametrize("flush_redis", [False, True], ids=["marker", "no_marker"])
def test_other_concert_token_on_sold_seat_is_403(q, flush_redis):
    from cachelib import redis_client

    b = _other_concert(q)
    b_token = q.admit("owner-b", b)["entry_token"]
    a_token = q.admit("owner-a")["entry_token"]
    seat = q.seat_id("A1", b)
    assert q.hold(seat, "owner-b", b_token).status_code == 201
    booking = q.confirm(seat, "owner-b", "k-sold")
    assert booking.status_code == 201, booking.text
    assert q.callback(booking.json()["id"]).json()["status"] == "CONFIRMED"
    assert q.seat_raw_status(seat) == "SOLD"
    if flush_redis:
        redis_client().flushdb()
    r = q.hold(seat, "owner-a", a_token)
    assert_error(r, 403, "ENTRY_TOKEN_INVALID")
    assert r.headers.get("X-Hold-Gate") is None
    assert q.seat_raw_status(seat) == "SOLD"


@pytest.mark.story("US-08", "다른 공연")
def test_unknown_seat_with_valid_token_still_goes_gate_then_db_404(q):
    token = q.admit("u1")["entry_token"]
    r = q.hold("no-such-seat", "u1", token)
    assert_error(r, 404, "SEAT_NOT_FOUND")
    assert r.headers.get("X-Hold-Gate") == "DB"
