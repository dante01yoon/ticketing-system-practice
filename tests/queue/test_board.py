"""docs/queue.md ⑥ 3: 전광판 전에는 입장권이 없고, 시계를 간격만큼 옮기면 다음 묶음이 입장한다.
가짜 시계 인프로세스 앱, QUEUE_BATCH=3 · 간격 10초 · 입장권 120초."""
from datetime import timedelta

import pytest

from apihelpers import assert_error
from queuelib import T0, parse_dt


def _join_7(q):
    return [q.join(f"u{i}") for i in range(1, 8)]


@pytest.mark.story("US-08", "전광판")
def test_first_batch_admitted_immediately_rest_waiting_with_ahead_and_retry_after(q):
    rs = _join_7(q)
    assert [r.status_code for r in rs] == [201] * 7, [r.text for r in rs]
    bodies = [r.json() for r in rs]
    assert [b["number"] for b in bodies] == list(range(1, 8))
    for b in bodies[:3]:  # 처음 BATCH(3)명은 바로 입장
        assert (b["status"], b["serving"], b["ahead"], b["retry_after"]) == ("ADMITTED", 3, 0, None), b
        assert isinstance(b["entry_token"], str) and b["entry_token"]
        assert parse_dt(b["entry_expires_at"]) == T0 + timedelta(seconds=120)
        assert b["concert_id"] == q.concert_id
    for r in rs[:3]:
        assert "retry-after" not in r.headers
    for b, r, ahead in zip(bodies[3:], rs[3:], [0, 1, 2, 3]):  # 4번은 전광판 바로 다음
        assert (b["status"], b["serving"], b["ahead"], b["retry_after"]) == ("WAITING", 3, ahead, 10), b
        assert b["entry_token"] is None and b["entry_expires_at"] is None
        assert r.headers.get("Retry-After") == "10"


@pytest.mark.story("US-08", "전광판")
def test_retry_after_counts_down_and_rounds_up_to_whole_seconds(q):
    _join_7(q)
    q.clock.advance(seconds=3.5)  # 다음 상승까지 6.5초 -> 올림 7
    r = q.me("u4")
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["status"], b["serving"], b["retry_after"]) == ("WAITING", 3, 7), b
    assert r.headers.get("Retry-After") == "7"
    q.clock.advance(seconds=5.5)  # T0+9초: 1초 남음
    r = q.me("u7")
    assert r.json()["retry_after"] == 1 and r.headers.get("Retry-After") == "1", r.text
    assert r.json()["ahead"] == 3


@pytest.mark.story("US-08", "전광판")
def test_clock_by_one_interval_admits_next_batch_only(q):
    _join_7(q)
    q.clock.advance(seconds=9)
    assert q.me("u4").json()["status"] == "WAITING"  # 간격 전에는 그대로
    q.clock.advance(seconds=1)  # 정확히 10초
    got = {u: q.me(u).json() for u in ["u3", "u4", "u5", "u6", "u7"]}
    assert [got[u]["status"] for u in ["u3", "u4", "u5", "u6", "u7"]] == ["ADMITTED"] * 4 + ["WAITING"], got
    assert all(g["serving"] == 6 for g in got.values())
    assert got["u7"]["ahead"] == 0 and got["u7"]["retry_after"] == 10
    for u in ["u4", "u5", "u6"]:
        assert got[u]["entry_token"] and parse_dt(got[u]["entry_expires_at"]) == T0 + timedelta(seconds=130)


@pytest.mark.story("US-08", "전광판")
def test_entry_expiry_is_fixed_at_first_admission_and_not_extended(q):
    _join_7(q)
    q.clock.advance(seconds=15)  # u4 는 이때 처음 입장으로 판단된다
    first = q.me("u4").json()
    assert first["status"] == "ADMITTED"
    assert parse_dt(first["entry_expires_at"]) == T0 + timedelta(seconds=15 + 120)
    q.clock.advance(seconds=5)
    again = q.me("u4").json()
    assert again["entry_token"] == first["entry_token"] and again["entry_expires_at"] == first["entry_expires_at"]


@pytest.mark.story("US-08", "같은 사용자")
def test_same_user_again_gets_same_number_200_with_same_entry(q):
    first = q.join("u1")
    assert first.status_code == 201
    q.clock.advance(seconds=50)
    again = q.join("u1")
    assert again.status_code == 200
    assert again.json()["number"] == first.json()["number"] == 1
    assert again.json()["entry_token"] == first.json()["entry_token"]
    assert again.json()["entry_expires_at"] == first.json()["entry_expires_at"]
    assert q.join("u2").json()["number"] == 2  # 번호가 빠지지 않는다


