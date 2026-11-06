"""3장 리뷰 지적 반영: 잠금 대기 중 만료, 공연 2개, 422 VALIDATION_ERROR."""
from datetime import timedelta

import pytest

from apihelpers import HOLD_TTL, T0, assert_error, parse_dt, request_blocked_by_row_lock


# ---------- 1. 시각은 좌석 잠금을 얻은 뒤에 읽는다 ----------
@pytest.mark.story("US-04", "AC-2")
def test_us04_ac2_hold_expires_while_waiting_for_row_lock(api):
    sid = api.sid("A1")
    assert api.hold(sid, "u1").status_code == 201  # T0
    resp = request_blocked_by_row_lock(
        api,
        sid,
        send=lambda c: api.confirm(sid, "u1", client=c),
        clock_at_send=T0 + timedelta(minutes=4, seconds=59),  # 요청 시작 시점엔 유효
        clock_after_wait=T0 + timedelta(minutes=5, seconds=1),  # 잠금을 기다리는 사이 만료
    )
    assert_error(resp, 409, "HOLD_EXPIRED")
    assert api.status("A1") != "SOLD"
    assert api.status("A1") == "AVAILABLE"


@pytest.mark.story("US-03", "AC-1")
def test_us03_ac1_hold_time_is_read_after_row_lock(api):
    sid = api.sid("A1")
    resp = request_blocked_by_row_lock(
        api,
        sid,
        send=lambda c: api.hold(sid, "u1", client=c),
        clock_at_send=T0,
        clock_after_wait=T0 + timedelta(minutes=1),
    )
    assert resp.status_code == 201, resp.text
    h = resp.json()
    assert parse_dt(h["held_at"]) == T0 + timedelta(minutes=1)
    assert parse_dt(h["expires_at"]) == T0 + timedelta(minutes=1) + HOLD_TTL  # T0+6분
    assert api.status("A1") == "HELD"


# ---------- 2. 공연 2개 ----------
@pytest.mark.story("US-01", "AC-1")
def test_us01_ac1_two_concerts_both_listed(api):
    from backend.app.seed import add_concert  # 함수 안 import: 없어도 다른 테스트에 번지지 않게

    second_start = T0 + timedelta(days=30)
    second_id = add_concert("두 번째 공연", second_start, seats=3)
    r = api.concerts()
    assert r.status_code == 200, r.text
    items = {c["id"]: c for c in r.json()["items"]}
    assert api.concert_id in items and second_id in items, f"두 공연이 모두 있어야 함: {list(items)}"
    assert len(items) == 2
    assert items[second_id]["title"] == "두 번째 공연"
    assert parse_dt(items[second_id]["starts_at"]) == second_start
    # 새 공연의 좌석맵: 좌석 3개 A1~A3, 모두 AVAILABLE
    seats = api.seatmap(second_id).json()["seats"]
    assert sorted(s["number"] for s in seats) == ["A1", "A2", "A3"]
    assert {s["status"] for s in seats} == {"AVAILABLE"}


# ---------- 3. 422 VALIDATION_ERROR (현재 동작 고정) ----------
BAD_BODIES = [
    pytest.param({"json": {}}, id="empty_object"),
    pytest.param({"json": {"seat_id": None}}, id="seat_id_null"),
    pytest.param({"json": {"seat_id": 5}}, id="seat_id_number"),
    pytest.param({"content": "이건 JSON이 아님", "headers": {"Content-Type": "application/json"}}, id="not_json"),
]


@pytest.mark.story("US-04", "VALIDATION")
@pytest.mark.parametrize("kwargs", BAD_BODIES)
def test_us04_confirm_malformed_body_is_422_validation_error(api, kwargs):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    headers = {"X-User-Id": "u1", **kwargs.get("headers", {})}
    send = {k: v for k, v in kwargs.items() if k != "headers"}
    r = api.client.post("/bookings", headers=headers, **send)
    assert_error(r, 422, "VALIDATION_ERROR")
    assert api.status("A1") == "HELD"  # 잘못된 요청은 상태를 바꾸지 않는다


# ---------- 4. 좌석 261개: 열이 Z 를 넘어 AA 로 (재검토 지적) ----------
@pytest.mark.story("US-02", "AC-1")
def test_us02_ac1_large_concert_seatmap_orders_rows_a_to_z_then_aa(api):
    from starlette.testclient import TestClient

    from backend.app.seed import add_concert

    cid = add_concert("큰 공연", T0, seats=261)
    # 서버 예외를 테스트 예외가 아니라 500 응답으로 받아 상태 코드로 판단한다
    r = TestClient(api.app, raise_server_exceptions=False).get(f"/concerts/{cid}/seats")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    numbers = [s["number"] for s in r.json()["seats"]]
    assert len(numbers) == 261
    assert "AA1" in numbers
    rows = [chr(ord("A") + i) for i in range(26)] + ["AA"]
    expected = [f"{row}{n}" for row in rows for n in range(1, 11)][:261]
    assert numbers == expected, "열 순서 A..Z, AA.. / 열 안에서는 1..10 숫자 순이어야 함"
    assert numbers.index("AA1") == numbers.index("Z10") + 1
