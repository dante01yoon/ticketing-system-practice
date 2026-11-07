"""10장: Kafka → notifications 소비자 (docs/events.md ④).

처리 순서: notifications에 쓰고 DB 커밋 → 그 뒤에 오프셋 동기 커밋. 중복은 event_id UNIQUE가 막는다.
"""
import json
import logging
import os

from confluent_kafka import Consumer, TopicPartition
from sqlalchemy import text

from .db import SessionLocal
from .events import bootstrap, topic_name

log = logging.getLogger("notifier")
BATCH = 100


def _parse(raw: bytes | None) -> dict | None:
    """알림을 만들 메시지면 dict, 깨졌거나 모르는 type이면 None."""
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(d, dict) or d.get("type") != "BookingConfirmed":
        return None
    if not all(isinstance(d.get(k), str) and d[k] for k in ("event_id", "booking_id", "user_id")):
        return None
    return d


class Notifier:
    def __init__(self) -> None:
        self._c = Consumer(
            {
                "bootstrap.servers": bootstrap(),
                "group.id": os.environ.get("NOTIFIER_GROUP", "notifier"),
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
        )
        self._c.subscribe([topic_name()])

    def poll_once(self, timeout: float = 5.0) -> int:
        msgs = [m for m in self._c.consume(BATCH, timeout) if m.error() is None]
        if not msgs:
            return 0
        try:
            with SessionLocal() as db, db.begin():
                for m in msgs:
                    d = _parse(m.value())
                    if d is None:
                        continue
                    db.execute(
                        text(
                            "INSERT INTO notifications (user_id, event_id, booking_id, message) "
                            "VALUES (:u, :e, :b, :msg) ON CONFLICT (event_id) DO NOTHING"
                        ),
                        {"u": d["user_id"], "e": d["event_id"], "b": d["booking_id"], "msg": "예매가 확정되었습니다."},
                    )
        except Exception:
            # DB 쓰기가 실패하면 오프셋을 커밋하지 않고, 같은 소비자가 다시 받도록 되감는다.
            first = {}
            for m in msgs:
                first.setdefault((m.topic(), m.partition()), m.offset())
            for (t, p), off in first.items():
                self._c.seek(TopicPartition(t, p, off))
            raise
        last = {}
        for m in msgs:
            last[(m.topic(), m.partition())] = m.offset() + 1
        self._c.commit(offsets=[TopicPartition(t, p, o) for (t, p), o in last.items()], asynchronous=False)
        return len(msgs)

    def close(self) -> None:
        self._c.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    n = Notifier()
    try:
        while True:
            try:
                n.poll_once(5.0)
            except Exception as e:  # noqa: BLE001
                log.warning("notifier failed: %r", e)
    finally:
        n.close()


if __name__ == "__main__":
    main()
