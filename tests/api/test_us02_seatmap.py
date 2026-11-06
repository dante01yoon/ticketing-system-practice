"""US-02 좌석맵 보기."""
import pytest

from apihelpers import SEAT_NUMBERS, assert_error


def make_sold(api, number, user):
    sid = api.sid(number)
    assert api.hold(sid, user).status_code == 201
    assert api.confirm(sid, user).status_code == 201


@pytest.mark.story("US-02", "AC-1")
def test_us02_ac1_available_held_sold_with_id_and_number(api):
    api.hold(api.sid("A2"), "u1")
    make_sold(api, "A3", "u2")
    r = api.seatmap()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["concert_id"] == api.concert_id
    seats = {s["number"]: s for s in body["seats"]}
    assert sorted(seats) == sorted(SEAT_NUMBERS), "시드 좌석 A1~J10 100석이어야 합니다"
    assert all(isinstance(s["id"], str) and s["id"] for s in seats.values())
    assert seats["A1"]["status"] == "AVAILABLE"
    assert seats["A2"]["status"] == "HELD"
    assert seats["A3"]["status"] == "SOLD"


@pytest.mark.story("US-02", "AC-2")
def test_us02_ac2_expired_hold_shows_available(api):
    api.hold(api.sid("A2"), "u1")
    api.clock.advance(minutes=6)
    assert api.status("A2") == "AVAILABLE"


@pytest.mark.story("US-02", "AC-2")
def test_us02_ac2_hold_still_valid_before_expiry_and_expired_at_exact_5min(api):
    api.hold(api.sid("A2"), "u1")
    api.clock.advance(minutes=4, seconds=59)
    assert api.status("A2") == "HELD"
    api.clock.advance(seconds=1)  # 정확히 5분: get_now >= held_at + 5분 이면 만료
    assert api.status("A2") == "AVAILABLE"


@pytest.mark.story("US-02", "AC-3")
def test_us02_ac3_unknown_concert_404(api):
    r = api.seatmap("no-such-concert")
    assert_error(r, 404, "CONCERT_NOT_FOUND")


@pytest.mark.story("US-02", "AC-4")
def test_us02_ac4_seatmap_does_not_expose_holder(api):
    holder = "secret-holder-u1"
    api.hold(api.sid("A2"), holder)
    r = api.seatmap(user="other-user")
    assert r.status_code == 200
    assert holder not in r.text, "좌석맵에 홀드한 사용자 ID 가 노출됨"
    for s in r.json()["seats"]:
        assert set(s) <= {"id", "number", "status"}, f"예상 밖 좌석 필드: {set(s)}"
