"""US-04 예매 확정 요청 (결제 대기 예매 만들기). 확정 요청은 좌석을 팔지 않는다 — 판매는 US-05 성공 콜백."""
import json
from datetime import timedelta

import pytest

from apihelpers import HOLD_TTL, T0, assert_error, parse_dt


# ---------- AC-1 ----------
@pytest.mark.story("US-04", "AC-1")
def test_us04_ac1_confirm_creates_pending_booking_without_selling(api):
    sid = api.sid("A1")
    hold = api.hold(sid, "u1").json()
    api.clock.advance(minutes=3)
    r = api.confirm(sid, "u1", key="k1")
    assert r.status_code == 201, r.text
    b = r.json()
    assert isinstance(b["id"], str) and b["id"]
    assert b["seat_id"] == sid
    assert b["concert_id"] == api.concert_id
    assert b["user_id"] == "u1"
    assert b["status"] == "PENDING"
    assert parse_dt(b["created_at"]) == api.clock.now  # 서버 시각
    assert parse_dt(b["expires_at"]) == parse_dt(hold["expires_at"]) == T0 + HOLD_TTL  # 결제 마감 = 홀드 만료
    assert api.status("A1") == "HELD"  # 아직 SOLD 아님
    assert api.booking_statuses(sid) == ["PENDING"]


@pytest.mark.story("US-04", "AC-1")
def test_us04_ac1_pending_booking_is_readable_by_owner(api):
    b = api.pending(api.sid("A1"), "u1")
    r = api.get_booking(b["id"], "u1")
    assert r.status_code == 200, r.text
    assert r.json() == b


# ---------- AC-2 만료 경계 ----------
@pytest.mark.story("US-04", "AC-2")
def test_us04_ac2_boundary_4m59s_still_confirmable(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=4, seconds=59)
    r = api.confirm(sid, "u1")
    assert r.status_code == 201, r.text
    assert parse_dt(r.json()["expires_at"]) == T0 + HOLD_TTL  # 남은 1초 — 마감은 늘지 않는다


@pytest.mark.story("US-04", "AC-2")
@pytest.mark.parametrize(
    "delta", [timedelta(minutes=5), timedelta(minutes=5, seconds=1)], ids=["exactly_5m", "5m1s"]
)
def test_us04_ac2_expired_hold_rejected_and_no_booking(api, delta):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.now = T0 + delta
    assert_error(api.confirm(sid, "u1"), 409, "HOLD_EXPIRED")
    assert api.booking_statuses(sid) == []
    assert api.status("A1") == "AVAILABLE"


