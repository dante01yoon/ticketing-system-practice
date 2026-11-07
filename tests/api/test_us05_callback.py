"""US-05 모의 결제 콜백. 판단은 콜백을 처리하는 순간의 서버 시각(get_now)으로만 한다."""
from datetime import timedelta

import pytest

from apihelpers import HOLD_TTL, T0, assert_error, parse_dt, request_blocked_by_row_lock


def pending_at(api, number="A1", user="u1"):
    """T0 에 홀드하고 확정 요청까지 한 PENDING 예매 (결제 마감 T0+5분)."""
    assert api.clock.now == T0
    return api.pending(api.sid(number), user)


# ---------- AC-1 성공 콜백 → 판매 ----------
@pytest.mark.story("US-05", "AC-1")
@pytest.mark.parametrize(
    "delta", [timedelta(seconds=0), timedelta(minutes=4, seconds=59)], ids=["immediately", "4m59s"]
)
def test_us05_ac1_success_before_deadline_confirms_and_sells(api, delta):
    b = pending_at(api)
    api.clock.now = T0 + delta
    r = api.callback(b["id"], "SUCCEEDED")  # X-User-Id 없이
    assert r.status_code == 200, r.text
    assert r.json()["id"] == b["id"] and r.json()["status"] == "CONFIRMED"
    assert api.status("A1") == "SOLD"
    assert api.booking_statuses(api.sid("A1")) == ["CONFIRMED"]
    assert api.get_booking(b["id"], "u1").json()["status"] == "CONFIRMED"


@pytest.mark.story("US-05", "AC-1")
def test_us05_ac1_sold_seat_stays_sold_forever(api):
    b = pending_at(api)
    api.callback(b["id"], "SUCCEEDED")
    api.clock.advance(hours=3)
    assert api.status("A1") == "SOLD"
    assert_error(api.hold(api.sid("A1"), "u2"), 409, "SEAT_ALREADY_SOLD")


# ---------- AC-2 실패 콜백 ----------
@pytest.mark.story("US-05", "AC-2")
def test_us05_ac2_failed_callback_keeps_hold_and_does_not_sell(api):
    b = pending_at(api)
    api.clock.advance(minutes=1)
    r = api.callback(b["id"], "FAILED")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FAILED"
    assert api.status("A1") == "HELD"
    assert_error(api.hold(api.sid("A1"), "u2"), 409, "SEAT_ALREADY_HELD")  # u1 의 홀드 유지
    assert api.booking_statuses(api.sid("A1")) == ["FAILED"]


@pytest.mark.story("US-05", "AC-2")
def test_us05_ac2_failed_then_hold_expires_seat_becomes_available(api):
    b = pending_at(api)
    api.callback(b["id"], "FAILED")
    api.clock.advance(minutes=5)
    assert api.status("A1") == "AVAILABLE"
    assert api.hold(api.sid("A1"), "u2").status_code == 201


# ---------- AC-3 늦은 성공 콜백 → 환불 필요 (경계) ----------
@pytest.mark.story("US-05", "AC-3")
@pytest.mark.parametrize(
    "delta", [timedelta(minutes=5), timedelta(minutes=5, seconds=1)], ids=["exactly_5m", "5m1s"]
)
def test_us05_ac3_late_success_is_refund_required_and_not_sold(api, delta):
    b = pending_at(api)
    api.clock.now = T0 + delta
    r = api.callback(b["id"], "SUCCEEDED")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REFUND_REQUIRED"
    assert api.status("A1") == "AVAILABLE"  # 팔리지 않았고 홀드는 만료
    assert api.booking_statuses(api.sid("A1")) == ["REFUND_REQUIRED"]
    assert api.get_booking(b["id"], "u1").json()["status"] == "REFUND_REQUIRED"
    assert api.hold(api.sid("A1"), "u2").status_code == 201  # 다시 잡을 수 있다


@pytest.mark.story("US-05", "AC-3")
def test_us05_ac3_booking_stays_pending_until_a_callback_arrives(api):
    """만료돼도 콜백이 오기 전에는 예매 상태가 바뀌지 않는다(별도 배치 없음)."""
    b = pending_at(api)
    api.clock.advance(minutes=10)
    assert api.get_booking(b["id"], "u1").json()["status"] == "PENDING"


