"""10장: outbox → Kafka 릴레이 (docs/events.md ③).

콜백 요청은 Kafka에 접속하지 않는다. 이 모듈의 relay_once만 Kafka에 닿는다.
"""
import json
import os
import time

from confluent_kafka import KafkaError, KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from sqlalchemy import text

from .db import SessionLocal

PARTITIONS = 3
BUDGET_SECONDS = 8.0  # 호출 전체가 10초 안에 돌아오게 하는 예산


def bootstrap() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")


def topic_name() -> str:
    return os.environ.get("BOOKING_EVENTS_TOPIC", "booking-events")


def ensure_topic(deadline: float) -> None:
    """토픽이 없으면 만든다(파티션 3). Kafka에 닿지 못하면 KafkaException."""
    admin = AdminClient({"bootstrap.servers": bootstrap(), "socket.timeout.ms": 3000})
    name = topic_name()
    md = admin.list_topics(timeout=max(1.0, min(3.0, deadline - time.monotonic())))
    if name in md.topics and md.topics[name].error is None:
        return
    futures = admin.create_topics([NewTopic(name, num_partitions=PARTITIONS, replication_factor=1)], request_timeout=4)
    try:
        futures[name].result(timeout=max(1.0, min(5.0, deadline - time.monotonic())))
    except KafkaException as e:
        if e.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise


def relay_once(limit: int = 100) -> int:
    """안 보낸 outbox 줄을 오래된 순서로 최대 limit개 보내고, 전송 확인(delivery report)을 받은 줄만 sent_at을 채운다.

    Kafka에 닿지 못하면 아무 줄도 표시하지 않고 예외를 낸다(10초 안에).
    """
    deadline = time.monotonic() + BUDGET_SECONDS
    with SessionLocal() as db, db.begin():
        rows = db.execute(
            text(
                "SELECT seq, event_id, payload FROM outbox WHERE sent_at IS NULL "
                "ORDER BY seq LIMIT :n FOR UPDATE SKIP LOCKED"
            ),
            {"n": limit},
        ).all()
        if not rows:
            return 0
        ensure_topic(deadline)
        producer = Producer(
            {
                "bootstrap.servers": bootstrap(),
                "acks": "all",
                "enable.idempotence": True,
                "message.timeout.ms": 5000,
                "socket.timeout.ms": 3000,
            }
        )
        delivered: set[int] = set()

        def report(seq):
            def cb(err, _msg):
                if err is None:
                    delivered.add(seq)

            return cb

        for seq, event_id, payload in rows:
            producer.produce(
                topic_name(),
                key=payload["concert_id"].encode(),
                value=json.dumps(payload, ensure_ascii=False).encode(),
                on_delivery=report(seq),
            )
        producer.flush(max(1.0, deadline - time.monotonic()))
        if delivered:
            db.execute(
                text("UPDATE outbox SET sent_at = now() WHERE seq = ANY(:seqs)"),
                {"seqs": sorted(delivered)},
            )
        return len(delivered)
