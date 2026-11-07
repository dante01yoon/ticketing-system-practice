"""실서버(별도 프로세스) 테스트용 입장권 도구 (9장, docs/queue.md).

실서버 테스트는 좌석맵·홀드 전에 `POST /concerts/{id}/queue` 로 입장권을 받아야 한다.
서버를 띄울 때 큰 QUEUE_BATCH·ENTRY_TTL_SECONDS 를 env 로 넘겨 기다림 없이 바로 입장하게 한다 (QUEUE_ENV).
공연 ID 는 reset_db 때마다 바뀌므로 입장권을 캐시하지 않는다: 테스트 안에서 받는다.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

QUEUE_ENV = {"QUEUE_BATCH": "1000000", "ENTRY_TTL_SECONDS": "3600", "QUEUE_INTERVAL_SECONDS": "3600"}


def _issue_offline(user, concert_id):
    """대기열이 503 일 때만: 서버와 같은 서명의 입장권을 직접 발급 (docs/queue.md ④ 테스트용 접점)."""
    from backend.app.queue import issue_entry_token

    return issue_entry_token(user, concert_id, datetime.now(timezone.utc) + timedelta(hours=1))


async def _join_all(base_url, concert_id, users, limit=100):
    sem = asyncio.Semaphore(limit)
    limits = httpx.Limits(max_connections=limit + 10, max_keepalive_connections=limit + 10)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=httpx.Timeout(120.0)) as client:

        async def one(user):
            async with sem:
                r = await client.post(f"/concerts/{concert_id}/queue", headers={"X-User-Id": user})
            if r.status_code == 503 and r.json().get("code") == "QUEUE_UNAVAILABLE":
                return user, _issue_offline(user, concert_id)  # Redis 가 없을 때만 (그 밖의 실패는 폴백 없이 실패)
            assert r.status_code in (200, 201), f"{user}: 대기열 입장 실패 {r.status_code} {r.text[:200]}"
            body = r.json()
            assert body["status"] == "ADMITTED" and body["entry_token"], f"{user}: 바로 입장하지 못함 {body}"
            return user, body["entry_token"]

        return dict(await asyncio.gather(*[one(u) for u in users]))


class EntryMixin:
    """base_url 이 있는 Server 객체에 입장권 메서드를 더한다."""

    def concert_id(self):
        concerts = httpx.get(f"{self.base_url}/concerts", timeout=30).json()["items"]
        assert concerts, "시드 공연이 없습니다"
        return concerts[0]["id"]

    def tokens(self, users, concert_id=None):
        """{사용자: 입장권}. 모두 바로 입장해야 한다."""
        return asyncio.run(_join_all(self.base_url, concert_id or self.concert_id(), list(users)))

    def entry_headers(self, user, concert_id=None):
        return {"X-User-Id": user, "X-Entry-Token": self.tokens([user], concert_id)[user]}

    def entry_headers_for(self, users, concert_id=None):
        return {u: {"X-User-Id": u, "X-Entry-Token": t} for u, t in self.tokens(users, concert_id).items()}

    def seatmap(self, headers=None):
        """좌석 목록. headers 를 주지 않으면 'viewer' 로 입장권을 받아 쓴다."""
        cid = self.concert_id()
        headers = headers or self.entry_headers("viewer", cid)
        r = httpx.get(f"{self.base_url}/concerts/{cid}/seats", headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        return r.json()["seats"]

    def seat_ids(self, headers=None):
        return {s["number"]: s["id"] for s in self.seatmap(headers)}