@pytest.mark.story("US-05", "AC-3")
def test_us05_ac3_deadline_is_hold_expiry_not_callback_body_time(api):
    """콜백 본문의 시각 필드는 무시한다. 서버 시각이 마감 이후면 본문이 '이른 시각'이라 해도 환불 필요."""
    b = pending_at(api)
    api.clock.advance(minutes=5, seconds=1)
    r = api.callback(b["id"], "SUCCEEDED", timestamp="2030-01-01T12:01:00Z", paid_at="2030-01-01T12:01:00Z")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REFUND_REQUIRED"
    assert api.status("A1") == "AVAILABLE"


@pytest.mark.story("US-05", "AC-3")
def test_us05_ac3_body_time_cannot_make_valid_callback_late(api):
    b = pending_at(api)
    api.clock.advance(minutes=1)
    r = api.callback(b["id"], "SUCCEEDED", timestamp="2031-01-01T00:00:00Z")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED"


@pytest.mark.story("US-05", "AC-3")
def test_us05_ac3_time_is_read_after_seat_row_lock(api):
    """콜백이 좌석 행 잠금을 기다리는 사이 마감이 지나면 REFUND_REQUIRED (시각은 잠금 뒤에 읽는다)."""
    b = pending_at(api)
    resp = request_blocked_by_row_lock(
        api,
        api.sid("A1"),
        send=lambda c: api.callback(b["id"], "SUCCEEDED", client=c),
        clock_at_send=T0 + timedelta(minutes=4, seconds=59),  # 요청 시작 시점엔 마감 전
        clock_after_wait=T0 + HOLD_TTL,  # 잠금을 기다리는 사이 마감
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REFUND_REQUIRED"
    assert api.status("A1") == "AVAILABLE"


# ---------- AC-4 늦은 성공 콜백, 그 사이 다른 사람이 홀드 ----------
@pytest.mark.story("US-05", "AC-4")
def test_us05_ac4_late_success_after_rehold_keeps_new_holder(api):
    sid = api.sid("A1")
    b = pending_at(api)
    api.clock.advance(minutes=5)
    assert api.hold(sid, "u2").status_code == 201  # u1 홀드 만료 → u2 재홀드
    api.clock.advance(minutes=1)
    r = api.callback(b["id"], "SUCCEEDED")  # u1 의 늦은 성공 콜백
    assert r.status_code == 200 and r.json()["status"] == "REFUND_REQUIRED", r.text
    assert api.status("A1") == "HELD"  # u2 의 HELD 유지
    assert_error(api.hold(sid, "u3"), 409, "SEAT_ALREADY_HELD")
    # u2 는 정상적으로 확정·결제할 수 있다
    r2 = api.confirm(sid, "u2")
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] != b["id"] and r2.json()["user_id"] == "u2"
    done = api.callback(r2.json()["id"], "SUCCEEDED")
    assert done.json()["status"] == "CONFIRMED"
    assert api.status("A1") == "SOLD"
    assert api.booking_statuses(sid) == ["CONFIRMED", "REFUND_REQUIRED"]
    assert api.get_booking(b["id"], "u1").json()["status"] == "REFUND_REQUIRED"


@pytest.mark.story("US-05", "AC-4")
def test_us05_ac4_u2_holds_then_u1_late_success_before_u2_confirm(api):
    """u2 가 막 홀드한 직후(홀드 시각 = 마감 시각)에도 u1 의 늦은 콜백은 u2 의 홀드를 건드리지 않는다."""
    sid = api.sid("A1")
    b = pending_at(api)
    api.clock.advance(minutes=5)
    api.hold(sid, "u2")
    r = api.callback(b["id"], "SUCCEEDED")
    assert r.json()["status"] == "REFUND_REQUIRED"
    assert api.status("A1") == "HELD"
    assert api.booking_statuses(sid) == ["REFUND_REQUIRED"]


# ---------- AC-5 같은 콜백이 두 번 → 한 번만 반영 ----------
@pytest.mark.story("US-05", "AC-5")
def test_us05_ac5_duplicate_success_callback_sequential(api):
    b = pending_at(api)
    r1 = api.callback(b["id"], "SUCCEEDED")
    api.clock.advance(minutes=1)
    r2 = api.callback(b["id"], "SUCCEEDED")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json() and r2.json()["status"] == "CONFIRMED"
    assert api.status("A1") == "SOLD"
    assert api.booking_statuses(api.sid("A1")) == ["CONFIRMED"]


@pytest.mark.story("US-05", "AC-5")
def test_us05_ac5_duplicate_success_callback_after_deadline_does_not_downgrade(api):
    """CONFIRMED 가 된 뒤 마감이 지나 같은 성공 콜백이 다시 와도 REFUND_REQUIRED 로 바뀌지 않는다."""
    b = pending_at(api)
    api.callback(b["id"], "SUCCEEDED")
    api.clock.advance(minutes=30)
    r = api.callback(b["id"], "SUCCEEDED")
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED"
    assert api.status("A1") == "SOLD"


