"""9장 대기열 테스트 공용 도구. 픽스처는 conftest.py. 계약: docs/queue.md.

Redis 키 이름·입장권 형식은 가정하지 않는다. 입장권은 받은 값을 그대로 쓰거나 일부 글자만 바꾼다.
"""
import asyncio
import sys
from pathlib import Path

import httpx
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "cache"))
sys.path.insert(0, str(ROOT / "tests"))

from apihelpers import T0, Clock, _dsn, parse_dt  # noqa: E402,F401


class Q:
    """가짜 시계 인프로세스 앱 위의 얇은 래퍼. 대기열 응답을 그대로 돌려준다 (서버 예외는 500 응답으로 받는다)."""

    def __init__(self, app, clock):
        self.app, self.clock = app, clock
        self.c = TestClient(app, raise_server_exceptions=False)
        self.concert_id = self.c.get("/concerts").json()["items"][0]["id"]

    @staticmethod
    def _h(user, token=None):
        h = {} if user is None else {"X-User-Id": user}
        if token is not None:
            h["X-Entry-Token"] = token
        return h

    def join(self, user, concert_id=None):
        return self.c.post(f"/concerts/{concert_id or self.concert_id}/queue", headers=self._h(user))

    def me(self, user, concert_id=None):
        return self.c.get(f"/concerts/{concert_id or self.concert_id}/queue/me", headers=self._h(user))

    def seatmap(self, user, token=None, concert_id=None):
        return self.c.get(f"/concerts/{concert_id or self.concert_id}/seats", headers=self._h(user, token))

    def hold(self, seat_id, user, token=None):
        return self.c.post(f"/seats/{seat_id}/hold", headers=self._h(user, token))

    def confirm(self, seat_id, user, key):
        return self.c.post("/bookings", json={"seat_id": seat_id}, headers={**self._h(user), "Idempotency-Key": key})

    def callback(self, booking_id, result="SUCCEEDED"):
        return self.c.post("/payments/callback", json={"booking_id": booking_id, "result": result})

    # --- DB 직접 확인 (좌석 원본 상태: 요청이 좌석을 바꿨는지) ---
    def seat_id(self, number, concert_id=None):
        import psycopg

        with psycopg.connect(_dsn()) as conn:
            return conn.execute(
                "SELECT id FROM seats WHERE concert_id = %s AND number = %s", (concert_id or self.concert_id, number)
            ).fetchone()[0]

    def seat_raw_status(self, seat_id):
        import psycopg

        with psycopg.connect(_dsn()) as conn:
            return conn.execute("SELECT status FROM seats WHERE id = %s", (seat_id,)).fetchone()[0]

    # --- 입장 도우미 ---
    def admit(self, user, concert_id=None):
        """줄 서서 바로 입장한 사용자의 QueueStatus 본문 (입장하지 못하면 실패)."""
        r = self.join(user, concert_id)
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["status"] == "ADMITTED" and body["entry_token"], body
        return body


def tamper(token, where):
    """받은 입장권의 글자 하나를 다른 글자로 바꾼다 (형식은 가정하지 않는다)."""
    i = {"first": 0, "middle": len(token) // 2, "last": len(token) - 1}[where]
    c = "A" if token[i] != "A" else "B"
    return token[:i] + c + token[i + 1 :]


# ---------- 실서버 대량 입장 ----------
async def _join_burst(base_url, concert_id, users, limit=200):
    sem = asyncio.Semaphore(limit)
    limits = httpx.Limits(max_connections=limit + 10, max_keepalive_connections=limit + 10)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=httpx.Timeout(120.0)) as client:
        await asyncio.gather(*[client.get("/concerts") for _ in range(min(len(users), limit))])  # 연결을 먼저 연다
        go = asyncio.Event()

        async def one(user):
            await go.wait()
            async with sem:
                r = await client.post(f"/concerts/{concert_id}/queue", headers={"X-User-Id": user})
            try:
                body = r.json()
            except ValueError:
                body = None
            return {"user": user, "status": r.status_code, "body": body, "text": r.text[:200]}

        tasks = [asyncio.create_task(one(u)) for u in users]
        await asyncio.sleep(0.2)
        go.set()
        return await asyncio.gather(*tasks)


def join_burst(base_url, concert_id, users):
    """users(중복 가능)가 동시에 POST /concerts/{id}/queue. [{user, status, body, text}] 를 돌려준다."""
    return asyncio.run(_join_burst(base_url, concert_id, list(users)))
