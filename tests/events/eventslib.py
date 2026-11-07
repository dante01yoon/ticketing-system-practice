"""10장 알림 테스트 공용 도구 (docs/events.md). 픽스처는 conftest.py.

- 알림·outbox 는 전체 수로 세지 않는다: 테스트마다 새 사용자 id(UUID 접미사)와 그 테스트의 booking_id·event_id 로 센다.
- Kafka 는 compose `events` 프로필의 `kafka`. 안 떠 있으면 skip 이 아니라 실패한다.
- 이름 가림을 피하려고 이 모듈은 고유한 이름이다. 함수 안에서 `from conftest import` 를 쓰지 않는다.
"""
import contextlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "api"))
sys.path.insert(0, str(ROOT / "tests"))

from apihelpers import T0, _dsn  # noqa: E402,F401


def bootstrap():
    return os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")


def topic():
    return os.environ.get("BOOKING_EVENTS_TOPIC", "booking-events")


# ---------- Kafka 제어 ----------
def kafka_up():
    try:
        from confluent_kafka.admin import AdminClient

        AdminClient({"bootstrap.servers": bootstrap(), "socket.timeout.ms": 3000}).list_topics(timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


def wait_kafka(up, timeout=90.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if kafka_up() == up:
            return
        time.sleep(0.5)
    pytest.fail(f"Kafka 가 {timeout:.0f}초 안에 {'켜지지' if up else '꺼지지'} 않음 ({bootstrap()})", pytrace=False)


def compose(*args, timeout=300):
    r = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        pytest.fail(f"docker compose {' '.join(args)} 실패: {r.stderr[-800:]}", pytrace=False)
    return r


@contextlib.contextmanager
def kafka_stopped():
    """kafka 를 멈춘다(stop 만, 다시 만들지 않는다). 나갈 때 반드시 start 하고 응답할 때까지 기다린다."""
    try:
        compose("stop", "kafka")
        wait_kafka(False, timeout=30)
        yield
    finally:
        compose("start", "kafka")
        wait_kafka(True, timeout=120)


# ---------- DB 직접 확인 ----------
def new_user():
    return f"ev-{uuid.uuid4().hex[:12]}"


def outbox_rows(booking_id):
    """[(event_id, sent_at)] — 이 예매의 outbox 줄."""
    import psycopg

    with psycopg.connect(_dsn()) as conn:
        return conn.execute(
            "SELECT event_id, sent_at FROM outbox WHERE booking_id = %s ORDER BY 1", (booking_id,)
        ).fetchall()


def notifications(user_id=None, booking_id=None, event_id=None):
    """[(user_id, event_id, booking_id)] — 조건에 맞는 알림."""
    import psycopg

    where, args = [], []
    for col, val in (("user_id", user_id), ("booking_id", booking_id), ("event_id", event_id)):
        if val is not None:
            where.append(f"{col} = %s")
            args.append(val)
    sql = "SELECT user_id, event_id, booking_id FROM notifications" + (" WHERE " + " AND ".join(where) if where else "")
    with psycopg.connect(_dsn()) as conn:
        return conn.execute(sql, args).fetchall()


def seat_raw_status(seat_id):
    import psycopg

    with psycopg.connect(_dsn()) as conn:
        return conn.execute("SELECT status FROM seats WHERE id = %s", (seat_id,)).fetchone()[0]


# ---------- 흐름 도우미 ----------
def confirm_booking(api, number, user=None):
    """대기열 → 입장권 → 홀드 → 예매 → 성공 콜백 (기존 Api 헬퍼). (user, 예매 본문)."""
    user = user or new_user()
    booking = api.buy(api.sid(number), user)
    return user, booking


def relay_until_sent(booking_ids, timeout=40.0):
    """릴레이를 반복해 이 예매들의 outbox 줄이 모두 sent_at 으로 표시될 때까지. Kafka 가 막 돌아온 직후의 일시 오류는 다시 시도한다."""
    from backend.app.events import relay_once

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            relay_once()
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        rows = [r for b in booking_ids for r in outbox_rows(b)]
        if len(rows) == len(booking_ids) and all(sent for _, sent in rows):
            return
        time.sleep(0.5)
    pytest.fail(f"{timeout:.0f}초 안에 outbox 가 모두 전송 표시되지 않음: {[outbox_rows(b) for b in booking_ids]} 마지막 오류: {last}", pytrace=False)


def pump_until(notifier, done, timeout=60.0):
    """done() 이 참이 될 때까지 poll_once 를 반복한다. 처리한 메시지 수 합계를 돌려준다."""
    total = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        total += notifier.poll_once(timeout=2.0)
        if done():
            return total
    pytest.fail(f"{timeout:.0f}초 안에 기대한 알림이 생기지 않음", pytrace=False)


def poll_some(notifier, seconds=4.0):
    """남은 메시지를 더 받아 처리하게 둔다 (중복이 늘어나지 않는지 보려고). 처리 수 합계."""
    total, deadline = 0, time.monotonic() + seconds
    while time.monotonic() < deadline:
        total += notifier.poll_once(timeout=1.0)
    return total


def drain_notifier(max_seconds=90.0):
    """그룹이 토픽 끝까지 따라잡게 한다: poll_once 가 연속 두 번 0 일 때까지 (첫 poll 은 그룹 가입 때문에 0 일 수 있다)."""
    from backend.app.notifier import Notifier

    n = Notifier()
    try:
        zeros, deadline = 0, time.monotonic() + max_seconds
        while zeros < 2 and time.monotonic() < deadline:
            zeros = zeros + 1 if n.poll_once(timeout=3.0) == 0 else 0
    finally:
        n.close()


def produce_raw(value, key=None):
    """토픽에 메시지를 직접 보낸다 (str/bytes). 전송 확인까지 기다린다."""
    from confluent_kafka import Producer

    errors = []
    p = Producer({"bootstrap.servers": bootstrap(), "acks": "all"})
    p.produce(topic(), value=value, key=key, on_delivery=lambda err, msg: errors.append(err) if err else None)
    assert p.flush(15) == 0, "Kafka 에 보내지 못함"
    assert not errors, errors


def event_message(event_id, booking_id, user_id, concert_id, seat_id, type_="BookingConfirmed"):
    return json.dumps(
        {
            "event_id": event_id,
            "type": type_,
            "booking_id": booking_id,
            "user_id": user_id,
            "concert_id": concert_id,
            "seat_id": seat_id,
            "confirmed_at": (T0 + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