@pytest.mark.story("US-05", "AC-5")
def test_us05_ac5_duplicate_refund_callback_is_noop(api):
    b = pending_at(api)
    api.clock.advance(minutes=6)
    first = api.callback(b["id"], "SUCCEEDED")
    second = api.callback(b["id"], "SUCCEEDED")
    assert first.json()["status"] == second.json()["status"] == "REFUND_REQUIRED"
    assert api.booking_statuses(api.sid("A1")) == ["REFUND_REQUIRED"]


@pytest.mark.story("US-05", "AC-5")
def test_us05_ac5_concurrent_success_callbacks_apply_once(api):
    for number in ["A1", "A2", "A3", "A4", "A5"]:
        sid = api.sid(number)
        b = api.pending(sid, "u1")
        rs = api.parallel(lambda c, i: api.callback(b["id"], "SUCCEEDED", client=c), 8)
        assert [r.status_code for r in rs] == [200] * 8, f"{number}: {[r.status_code for r in rs]}"
        assert {r.json()["status"] for r in rs} == {"CONFIRMED"}, number
        assert {r.json()["id"] for r in rs} == {b["id"]}
        assert api.booking_statuses(sid) == ["CONFIRMED"]
        assert api.status(number) == "SOLD"


@pytest.mark.story("US-05", "AC-5")
def test_us05_ac5_concurrent_late_success_callbacks_all_refund_required(api):
    sid = api.sid("A1")
    b = pending_at(api)
    api.clock.advance(minutes=6)
    rs = api.parallel(lambda c, i: api.callback(b["id"], "SUCCEEDED", client=c), 8)
    assert [r.status_code for r in rs] == [200] * 8
    assert {r.json()["status"] for r in rs} == {"REFUND_REQUIRED"}
    assert api.booking_statuses(sid) == ["REFUND_REQUIRED"]
    assert api.status("A1") == "AVAILABLE"


# ---------- AC-6 결과가 정해진 뒤 반대 결과 콜백은 무시 ----------
@pytest.mark.story("US-05", "AC-6")
def test_us05_ac6_failed_after_confirmed_is_ignored(api):
    b = pending_at(api)
    api.callback(b["id"], "SUCCEEDED")
    r = api.callback(b["id"], "FAILED")
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED"
    assert api.status("A1") == "SOLD"
    assert api.booking_statuses(api.sid("A1")) == ["CONFIRMED"]


@pytest.mark.story("US-05", "AC-6")
def test_us05_ac6_success_after_failed_is_ignored(api):
    b = pending_at(api)
    api.callback(b["id"], "FAILED")
    api.clock.advance(minutes=1)  # 아직 마감 전이어도 성공 콜백은 반영하지 않는다
    r = api.callback(b["id"], "SUCCEEDED")
    assert r.status_code == 200 and r.json()["status"] == "FAILED"
    assert api.status("A1") == "HELD"
    assert api.booking_statuses(api.sid("A1")) == ["FAILED"]


@pytest.mark.story("US-05", "AC-6")
def test_us05_ac6_success_after_refund_required_is_ignored(api):
    b = pending_at(api)
    api.clock.advance(minutes=6)
    api.callback(b["id"], "SUCCEEDED")
    r = api.callback(b["id"], "FAILED")
    assert r.status_code == 200 and r.json()["status"] == "REFUND_REQUIRED"
    r = api.callback(b["id"], "SUCCEEDED")
    assert r.status_code == 200 and r.json()["status"] == "REFUND_REQUIRED"
    assert api.booking_statuses(api.sid("A1")) == ["REFUND_REQUIRED"]


@pytest.mark.story("US-05", "AC-6")
def test_us05_ac6_concurrent_opposite_callbacks_decide_exactly_once(api):
    """성공·실패 콜백이 동시에 와도 결과는 하나로 정해지고, 모든 응답과 DB·좌석이 그 결과와 일치한다."""
    for number in ["A1", "A2", "A3", "A4", "A5", "A6"]:
        sid = api.sid(number)
        b = api.pending(sid, "u1")
        rs = api.parallel(lambda c, i: api.callback(b["id"], "SUCCEEDED" if i % 2 else "FAILED", client=c), 8)
        assert [r.status_code for r in rs] == [200] * 8
        final = {r.json()["status"] for r in rs}
        assert len(final) == 1 and final <= {"CONFIRMED", "FAILED"}, f"{number}: {final}"
        winner = final.pop()
        assert api.booking_statuses(sid) == [winner]
        assert api.get_booking(b["id"], "u1").json()["status"] == winner
        assert api.status(number) == ("SOLD" if winner == "CONFIRMED" else "HELD")


