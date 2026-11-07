"""10장 / US-09: 예매 확정 알림 (docs/events.md ⑤). 콜백 → outbox → 릴레이 → Kafka → notifier → notifications.
가짜 시계 인프로세스 앱 + 실제 PostgreSQL + 실제 Kafka(localhost:9092)."""
import time
from datetime import timedelta

import pytest

import eventslib as ev
from apihelpers import T0


def _pending(api, number, user=None):
    user = user or ev.new_user()
    return user, api.pending(api.sid(number), user)


# ---------- AC-1 확정 1건 → 알림 1건 ----------
@pytest.mark.story("US-09", "AC-1")
def test_confirmed_booking_goes_outbox_then_relay_then_exactly_one_notification(api, make_notifier):
    user, b = _pending(api, "A1")
    assert ev.outbox_rows(b["id"]) == [], "결제 대기(PENDING)만으로는 outbox 줄이 없어야 함"
    r = api.callback(b["id"], "SUCCEEDED")
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED", r.text
    rows = ev.outbox_rows(b["id"])
    assert len(rows) == 1, rows
    event_id, sent_at = rows[0]
    assert sent_at is None, "릴레이 전에는 sent_at 이 NULL"
    assert api.callback(b["id"], "SUCCEEDED").status_code == 200  # 중복 성공 콜백
    assert len(ev.outbox_rows(b["id"])) == 1, "중복 성공 콜백이 outbox 줄을 늘림"

    ev.relay_until_sent([b["id"]])
    rows = ev.outbox_rows(b["id"])
    assert len(rows) == 1 and rows[0][0] == event_id and rows[0][1] is not None

    n = make_notifier()
    ev.pump_until(n, lambda: len(ev.notifications(user_id=user)) >= 1)
    ev.poll_some(n, 3.0)
    got = ev.notifications(user_id=user)
    assert got == [(user, event_id, b["id"])], got
    assert ev.notifications(booking_id=b["id"]) == got


@pytest.mark.story("US-09", "AC-1")
def test_failed_callback_makes_no_outbox_row(api):
    user, b = _pending(api, "A1")
    r = api.callback(b["id"], "FAILED")
    assert r.status_code == 200 and r.json()["status"] == "FAILED", r.text
    assert ev.outbox_rows(b["id"]) == []
    api.callback(b["id"], "SUCCEEDED")  # 반대 결과 콜백도 상태를 바꾸지 않으므로 이벤트가 없다
    assert ev.outbox_rows(b["id"]) == []


@pytest.mark.story("US-09", "AC-1")
def test_late_success_refund_required_makes_no_outbox_row(api):
    user, b = _pending(api, "A1")
    api.clock.now = T0 + timedelta(minutes=5, seconds=1)  # 홀드 만료 뒤의 늦은 성공
    r = api.callback(b["id"], "SUCCEEDED")
    assert r.status_code == 200 and r.json()["status"] == "REFUND_REQUIRED", r.text
    assert ev.outbox_rows(b["id"]) == []
    api.callback(b["id"], "SUCCEEDED")  # 중복
    assert ev.outbox_rows(b["id"]) == []


# ---------- AC-2 Kafka 가 잠깐 멈춰도 확정은 성공 ----------
@pytest.mark.story("US-09", "AC-2")
def test_confirm_succeeds_while_kafka_is_stopped_and_notification_arrives_after_restart(api, make_notifier):
    user, b = _pending(api, "A1")  # 멈추기 전에 결제 대기까지 만들어 둔다
    with ev.kafka_stopped():
        r = api.callback(b["id"], "SUCCEEDED")
        assert r.status_code == 200 and r.json()["status"] == "CONFIRMED", r.text
        assert ev.seat_raw_status(api.sid("A1")) == "SOLD"
        rows = ev.outbox_rows(b["id"])
        assert len(rows) == 1 and rows[0][1] is None

        from backend.app.events import relay_once

        t0 = time.monotonic()
        try:
            marked = relay_once()
        except Exception:  # noqa: BLE001  (예외도 허용)
            marked = 0
        assert time.monotonic() - t0 < 10, "Kafka 가 없을 때 relay_once 가 10초 안에 돌아오지 않음"
        assert not marked, f"전송하지 못했는데 {marked}줄을 표시함"
        assert ev.outbox_rows(b["id"])[0][1] is None, "전송 확인 없이 sent_at 이 채워짐"
        assert ev.notifications(user_id=user) == []
    # kafka 복구됨
    ev.relay_until_sent([b["id"]])
    n = make_notifier()
    ev.pump_until(n, lambda: len(ev.notifications(user_id=user)) >= 1)
    ev.poll_some(n, 3.0)
    got = ev.notifications(user_id=user)
    assert len(got) == 1 and got[0][2] == b["id"], got
    assert api.get_booking(b["id"], user).json()["status"] == "CONFIRMED"