# ---------- AC-3 남의 홀드 ----------
@pytest.mark.story("US-04", "AC-3")
def test_us04_ac3_not_my_hold_403_no_booking_and_hold_kept(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    api.clock.advance(minutes=1)
    assert_error(api.confirm(sid, "u2"), 403, "HOLD_NOT_OWNED")
    assert api.booking_statuses(sid) == []
    assert api.status("A1") == "HELD"
    r = api.confirm(sid, "u1")  # u1 의 홀드는 그대로
    assert r.status_code == 201 and r.json()["user_id"] == "u1"


@pytest.mark.story("US-04", "AC-3")
def test_us04_ac3_others_pending_booking_does_not_leak_to_other_user(api):
    sid = api.sid("A1")
    b = api.pending(sid, "u1")
    assert_error(api.confirm(sid, "u2"), 403, "HOLD_NOT_OWNED")  # u1 의 PENDING 예매를 돌려주지 않는다
    assert api.booking_statuses(sid) == ["PENDING"]
    assert api.get_booking(b["id"], "u1").json()["status"] == "PENDING"


# ---------- AC-4 ----------
@pytest.mark.story("US-04", "AC-4")
def test_us04_ac4_confirm_without_hold(api):
    sid = api.sid("A1")
    assert_error(api.confirm(sid, "u1"), 409, "HOLD_REQUIRED")
    assert api.status("A1") == "AVAILABLE"
    assert api.booking_statuses(sid) == []


# ---------- AC-5 ----------
@pytest.mark.story("US-04", "AC-5")
def test_us04_ac5_sold_seat_cannot_be_held_or_confirmed_by_others(api):
    sid = api.sid("A1")
    api.buy(sid, "u1")
    assert_error(api.hold(sid, "u2"), 409, "SEAT_ALREADY_SOLD")
    assert_error(api.confirm(sid, "u2"), 409, "SEAT_ALREADY_SOLD")
    assert api.status("A1") == "SOLD"
    assert api.booking_statuses(sid) == ["CONFIRMED"]


# ---------- AC-6 ----------
@pytest.mark.story("US-04", "AC-6")
@pytest.mark.parametrize("user", [None, ""], ids=["no_header", "empty_header"])
def test_us04_ac6_missing_user_id(api, user):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    assert_error(api.confirm(sid, user, key="k1"), 401, "USER_ID_REQUIRED")
    assert api.status("A1") == "HELD"
    assert api.booking_statuses(sid) == []
    assert api.confirm(sid, "u1").status_code == 201  # u1 의 HELD 로 남아 있었다


# ---------- AC-7 동시 확정: 예매 하나 ----------
@pytest.mark.story("US-04", "AC-7")
def test_us04_ac7_concurrent_confirms_with_different_keys_make_one_booking(api):
    for number in ["A1", "A2", "A3", "A4", "A5"]:
        sid = api.sid(number)
        assert api.hold(sid, "u1").status_code == 201
        rs = api.parallel(lambda c, i: api.confirm(sid, "u1", client=c, key=f"{number}-k{i}"), 8)
        codes = sorted(r.status_code for r in rs)
        assert codes == [200] * 7 + [201], f"{number}: {codes}"
        ids = {r.json()["id"] for r in rs}
        assert len(ids) == 1, f"{number}: 예매 ID 가 여럿: {ids}"
        assert {r.json()["status"] for r in rs} == {"PENDING"}
        assert api.booking_statuses(sid) == ["PENDING"]
        assert api.status(number) == "HELD"  # 확정 요청은 팔지 않는다


# ---------- AC-8 멱등 키 없음 ----------
@pytest.mark.story("US-04", "AC-8")
@pytest.mark.parametrize("key", [None, ""], ids=["no_header", "empty_header"])
def test_us04_ac8_missing_idempotency_key(api, key):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    assert_error(api.confirm(sid, "u1", key=key), 400, "IDEMPOTENCY_KEY_REQUIRED")
    assert api.booking_statuses(sid) == []
    assert api.status("A1") == "HELD"


@pytest.mark.story("US-04", "AC-8")
def test_us04_ac8_idempotency_key_255_chars_is_allowed(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    key = "k" * 255
    r = api.confirm(sid, "u1", key=key)
    assert r.status_code == 201, r.text
    again = api.confirm(sid, "u1", key=key)  # 긴 키도 멱등 재요청이 된다
    assert (again.status_code, again.json()) == (201, r.json())
    assert api.booking_statuses(sid) == ["PENDING"]


@pytest.mark.story("US-04", "AC-8")
@pytest.mark.parametrize("length", [256, 4000], ids=["256_chars", "4000_chars"])
def test_us04_ac8_idempotency_key_too_long_is_400_invalid_not_500(api, length):
    from starlette.testclient import TestClient

    sid = api.sid("A1")
    api.hold(sid, "u1")
    # 서버 예외가 테스트 예외가 아니라 500 응답으로 보이도록 raise_server_exceptions=False
    c = TestClient(api.app, raise_server_exceptions=False)
    r = api.confirm(sid, "u1", client=c, key="k" * length)
    assert r.status_code != 500, r.text[:200]
    assert_error(r, 400, "IDEMPOTENCY_KEY_INVALID")
    assert api.booking_statuses(sid) == []
    assert api.status("A1") == "HELD"
    assert api.confirm(sid, "u1", key="ok-key").status_code == 201  # 홀드는 그대로 쓸 수 있다


# ---------- AC-9 같은 키 재요청은 같은 응답 ----------
@pytest.mark.story("US-04", "AC-9")
def test_us04_ac9_same_key_sequential_returns_identical_response(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    r1 = api.confirm(sid, "u1", key="k1")
    assert r1.status_code == 201
    api.clock.advance(seconds=30)  # 시각이 지나도 처음 응답 그대로
    r2 = api.confirm(sid, "u1", key="k1")
    assert (r2.status_code, r2.json()) == (r1.status_code, r1.json())
    assert api.booking_statuses(sid) == ["PENDING"]


@pytest.mark.story("US-04", "AC-9")
def test_us04_ac9_same_key_after_callback_returns_original_response(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    r1 = api.confirm(sid, "u1", key="k1")
    assert api.callback(r1.json()["id"], "SUCCEEDED").json()["status"] == "CONFIRMED"
    r2 = api.confirm(sid, "u1", key="k1")
    assert (r2.status_code, r2.json()) == (r1.status_code, r1.json())  # 본문의 status 도 처음 그대로(PENDING)
    assert api.booking_statuses(sid) == ["CONFIRMED"]


@pytest.mark.story("US-04", "AC-9")
def test_us04_ac9_same_key_after_hold_expired_returns_original_response(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    r1 = api.confirm(sid, "u1", key="k1")
    api.clock.advance(minutes=10)
    r2 = api.confirm(sid, "u1", key="k1")
    assert (r2.status_code, r2.json()) == (r1.status_code, r1.json())
    assert api.booking_statuses(sid) == ["PENDING"]


@pytest.mark.story("US-04", "AC-9")
def test_us04_ac9_same_key_after_failed_callback_returns_original_response(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    r1 = api.confirm(sid, "u1", key="k1")
    api.callback(r1.json()["id"], "FAILED")
    r2 = api.confirm(sid, "u1", key="k1")  # 새 예매를 만들지 않는다
    assert (r2.status_code, r2.json()) == (r1.status_code, r1.json())
    assert api.booking_statuses(sid) == ["FAILED"]


@pytest.mark.story("US-04", "AC-9")
def test_us04_ac9_same_key_concurrent_returns_one_booking_same_response(api):
    for number in ["A1", "A2", "A3"]:
        sid = api.sid(number)
        assert api.hold(sid, "u1").status_code == 201
        rs = api.parallel(lambda c, i: api.confirm(sid, "u1", client=c, key=f"same-key-{number}"), 8)
        assert {r.status_code for r in rs} == {201}, f"{number}: {[r.status_code for r in rs]}"
        bodies = {json.dumps(r.json(), sort_keys=True) for r in rs}  # 키 순서가 아니라 내용을 비교
        assert len(bodies) == 1, f"{number}: 응답 본문이 서로 다름: {sorted(bodies)}"
        assert api.booking_statuses(sid) == ["PENDING"]


@pytest.mark.story("US-04", "AC-9")
def test_us04_ac9_error_responses_are_not_remembered(api):
    """성공(2xx)만 저장한다: 오류였던 키는 다시 보내면 새로 판단한다."""
    sid = api.sid("A1")
    assert_error(api.confirm(sid, "u1", key="k1"), 409, "HOLD_REQUIRED")
    assert api.hold(sid, "u1").status_code == 201
    r = api.confirm(sid, "u1", key="k1")
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "PENDING"


@pytest.mark.story("US-04", "AC-9")
def test_us04_ac9_idempotency_key_is_per_user(api):
    s1, s2 = api.sid("A1"), api.sid("A2")
    api.hold(s1, "u1")
    api.hold(s2, "u2")
    r1 = api.confirm(s1, "u1", key="shared")
    r2 = api.confirm(s2, "u2", key="shared")  # 사용자가 다르면 별개 요청
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    assert r1.json()["id"] != r2.json()["id"]
    assert r1.json()["user_id"] == "u1" and r2.json()["user_id"] == "u2"


@pytest.mark.story("US-04", "AC-9")
def test_us04_ac9_same_key_waiter_gets_original_response_even_if_deadline_passes_while_waiting(api):
    """같은 키 요청 둘이 겹침: 앞 요청이 4분 59초에 성공하고, 좌석 잠금을 기다리던 뒤 요청은 5분 정각에 처리돼도
    HOLD_EXPIRED 가 아니라 처음 응답(201 같은 본문)을 받는다. 예매는 하나."""
    r1, r2 = _overlapping_confirms(api, key1="k1", key2="k1")
    assert r1.status_code == 201, r1.text
    assert r1.json()["status"] == "PENDING"
    assert (r2.status_code, r2.json()) == (r1.status_code, r1.json()), r2.text
    assert api.booking_statuses(api.sid("A1")) == ["PENDING"]


@pytest.mark.story("US-04", "AC-2")
def test_us04_ac2_new_key_waiter_still_gets_hold_expired_when_deadline_passes_while_waiting(api):
    """대조군: 같은 상황에서 키가 다르면(새 요청) 5분 정각 판단은 그대로 HOLD_EXPIRED."""
    r1, r2 = _overlapping_confirms(api, key1="k1", key2="k2")
    assert r1.status_code == 201, r1.text
    assert_error(r2, 409, "HOLD_EXPIRED")
    assert api.booking_statuses(api.sid("A1")) == ["PENDING"]


def _overlapping_confirms(api, key1, key2, timeout=15.0):
    """T0 에 홀드한 A1 에 확정 요청 둘을 겹쳐 보낸다.
    첫 요청은 (좌석 잠금을 잡은 채) 첫 시계 읽기에서 T0+4분59초를 받고 멈춘다. 둘째 요청이 좌석 행 잠금을
    실제로 기다리는 것을 pg_stat_activity 로 확인한 뒤 시계를 T0+5분으로 옮기고 첫 요청을 푼다.
    (구현이 요청 시작 때도 시계를 읽으면 첫 읽기가 잠금 앞이라 둘째가 대기하지 않아 이 헬퍼가 실패한다.)"""
    import threading
    import time

    import psycopg
    from starlette.testclient import TestClient

    from apihelpers import _dsn
    from backend.app.deps import get_now

    sid = api.sid("A1")
    assert api.hold(sid, "u1").status_code == 201  # T0
    first_read, release, lock = threading.Event(), threading.Event(), threading.Lock()
    calls = []

    def gated():
        with lock:
            n = len(calls)
            calls.append(n)
        if n == 0:
            first_read.set()
            assert release.wait(timeout), "첫 요청을 풀지 못함"
            return T0 + timedelta(minutes=4, seconds=59)
        return api.clock.now

    api.app.dependency_overrides[get_now] = gated
    out = {}

    def send(name, key):
        try:
            out[name] = api.confirm(sid, "u1", client=TestClient(api.app), key=key)
        except BaseException as e:  # noqa: BLE001
            out[name] = e

    t1 = threading.Thread(target=send, args=("r1", key1), daemon=True)
    t2 = threading.Thread(target=send, args=("r2", key2), daemon=True)
    try:
        t1.start()
        assert first_read.wait(timeout), "첫 요청이 시계를 읽지 않음"
        t2.start()
        waiting = 0
        # autocommit: 트랜잭션 안에서는 pg_stat_activity 스냅샷이 캐시돼 뒤늦게 생긴 대기자가 안 보인다
        with psycopg.connect(_dsn(), autocommit=True) as conn:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and "r2" not in out:
                waiting = conn.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'"
                ).fetchone()[0]
                if waiting:
                    break
                time.sleep(0.05)
        if not waiting:
            with psycopg.connect(_dsn(), autocommit=True) as conn:
                snap = conn.execute(
                    "SELECT state, wait_event_type, wait_event, left(query, 80) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                ).fetchall()
            r2 = out.get("r2")
            detail = r2 if isinstance(r2, BaseException) or r2 is None else (r2.status_code, r2.text[:200])
            pytest.fail(f"둘째 요청이 좌석 행 잠금을 기다리지 않음 (r2={detail}, 시계 호출 {len(calls)}회, pg={snap})")
        api.clock.now = T0 + HOLD_TTL  # 잠금 대기 중 마감 도달
    finally:
        release.set()
        t1.join(timeout)
        t2.join(timeout)
    for name in ("r1", "r2"):
        assert name in out, f"{name} 가 끝나지 않음"
        if isinstance(out[name], BaseException):
            raise out[name]
    return out["r1"], out["r2"]


# ---------- AC-10 같은 키를 다른 좌석에 ----------
@pytest.mark.story("US-04", "AC-10")
def test_us04_ac10_same_key_other_seat_rejected(api):
    s1, s2 = api.sid("A1"), api.sid("A2")
    api.hold(s1, "u1")
    api.hold(s2, "u1")
    assert api.confirm(s1, "u1", key="k1").status_code == 201
    assert_error(api.confirm(s2, "u1", key="k1"), 422, "IDEMPOTENCY_KEY_REUSED")
    assert api.booking_statuses(s2) == []
    assert api.status("A2") == "HELD"
    assert api.confirm(s2, "u1", key="k2").status_code == 201  # 다른 키면 된다


# ---------- AC-11 같은 사람이 다시 확정 요청 → 기존 예매 ----------
@pytest.mark.story("US-04", "AC-11")
def test_us04_ac11_reconfirm_after_confirmed_returns_existing_booking(api):
    sid = api.sid("A1")
    done = api.buy(sid, "u1")
    r = api.confirm(sid, "u1", key="another-key")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == done["id"] and r.json()["status"] == "CONFIRMED"
    assert api.booking_statuses(sid) == ["CONFIRMED"]


@pytest.mark.story("US-04", "AC-11")
def test_us04_ac11_reconfirm_after_confirmed_works_even_after_hold_time_passed(api):
    sid = api.sid("A1")
    done = api.buy(sid, "u1")
    api.clock.advance(hours=1)
    r = api.confirm(sid, "u1", key="another-key")
    assert r.status_code == 200 and r.json()["id"] == done["id"], r.text
    assert api.booking_statuses(sid) == ["CONFIRMED"]


@pytest.mark.story("US-04", "AC-11")
def test_us04_ac11_reconfirm_while_pending_returns_existing_pending_booking(api):
    sid = api.sid("A1")
    b = api.pending(sid, "u1")
    api.clock.advance(minutes=1)
    r = api.confirm(sid, "u1", key="another-key")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == b["id"] and r.json()["status"] == "PENDING"
    assert parse_dt(r.json()["expires_at"]) == parse_dt(b["expires_at"])
    assert api.booking_statuses(sid) == ["PENDING"]


@pytest.mark.story("US-04", "AC-11")
def test_us04_ac11_pending_booking_with_expired_hold_is_hold_expired(api):
    """유효한 홀드에 묶인 PENDING 일 때만 기존 예매를 돌려준다. 홀드가 만료됐으면 HOLD_EXPIRED."""
    sid = api.sid("A1")
    api.pending(sid, "u1")
    api.clock.advance(minutes=5)
    assert_error(api.confirm(sid, "u1", key="another-key"), 409, "HOLD_EXPIRED")
    assert api.booking_statuses(sid) == ["PENDING"]


@pytest.mark.story("US-04", "AC-11")
def test_us04_ac11_other_user_cannot_get_existing_booking(api):
    sid = api.sid("A1")
    api.buy(sid, "u1")
    assert_error(api.confirm(sid, "u2", key="k-u2"), 409, "SEAT_ALREADY_SOLD")
    assert api.booking_statuses(sid) == ["CONFIRMED"]


# ---------- AC-12 결제 실패 뒤 재시도 ----------
@pytest.mark.story("US-04", "AC-12")
def test_us04_ac12_retry_after_failed_creates_new_pending_with_same_deadline(api):
    sid = api.sid("A1")
    hold = api.hold(sid, "u1").json()
    first = api.confirm(sid, "u1", key="k1").json()
    assert api.callback(first["id"], "FAILED").json()["status"] == "FAILED"
    assert api.status("A1") == "HELD"  # 실패해도 홀드 유지
    api.clock.advance(minutes=2)
    r = api.confirm(sid, "u1", key="k2")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["id"] != first["id"] and b["status"] == "PENDING"
    assert parse_dt(b["expires_at"]) == parse_dt(hold["expires_at"])  # 마감은 처음 홀드의 만료 시각 그대로
    assert api.booking_statuses(sid) == ["FAILED", "PENDING"]
    assert api.get_booking(first["id"], "u1").json()["status"] == "FAILED"


@pytest.mark.story("US-04", "AC-12")
def test_us04_ac12_retry_can_then_succeed(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    first = api.confirm(sid, "u1", key="k1").json()
    api.callback(first["id"], "FAILED")
    second = api.confirm(sid, "u1", key="k2").json()
    assert api.callback(second["id"], "SUCCEEDED").json()["status"] == "CONFIRMED"
    assert api.status("A1") == "SOLD"
    assert api.booking_statuses(sid) == ["CONFIRMED", "FAILED"]


@pytest.mark.story("US-04", "AC-12")
def test_us04_ac12_retry_after_failed_but_hold_expired_is_rejected(api):
    sid = api.sid("A1")
    api.hold(sid, "u1")
    first = api.confirm(sid, "u1", key="k1").json()
    api.callback(first["id"], "FAILED")
    api.clock.advance(minutes=5)
    assert_error(api.confirm(sid, "u1", key="k2"), 409, "HOLD_EXPIRED")
    assert api.booking_statuses(sid) == ["FAILED"]


# ---------- 기타 ----------
@pytest.mark.story("US-04", "404")
def test_us04_confirm_unknown_seat_404(api):
    assert_error(api.confirm("no-such-seat", "u1"), 404, "SEAT_NOT_FOUND")
