"""헤더의 NUL 문자(U+0000): DB 에 닿기 전에 걸러 500 대신 정해진 4xx 로 응답 (docs/openapi.yaml).

- X-User-Id 에 NUL → 401 USER_ID_REQUIRED (POST /seats/{id}/hold, POST /bookings, GET /bookings/{id})
- Idempotency-Key 에 NUL → 400 IDEMPOTENCY_KEY_INVALID (POST /bookings)
- 판단 순서: X-User-Id → Idempotency-Key → 본문
- 거부된 요청은 좌석·예매·멱등 기록을 바꾸지 않는다 (같은 키를 NUL 없이 다시 쓰면 정상 처리)
서버 예외는 테스트 예외가 아니라 500 응답으로 받아 상태 코드로 판단한다.
"""
import pytest
from starlette.testclient import TestClient

from apihelpers import assert_error

NUL = "\x00"
POSITIONS = [NUL, f"{NUL}abc", f"abc{NUL}", f"abc{NUL}def"]
POS_IDS = ["only", "prefix", "suffix", "middle"]


def _client(api):
    return TestClient(api.app, raise_server_exceptions=False)


# ---------- 1. X-User-Id 의 NUL → 401 ----------
@pytest.mark.story("US-03", "HEADER_NUL")
@pytest.mark.parametrize("user", POSITIONS, ids=POS_IDS)
def test_us03_hold_user_id_with_nul_is_401_and_changes_nothing(api, user):
    r = _client(api).post(f"/seats/{api.sid('A1')}/hold", headers={"X-User-Id": user})
    assert_error(r, 401, "USER_ID_REQUIRED")
    assert api.status("A1") == "AVAILABLE"
    # NUL 없는 같은 사용자 ID 로는 정상 홀드
    assert api.hold(api.sid("A1"), "abc").status_code == 201


@pytest.mark.story("US-04", "HEADER_NUL")
@pytest.mark.parametrize("user", POSITIONS, ids=POS_IDS)
def test_us04_confirm_user_id_with_nul_is_401_and_changes_nothing(api, user):
    api.hold(api.sid("A1"), "abc")
    r = _client(api).post(
        "/bookings", json={"seat_id": api.sid("A1")}, headers={"X-User-Id": user, "Idempotency-Key": "k-nul-user"}
    )
    assert_error(r, 401, "USER_ID_REQUIRED")
    assert api.status("A1") == "HELD"
    assert api.booking_statuses(api.sid("A1")) == []
    ok = api.client.post(
        "/bookings", json={"seat_id": api.sid("A1")}, headers={"X-User-Id": "abc", "Idempotency-Key": "k-nul-user"}
    )
    assert ok.status_code == 201, ok.text


@pytest.mark.story("US-05", "HEADER_NUL")
@pytest.mark.parametrize("user", POSITIONS, ids=POS_IDS)
def test_us05_get_booking_user_id_with_nul_is_401(api, user):
    b = api.pending(api.sid("A1"), "abc")
    r = _client(api).get(f"/bookings/{b['id']}", headers={"X-User-Id": user})
    assert_error(r, 401, "USER_ID_REQUIRED")
    assert api.get_booking(b["id"], "abc").json()["status"] == "PENDING"


# ---------- 2. Idempotency-Key 의 NUL → 400 ----------
@pytest.mark.story("US-04", "HEADER_NUL")
@pytest.mark.parametrize("key", POSITIONS, ids=POS_IDS)
def test_us04_confirm_idempotency_key_with_nul_is_400_and_changes_nothing(api, key):
    api.hold(api.sid("A1"), "u1")
    r = _client(api).post(
        "/bookings", json={"seat_id": api.sid("A1")}, headers={"X-User-Id": "u1", "Idempotency-Key": key}
    )
    assert_error(r, 400, "IDEMPOTENCY_KEY_INVALID")
    assert api.status("A1") == "HELD"
    assert api.booking_statuses(api.sid("A1")) == []
    # NUL 을 뺀 같은 키로 정상 처리
    clean = key.replace(NUL, "") or "k"
    ok = api.client.post(
        "/bookings", json={"seat_id": api.sid("A1")}, headers={"X-User-Id": "u1", "Idempotency-Key": clean}
    )
    assert ok.status_code == 201, ok.text
    assert api.booking_statuses(api.sid("A1")) == ["PENDING"]


# ---------- 3. 판단 순서: X-User-Id → Idempotency-Key → 본문 ----------
@pytest.mark.story("US-04", "HEADER_NUL")
def test_us04_confirm_order_user_nul_before_key_nul(api):
    r = _client(api).post(
        "/bookings", json={"seat_id": api.sid("A1")}, headers={"X-User-Id": f"u{NUL}", "Idempotency-Key": f"k{NUL}"}
    )
    assert_error(r, 401, "USER_ID_REQUIRED")


@pytest.mark.story("US-04", "HEADER_NUL")
def test_us04_confirm_order_user_nul_before_missing_key(api):
    r = _client(api).post("/bookings", json={"seat_id": api.sid("A1")}, headers={"X-User-Id": f"u{NUL}"})
    assert_error(r, 401, "USER_ID_REQUIRED")


@pytest.mark.story("US-04", "HEADER_NUL")
def test_us04_confirm_order_key_nul_before_body_nul(api):
    r = _client(api).post(
        "/bookings", json={"seat_id": f"a{NUL}"}, headers={"X-User-Id": "u1", "Idempotency-Key": f"k{NUL}"}
    )
    assert_error(r, 400, "IDEMPOTENCY_KEY_INVALID")


@pytest.mark.story("US-04", "HEADER_NUL")
def test_us04_confirm_order_key_nul_before_malformed_body(api):
    r = _client(api).post(
        "/bookings", content=b"not json", headers={"X-User-Id": "u1", "Idempotency-Key": f"k{NUL}"}
    )
    assert_error(r, 400, "IDEMPOTENCY_KEY_INVALID")