@pytest.mark.story("US-08", "입장권 검사")
def test_waiting_user_without_entry_token_gets_403_required_then_in_after_next_batch(q):
    _join_7(q)
    u1 = q.me("u1").json()["entry_token"]
    a5 = q.seat_id("A5")
    # 전광판 밖(WAITING) u5: 입장권 없음 / 빈 값 -> 403 REQUIRED, 좌석 무변화
    for token in (None, ""):
        assert_error(q.seatmap("u5", token), 403, "ENTRY_TOKEN_REQUIRED")
        assert_error(q.hold(a5, "u5", token), 403, "ENTRY_TOKEN_REQUIRED")
    assert q.seat_raw_status(a5) == "AVAILABLE"
    # 입장한 u1 은 입장권으로 통과
    assert q.seatmap("u1", u1).status_code == 200
    assert q.hold(q.seat_id("A1"), "u1", u1).status_code == 201
    # 시계를 간격만큼 옮기면 u5 도 입장
    q.clock.advance(seconds=10)
    b = q.me("u5").json()
    assert b["status"] == "ADMITTED" and b["entry_token"], b
    assert q.seatmap("u5", b["entry_token"]).status_code == 200
    assert q.hold(a5, "u5", b["entry_token"]).status_code == 201


@pytest.mark.story("US-08", "대기열 기본")
def test_queue_endpoints_validate_user_and_concert(q):
    assert_error(q.join(None), 401, "USER_ID_REQUIRED")
    assert_error(q.me(None), 401, "USER_ID_REQUIRED")
    assert_error(q.join("\x00"), 401, "USER_ID_REQUIRED")
    assert_error(q.join("u1", "no-such-concert"), 404, "CONCERT_NOT_FOUND")
    assert_error(q.join("u1", "abc%00def"), 404, "CONCERT_NOT_FOUND")
    assert_error(q.me("u1"), 404, "QUEUE_TICKET_NOT_FOUND")  # 아직 번호가 없다
    assert_error(q.me("u1", "no-such-concert"), 404, "QUEUE_TICKET_NOT_FOUND")
    q.join("u1")
    assert q.me("u1").status_code == 200
    assert_error(q.me("u2"), 404, "QUEUE_TICKET_NOT_FOUND")  # 남의 번호는 내 것이 아니다


# ---------- 전광판 경계 (docs/queue.md ②) ----------
def _join_n(q, n):
    for i in range(1, n + 1):
        assert q.join(f"u{i}").status_code == 201


@pytest.mark.story("US-08", "전광판")
def test_catches_up_several_ticks_at_once_and_keeps_the_remainder(q):
    _join_n(q, 12)  # serving 3, anchor T0
    q.clock.advance(seconds=25)  # 2.5 간격: 2틱(+6), anchor 는 T0+20 으로 나머지 5초를 남긴다
    b9, b10 = q.me("u9"), q.me("u10")
    assert b9.json()["status"] == "ADMITTED" and b9.json()["serving"] == 9, b9.text
    assert (b10.json()["status"], b10.json()["serving"], b10.json()["ahead"]) == ("WAITING", 9, 0), b10.text
    assert b10.json()["retry_after"] == 5 and b10.headers.get("Retry-After") == "5"  # 다음 상승(T0+30)까지 5초
    q.clock.advance(seconds=5)  # T0+30: 한 틱 더
    assert q.me("u12").json()["status"] == "ADMITTED"
    assert q.me("u12").json()["serving"] == 12


@pytest.mark.story("US-08", "전광판")
def test_long_idle_opens_at_most_one_batch_beyond_issued(q):
    _join_n(q, 3)  # issued 3, serving 3
    q.clock.advance(seconds=1005)  # 100틱을 쉬었다: 쌓이는 여유는 issued + BATCH 까지만
    assert q.me("u1").json()["serving"] == 6
    nums = {}
    for i in range(4, 10):  # 한꺼번에 몰려도 한 묶음만 들어간다
        r = q.join(f"u{i}")
        assert r.status_code == 201
        nums[i] = r.json()
    assert [nums[i]["number"] for i in range(4, 10)] == [4, 5, 6, 7, 8, 9]
    assert [nums[i]["status"] for i in range(4, 10)] == ["ADMITTED"] * 3 + ["WAITING"] * 3
    assert nums[7]["retry_after"] == 5  # anchor 는 T0+1000, 다음 상승까지 5초


@pytest.mark.story("US-08", "전광판")
def test_clock_going_backwards_never_lowers_serving_or_changes_issued_entry(q):
    _join_n(q, 7)
    q.clock.advance(seconds=10)
    first = q.me("u4").json()
    assert first["status"] == "ADMITTED" and first["serving"] == 6
    q.clock.now = T0 + timedelta(seconds=5)  # 시계를 뒤로
    back = q.me("u4").json()
    assert back["serving"] == 6, "serving 은 줄지 않는다"
    assert back["status"] == "ADMITTED"
    assert back["entry_token"] == first["entry_token"] and back["entry_expires_at"] == first["entry_expires_at"]
    assert q.me("u7").json()["status"] == "WAITING"
    q.clock.now = T0 + timedelta(seconds=20)  # 다시 앞으로: anchor(T0+10) 기준으로 한 틱
    assert q.me("u7").json()["status"] == "ADMITTED"
