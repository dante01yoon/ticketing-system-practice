"""US-03 좌석 하나를 5분 동안 홀드."""
import pytest

from apihelpers import HOLD_TTL, assert_error, parse_dt


@pytest.mark.story("US-03", "AC-1")
def test_us03_ac1_hold_available_seat(api):
    sid = api.sid("A1")
    r = api.hold(sid, "u1")
    assert r.status_code == 201, r.text
    h = r.json()
    assert h["seat_id"] == sid
    assert h["status"] == "HELD"
    assert parse_dt(h["held_at"]) == api.clock.now
    assert parse_dt(h["expires_at"]) == api.clock.now + HOLD_TTL
    assert api.status("A1") == "HELD"
    # u1 의 HELD: 다른 사용자는 못 잡고, u1 은 확정할 수 있다
    assert_error(api.hold(sid, "u2"), 409, "SEAT_ALREADY_HELD")
    assert api.confirm(sid, "u1").status_code == 201


@pytest.mark.story("US-03", "AC-2")
def test_us03_ac2_other_users_valid_hold_rejected(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=2)
    assert_error(api.hold(sid, "u2"), 409, "SEAT_ALREADY_HELD")
    assert api.status("A1") == "HELD"
    r = api.confirm(sid, "u1")  # 여전히 u1 의 홀드
    assert r.status_code == 201 and r.json()["user_id"] == "u1"


@pytest.mark.story("US-03", "AC-3")
def test_us03_ac3_expired_hold_can_be_taken(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=5, seconds=1)
    r = api.hold(sid, "u2")
    assert r.status_code == 201, r.text
    assert parse_dt(r.json()["expires_at"]) == api.clock.now + HOLD_TTL
    assert api.status("A1") == "HELD"
    assert_error(api.confirm(sid, "u1"), 403, "HOLD_NOT_OWNED")  # 이제 u2 의 홀드
    assert api.confirm(sid, "u2").status_code == 201


@pytest.mark.story("US-03", "AC-4")
def test_us03_ac4_boundary_4m59s_still_held(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=4, seconds=59)
    assert_error(api.hold(sid, "u2"), 409, "SEAT_ALREADY_HELD")


@pytest.mark.story("US-03", "AC-4")
def test_us03_ac4_boundary_exactly_5m_can_rehold(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=5)
    assert api.hold(sid, "u2").status_code == 201


@pytest.mark.story("US-03", "AC-5")
def test_us03_ac5_sold_seat_rejected_for_anyone(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    assert api.confirm(sid, "u1").status_code == 201
    assert_error(api.hold(sid, "u2"), 409, "SEAT_ALREADY_SOLD")
    assert_error(api.hold(sid, "u1"), 409, "SEAT_ALREADY_SOLD")
    api.clock.advance(hours=1)
    assert_error(api.hold(sid, "u3"), 409, "SEAT_ALREADY_SOLD")
    assert api.status("A1") == "SOLD"


@pytest.mark.story("US-03", "AC-6")
@pytest.mark.parametrize("user", [None, ""], ids=["no_header", "empty_header"])
def test_us03_ac6_missing_user_id(api, user):
    sid = api.sid("A1")
    assert_error(api.hold(sid, user), 401, "USER_ID_REQUIRED")
    assert api.status("A1") == "AVAILABLE"


@pytest.mark.story("US-03", "AC-7")
def test_us03_ac7_unknown_seat_404(api):
    assert_error(api.hold("no-such-seat", "u1"), 404, "SEAT_NOT_FOUND")


@pytest.mark.story("US-03", "AC-8")
def test_us03_ac8_concurrent_holds_exactly_one_wins(api):
    n = 16
    for number in ["A1", "A2", "A3", "A4", "A5"]:  # 여러 판 반복해 경쟁 조건을 드러낸다
        sid = api.sid(number)
        rs = api.parallel(lambda c, i: api.hold(sid, f"user-{i}", client=c), n)
        wins = [r for r in rs if r.status_code == 201]
        losses = [r for r in rs if r.status_code != 201]
        assert len(wins) == 1, f"{number}: 성공 {len(wins)}건 {[r.status_code for r in rs]}"
        for r in losses:
            assert_error(r, 409, "SEAT_ALREADY_HELD")
        assert api.status(number) == "HELD"
