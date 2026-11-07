"""US-03 좌석 하나를 5분 동안 홀드."""
import pytest

from apihelpers import HOLD_TTL, T0, assert_error, parse_dt


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
    api.buy(sid, "u1")  # 확정 요청만으로는 팔리지 않는다: 성공 콜백까지 가야 SOLD
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


# ---------- AC-9 자기 유효 홀드를 다시 홀드 (6장) ----------
@pytest.mark.story("US-03", "AC-9")
def test_us03_ac9_rehold_own_valid_hold_returns_same_hold(api):
    sid = api.sid("A1")
    first = api.hold(sid, "u1")
    assert first.status_code == 201
    api.clock.advance(minutes=2)
    again = api.hold(sid, "u1")
    assert again.status_code == 200, again.text
    h = again.json()
    assert h["seat_id"] == sid and h["status"] == "HELD"
    assert parse_dt(h["held_at"]) == parse_dt(first.json()["held_at"]) == T0
    assert parse_dt(h["expires_at"]) == parse_dt(first.json()["expires_at"]) == T0 + HOLD_TTL
    assert api.status("A1") == "HELD"


@pytest.mark.story("US-03", "AC-9")
def test_us03_ac9_rehold_does_not_extend_expiry(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=2)
    assert api.hold(sid, "u1").status_code == 200  # 재홀드: 연장 아님
    api.clock.advance(minutes=2, seconds=59)  # 처음 홀드 후 4분 59초
    assert_error(api.hold(sid, "u2"), 409, "SEAT_ALREADY_HELD")
    api.clock.advance(seconds=1)  # 처음 홀드 후 5분 정각
    assert api.status("A1") == "AVAILABLE"
    assert api.hold(sid, "u2").status_code == 201


@pytest.mark.story("US-03", "AC-9")
def test_us03_ac9_rehold_repeated_never_changes_times(api):
    sid = api.sid("A1")
    first = api.hold(sid, "u1").json()
    for _ in range(3):
        api.clock.advance(seconds=30)
        r = api.hold(sid, "u1")
        assert r.status_code == 200
        assert r.json()["held_at"] == first["held_at"] and r.json()["expires_at"] == first["expires_at"]


@pytest.mark.story("US-03", "AC-9")
def test_us03_ac9_rehold_own_expired_hold_is_a_new_hold(api):
    """자기 홀드라도 만료됐으면 새 홀드(201)이고 시각은 지금 기준이다."""
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=5)  # 정각: 만료
    r = api.hold(sid, "u1")
    assert r.status_code == 201, r.text
    assert parse_dt(r.json()["held_at"]) == api.clock.now
    assert parse_dt(r.json()["expires_at"]) == api.clock.now + HOLD_TTL


@pytest.mark.story("US-03", "AC-9")
def test_us03_ac9_rehold_with_pending_booking_returns_same_hold(api):
    sid = api.sid("A1")
    b = api.pending(sid, "u1")
    api.clock.advance(minutes=1)
    r = api.hold(sid, "u1")
    assert r.status_code == 200, r.text
    assert parse_dt(r.json()["expires_at"]) == parse_dt(b["expires_at"])


@pytest.mark.story("US-03", "AC-9")
def test_us03_ac9_other_user_still_rejected_on_valid_hold(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    assert_error(api.hold(sid, "u2"), 409, "SEAT_ALREADY_HELD")
