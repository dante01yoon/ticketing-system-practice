"""11장 seed 안전성 (docs/k8s.md ④ 'seed 테스트'): seed --if-empty 는 기존(Locust) 데이터를 지우지 않는다.

로컬 테스트 DB(DATABASE_URL, 기본 localhost:5432) + reset_db 를 쓴다: 다른 자리의 pytest·tests/infra 와 동시에 돌리지 않는다.
클러스터 DB 는 쓰지 않는다. backend 는 함수 안에서 import 한다.
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _dsn():
    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _q(sql, *args):
    with psycopg.connect(_dsn()) as conn:
        return conn.execute(sql, args).fetchall()


def _counts():
    return _q("SELECT (SELECT count(*) FROM concerts), (SELECT count(*) FROM seats)")[0]


def _snapshot():
    return {
        "concerts": _q("SELECT id, title FROM concerts ORDER BY id"),
        "seats": _q("SELECT id, concert_id, number, status FROM seats ORDER BY id"),
        "bookings": _q("SELECT id, seat_id, user_id, status, expires_at FROM bookings ORDER BY id"),
        "keys": _q("SELECT user_id, key, seat_id, response_status, response_body::text FROM idempotency_keys ORDER BY user_id, key"),
    }


def _cli(*args, timeout=120):
    return subprocess.run([sys.executable, "-m", "backend.app.seed", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=os.environ.copy())


@pytest.fixture(autouse=True)
def _redis_ok(monkeypatch):
    monkeypatch.setenv("QUEUE_BATCH", "100000")


def _make_booking(confirm):
    """시드된 DB 에서 좌석 하나를 홀드하고 예매(PENDING, confirm 이면 CONFIRMED)까지 만든다. 입장권은 서명으로 직접 발급."""
    from backend.app.main import create_app
    from backend.app.queue import issue_entry_token

    client = TestClient(create_app())
    cid, seat_id = _q("SELECT concert_id, id FROM seats ORDER BY number LIMIT 1")[0]
    user = f"seedtest-{uuid.uuid4().hex[:8]}"
    token = issue_entry_token(user, cid, datetime.now(timezone.utc) + timedelta(hours=1))
    h = {"X-User-Id": user, "X-Entry-Token": token}
    r = client.post(f"/seats/{seat_id}/hold", headers=h)
    assert r.status_code == 201, r.text
    r = client.post("/bookings", json={"seat_id": seat_id}, headers={"X-User-Id": user, "Idempotency-Key": "key-" + user})
    assert r.status_code == 201, r.text
    booking = r.json()
    if confirm:
        r = client.post("/payments/callback", json={"booking_id": booking["id"], "result": "SUCCEEDED"})
        assert r.status_code == 200 and r.json()["status"] == "CONFIRMED", r.text
    return booking["id"]


# ---------- 빈 DB ----------
@pytest.mark.story("US-07", "seed 빈 DB")
def test_seed_if_empty_seeds_once_on_empty_db():
    from backend.app.seed import reset_db, seed_if_empty

    reset_db(seed=False)
    assert _counts() == (0, 0)
    assert seed_if_empty() is True
    assert _counts() == (1, 100)
    before = _snapshot()
    assert seed_if_empty() is False
    assert _counts() == (1, 100)
    assert _snapshot() == before


# ---------- 데이터가 있는 DB ----------
@pytest.mark.story("US-07", "seed 보존")
@pytest.mark.parametrize("confirm", [False, True], ids=["pending", "confirmed"])
def test_seed_if_empty_preserves_existing_data(confirm):
    from backend.app.seed import reset_db, seed_if_empty

    reset_db(seed=True)
    bid = _make_booking(confirm)
    before = _snapshot()
    assert before["bookings"] and before["keys"], "사전 조건: 예매와 멱등 키가 있어야 함"
    assert any(s[3] in ("HELD", "SOLD") for s in before["seats"])
    assert seed_if_empty() is False
    after = _snapshot()
    assert after == before, "seed_if_empty 가 기존 데이터를 바꿨다"
    assert [b[0] for b in after["bookings"]] == [bid]


# ---------- CLI 분기 ----------
@pytest.mark.story("US-07", "seed CLI")
def test_cli_if_empty_does_not_wipe_existing_data():
    from backend.app.seed import reset_db

    reset_db(seed=True)
    _make_booking(confirm=True)
    before = _snapshot()
    r = _cli("--if-empty")
    assert r.returncode == 0, r.stderr[-800:]
    assert _snapshot() == before, "--if-empty 가 데이터를 지웠다 (reset_db 로 잘못 연결?)"


@pytest.mark.story("US-07", "seed CLI")
def test_cli_if_empty_seeds_an_empty_db():
    from backend.app.seed import reset_db

    reset_db(seed=False)
    r = _cli("--if-empty")
    assert r.returncode == 0, r.stderr[-800:]
    assert _counts() == (1, 100)
    assert _cli("--if-empty").returncode == 0
    assert _counts() == (1, 100)


# ---------- wait-schema ----------
@pytest.mark.story("US-07", "wait-schema")
def test_wait_for_schema_true_on_migrated_db():
    from backend.app import seed
    from backend.app.seed import reset_db

    reset_db(seed=True)
    assert hasattr(seed, "wait_for_schema"), "backend.app.seed.wait_for_schema 가 없음 (미구현)"
    assert seed.wait_for_schema(timeout=5) is True


@pytest.mark.story("US-07", "wait-schema")
def test_cli_wait_schema_exits_zero_and_does_not_touch_data():
    from backend.app.seed import reset_db

    reset_db(seed=True)
    _make_booking(confirm=False)
    before = _snapshot()
    r = _cli("--wait-schema", timeout=60)
    assert r.returncode == 0, f"--wait-schema 종료코드 {r.returncode}: {r.stderr[-600:]}"
    assert _snapshot() == before, "--wait-schema 는 데이터를 건드리면 안 됨(reset_db 로 잘못 연결?)"