# ---------- AC-7 없는 예매 ----------
@pytest.mark.story("US-05", "AC-7")
def test_us05_ac7_unknown_booking_404(api):
    assert_error(api.callback("no-such-booking", "SUCCEEDED"), 404, "BOOKING_NOT_FOUND")
    assert_error(api.callback("no-such-booking", "FAILED"), 404, "BOOKING_NOT_FOUND")


# ---------- AC-8 콜백 형식 오류 ----------
BAD_CALLBACKS = [
    pytest.param({"json": {}}, id="empty_object"),
    pytest.param({"json": {"result": "SUCCEEDED"}}, id="booking_id_missing"),
    pytest.param({"json": {"booking_id": None, "result": "SUCCEEDED"}}, id="booking_id_null"),
    pytest.param({"json": {"booking_id": 5, "result": "SUCCEEDED"}}, id="booking_id_number"),
    pytest.param({"json": {"booking_id": "{id}"}}, id="result_missing"),
    pytest.param({"json": {"booking_id": "{id}", "result": "PAID"}}, id="result_unknown_value"),
    pytest.param({"json": {"booking_id": "{id}", "result": "succeeded"}}, id="result_lowercase"),
    pytest.param({"json": {"booking_id": "{id}", "result": True}}, id="result_bool"),
    pytest.param({"json": {"booking_id": "{id}", "result": None}}, id="result_null"),
    pytest.param({"content": "이건 JSON이 아님", "headers": {"Content-Type": "application/json"}}, id="not_json"),
]


@pytest.mark.story("US-05", "AC-8")
@pytest.mark.parametrize("kwargs", BAD_CALLBACKS)
def test_us05_ac8_malformed_callback_is_422_and_changes_nothing(api, kwargs):
    b = pending_at(api)
    kwargs = dict(kwargs)
    if "json" in kwargs:
        kwargs["json"] = {k: (b["id"] if v == "{id}" else v) for k, v in kwargs["json"].items()}
    r = api.client.post("/payments/callback", **kwargs)
    assert_error(r, 422, "VALIDATION_ERROR")
    assert api.get_booking(b["id"], "u1").json()["status"] == "PENDING"
    assert api.status("A1") == "HELD"
    assert api.booking_statuses(api.sid("A1")) == ["PENDING"]


# ---------- AC-9 예매 상태 조회 ----------
@pytest.mark.story("US-05", "AC-9")
def test_us05_ac9_owner_sees_each_state(api):
    b_pending = api.pending(api.sid("A1"), "u1")
    b_failed = api.pending(api.sid("A2"), "u1")
    api.callback(b_failed["id"], "FAILED")
    b_ok = api.pending(api.sid("A3"), "u1")
    api.callback(b_ok["id"], "SUCCEEDED")
    b_late = api.pending(api.sid("A4"), "u1")
    api.clock.advance(minutes=6)
    api.callback(b_late["id"], "SUCCEEDED")

    expected = {
        b_pending["id"]: "PENDING",
        b_failed["id"]: "FAILED",
        b_ok["id"]: "CONFIRMED",
        b_late["id"]: "REFUND_REQUIRED",
    }
    for bid, status in expected.items():
        r = api.get_booking(bid, "u1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == bid and body["status"] == status and body["user_id"] == "u1"
        assert body["concert_id"] == api.concert_id
        parse_dt(body["created_at"])
        parse_dt(body["expires_at"])


@pytest.mark.story("US-05", "AC-9")
def test_us05_ac9_other_users_booking_looks_nonexistent(api):
    b = pending_at(api)
    assert_error(api.get_booking(b["id"], "u2"), 404, "BOOKING_NOT_FOUND")
    unknown = api.get_booking("no-such-booking", "u2")
    assert_error(unknown, 404, "BOOKING_NOT_FOUND")
    # 없는 예매와 남의 예매를 구분할 수 없어야 한다
    assert api.get_booking(b["id"], "u2").json() == unknown.json()


@pytest.mark.story("US-05", "AC-9")
def test_us05_ac9_get_booking_requires_user_id(api):
    b = pending_at(api)
    assert_error(api.get_booking(b["id"], None), 401, "USER_ID_REQUIRED")
    assert_error(api.get_booking(b["id"], ""), 401, "USER_ID_REQUIRED")
