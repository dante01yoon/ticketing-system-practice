"""US-04 모의 결제로 예매 확정."""
import pytest

from apihelpers import assert_error, parse_dt


@pytest.mark.story("US-04", "AC-1")
def test_us04_ac1_confirm_own_valid_hold(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=3)
    r = api.confirm(sid, "u1")
    assert r.status_code == 201, r.text
    b = r.json()
    assert isinstance(b["id"], str) and b["id"]
    assert b["seat_id"] == sid
    assert b["concert_id"] == api.concert_id
    assert b["user_id"] == "u1"
    parse_dt(b["created_at"])
    assert api.status("A1") == "SOLD"


@pytest.mark.story("US-04", "AC-2")
def test_us04_ac2_expired_hold_rejected(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=5, seconds=1)
    assert_error(api.confirm(sid, "u1"), 409, "HOLD_EXPIRED")
    assert api.status("A1") != "SOLD"
    assert api.status("A1") == "AVAILABLE"


@pytest.mark.story("US-04", "AC-2")
def test_us04_ac2_boundary_exactly_5m_is_expired(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=5)
    assert_error(api.confirm(sid, "u1"), 409, "HOLD_EXPIRED")


@pytest.mark.story("US-04", "AC-2")
def test_us04_ac2_boundary_4m59s_still_confirmable(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=4, seconds=59)
    assert api.confirm(sid, "u1").status_code == 201


@pytest.mark.story("US-04", "AC-3")
def test_us04_ac3_not_my_hold(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=1)
    assert_error(api.confirm(sid, "u2"), 403, "HOLD_NOT_OWNED")
    assert api.status("A1") == "HELD"
    assert api.confirm(sid, "u1").status_code == 201  # u1 의 HELD 로 남아 있었다


@pytest.mark.story("US-04", "AC-4")
def test_us04_ac4_confirm_without_hold(api):
    assert_error(api.confirm(api.sid("A1"), "u1"), 409, "HOLD_REQUIRED")
    assert api.status("A1") == "AVAILABLE"


@pytest.mark.story("US-04", "AC-5")
def test_us04_ac5_sold_seat_cannot_be_held_again(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    assert api.confirm(sid, "u1").status_code == 201
    assert_error(api.hold(sid, "u2"), 409, "SEAT_ALREADY_SOLD")
    assert api.status("A1") == "SOLD"


@pytest.mark.story("US-04", "AC-6")
@pytest.mark.parametrize("user", [None, ""], ids=["no_header", "empty_header"])
def test_us04_ac6_missing_user_id(api, user):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    assert_error(api.confirm(sid, user), 401, "USER_ID_REQUIRED")
    assert api.status("A1") == "HELD"
    assert api.confirm(sid, "u1").status_code == 201  # u1 의 HELD 로 남아 있었다


@pytest.mark.story("US-04", "AC-7")
def test_us04_ac7_concurrent_double_confirm_sells_once(api):
    for number in ["A1", "A2", "A3", "A4", "A5"]:
        sid = api.sid(number)
        assert api.hold(sid, "u1").status_code == 201
        rs = api.parallel(lambda c, i: api.confirm(sid, "u1", client=c), 8)
        wins = [r for r in rs if r.status_code == 201]
        assert len(wins) == 1, f"{number}: 예매 {len(wins)}건 {[r.status_code for r in rs]}"
        for r in rs:
            if r.status_code != 201:
                assert_error(r, 409, "SEAT_ALREADY_SOLD")
        assert api.status(number) == "SOLD"
        # 한 좌석에 예매는 하나: 이후 확정 시도는 모두 SEAT_ALREADY_SOLD
        assert_error(api.confirm(sid, "u1"), 409, "SEAT_ALREADY_SOLD")


@pytest.mark.story("US-04", "404")
def test_us04_confirm_unknown_seat_404(api):
    assert_error(api.confirm("no-such-seat", "u1"), 404, "SEAT_NOT_FOUND")
