"""NUL 문자(U+0000)가 든 ID 입력: 500 이 아니라 정해진 클라이언트 오류 (docs/openapi.yaml).

- 본문(seat_id, booking_id)에 NUL → 422 VALIDATION_ERROR, 상태 변화 없음
- 경로 ID 에 NUL(%00) → 없는 ID 와 똑같은 404 응답
서버 예외는 테스트 예외가 아니라 500 응답으로 받아 상태 코드로 판단한다.
"""
import pytest
from starlette.testclient import TestClient

from apihelpers import assert_error

NUL = "\x00"


def _client(api):
    return TestClient(api.app, raise_server_exceptions=False)


def _no_side_effects(api):
    assert api.status("A1") == "HELD"
    assert api.booking_statuses(api.sid("A1")) == []


# ---------- 1. POST /bookings: 본문 seat_id ----------
@pytest.mark.story("US-04", "VALIDATION")
@pytest.mark.parametrize("seat_id", [NUL, f"{NUL}abc", f"abc{NUL}", f"abc{NUL}def"], ids=["only", "prefix", "suffix", "middle"])
def test_us04_confirm_seat_id_with_nul_is_422_and_changes_nothing(api, seat_id):
    api.hold(api.sid("A1"), "u1")
    r = _client(api).post("/bookings", json={"seat_id": seat_id}, headers={"X-User-Id": "u1", "Idempotency-Key": "k-nul"})
    assert_error(r, 422, "VALIDATION_ERROR")
    _no_side_effects(api)
    # 멱등 키 기록도 남지 않아 같은 키로 정상 요청이 성공한다
    ok = api.client.post("/bookings", json={"seat_id": api.sid("A1")}, headers={"X-User-Id": "u1", "Idempotency-Key": "k-nul"})
    assert ok.status_code == 201, ok.text


@pytest.mark.story("US-04", "VALIDATION")
def test_us04_confirm_nul_seat_id_order_user_before_body(api):
    r = _client(api).post("/bookings", json={"seat_id": NUL})
    assert_error(r, 401, "USER_ID_REQUIRED")


@pytest.mark.story("US-04", "VALIDATION")
def test_us04_confirm_nul_seat_id_order_idempotency_key_before_body(api):
    r = _client(api).post("/bookings", json={"seat_id": NUL}, headers={"X-User-Id": "u1"})
    assert_error(r, 400, "IDEMPOTENCY_KEY_REQUIRED")


# ---------- 2. POST /payments/callback: 본문 booking_id ----------
@pytest.mark.story("US-05", "AC-8")
@pytest.mark.parametrize("suffix", ["", "-x"], ids=["only", "with_text"])
@pytest.mark.parametrize("result", ["SUCCEEDED", "FAILED"])
def test_us05_ac8_callback_booking_id_with_nul_is_422_and_changes_nothing(api, suffix, result):
    api.hold(api.sid("A1"), "u1")
    b = api.pending(api.sid("A2"), "u1")
    r = _client(api).post("/payments/callback", json={"booking_id": NUL + suffix, "result": result})
    assert_error(r, 422, "VALIDATION_ERROR")
    assert api.get_booking(b["id"], "u1").json()["status"] == "PENDING"
    assert api.status("A2") == "HELD"
    assert api.booking_statuses(api.sid("A2")) == ["PENDING"]
    assert api.status("A1") == "HELD"


# ---------- 3. 경로 ID 의 NUL(%00) = 없는 ID 와 똑같은 응답 ----------
def _same_as_unknown(api, send, status, code):
    c = _client(api)
    unknown = send(c, "no-such-id")
    assert_error(unknown, status, code)
    for bad in ["%00", "abc%00", "%00abc", "ab%00cd"]:
        r = send(c, bad)
        assert (r.status_code, r.json()) == (unknown.status_code, unknown.json()), f"{bad}: {r.status_code} {r.text}"


@pytest.mark.story("US-02", "AC-4")
def test_us02_seatmap_path_id_with_nul_is_same_404_as_unknown(api):
    _same_as_unknown(api, lambda c, i: c.get(f"/concerts/{i}/seats", headers={"X-User-Id": "u1"}), 404, "CONCERT_NOT_FOUND")


@pytest.mark.story("US-03", "AC-6")
def test_us03_hold_path_id_with_nul_is_same_404_as_unknown_and_changes_nothing(api):
    _same_as_unknown(api, lambda c, i: c.post(f"/seats/{i}/hold", headers=api.entry_headers("u1")), 404, "SEAT_NOT_FOUND")
    assert api.status("A1") == "AVAILABLE"


@pytest.mark.story("US-05", "AC-9")
def test_us05_ac9_booking_path_id_with_nul_is_same_404_as_unknown(api):
    _same_as_unknown(api, lambda c, i: c.get(f"/bookings/{i}", headers={"X-User-Id": "u1"}), 404, "BOOKING_NOT_FOUND")