# ---------- AC-3 소비자를 껐다 켜도 이어 받음 ----------
@pytest.mark.story("US-09", "AC-3")
def test_new_notifier_resumes_after_close_each_booking_exactly_one_notification(api, make_notifier):
    first, second = [], []
    for number in ("A1", "A2", "A3"):
        user, b = ev.confirm_booking(api, number)
        first.append((user, b["id"]))
    ev.relay_until_sent([bid for _, bid in first])

    n1 = make_notifier()
    ev.pump_until(n1, lambda: len(ev.notifications(user_id=first[0][0])) >= 1)  # 일부(또는 전부)만 처리
    n1.close()

    for number in ("A4", "A5", "A6"):
        user, b = ev.confirm_booking(api, number)
        second.append((user, b["id"]))
    ev.relay_until_sent([bid for _, bid in second])

    n2 = make_notifier()
    everyone = first + second
    ev.pump_until(n2, lambda: all(ev.notifications(user_id=u) for u, _ in everyone))
    ev.poll_some(n2, 3.0)
    for user, bid in everyone:
        got = ev.notifications(user_id=user)
        assert len(got) == 1 and got[0][2] == bid, f"{user}: {got}"
    assert len({e for u, _ in everyone for _, e, _ in ev.notifications(user_id=u)}) == len(everyone)


# ---------- AC-4 같은 이벤트가 다시 와도 알림은 하나 ----------
@pytest.mark.story("US-09", "AC-4")
@pytest.mark.parametrize("how", ["producer_resend", "reset_sent_at_and_relay"])
def test_same_event_delivered_again_does_not_add_a_notification(api, make_notifier, how):
    user, b = ev.confirm_booking(api, "A1")
    ev.relay_until_sent([b["id"]])
    event_id = ev.outbox_rows(b["id"])[0][0]
    n = make_notifier()
    ev.pump_until(n, lambda: len(ev.notifications(user_id=user)) >= 1)
    ev.poll_some(n, 2.0)
    assert len(ev.notifications(user_id=user)) == 1

    if how == "producer_resend":
        ev.produce_raw(ev.event_message(event_id, b["id"], user, api.concert_id, api.sid("A1")), key=api.concert_id)
    else:
        import psycopg

        with psycopg.connect(ev._dsn()) as conn:
            conn.execute("UPDATE outbox SET sent_at = NULL WHERE booking_id = %s", (b["id"],))
        ev.relay_until_sent([b["id"]])
    received = ev.pump_until(n, lambda: True) + ev.poll_some(n, 6.0)  # 다시 온 메시지를 notifier 가 실제로 받게 둔다
    assert received >= 1, "다시 보낸 이벤트를 notifier 가 받지 못함(시험이 의미 없음)"
    got = ev.notifications(user_id=user)
    assert len(got) == 1 and got[0][1] == event_id, got
    assert len(ev.notifications(event_id=event_id)) == 1


# ---------- 계약: 알 수 없는 메시지는 건너뛰고 소비가 멈추지 않음 ----------
@pytest.mark.story("US-09", "AC-4")
def test_garbage_and_unknown_type_messages_are_skipped_without_blocking(api, make_notifier):
    ev.produce_raw("이건 JSON 이 아님 {{{", key=api.concert_id)
    ev.produce_raw(ev.event_message("x-unknown-" + ev.new_user(), "no-booking", "nobody", api.concert_id, "s", "SomethingElse"), key=api.concert_id)
    user, b = ev.confirm_booking(api, "A1")
    ev.relay_until_sent([b["id"]])
    n = make_notifier()
    ev.pump_until(n, lambda: len(ev.notifications(user_id=user)) >= 1)
    ev.poll_some(n, 2.0)
    assert len(ev.notifications(user_id=user)) == 1
    assert ev.notifications(user_id="nobody") == []
