"""헤더까지 돌려주는 동시 요청 도구 (tests/concurrency 의 fire 는 응답 헤더를 버린다)."""
import asyncio
from collections import Counter

import httpx


async def _burst(base_url, requests):
    n = len(requests)
    limits = httpx.Limits(max_connections=n + 10, max_keepalive_connections=n + 10)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=httpx.Timeout(120.0)) as client:
        await asyncio.gather(*[client.get("/concerts") for _ in range(n)])  # 연결을 먼저 연다
        go = asyncio.Event()
        results = [None] * n

        async def one(i, req):
            method, path, headers, body = req
            await go.wait()
            r = await client.request(method, path, headers=headers, json=body)
            try:
                data = r.json()
            except ValueError:
                data = None
            results[i] = {"status": r.status_code, "body": data, "text": r.text, "headers": dict(r.headers)}

        tasks = [asyncio.create_task(one(i, q)) for i, q in enumerate(requests)]
        await asyncio.sleep(0.2)
        go.set()
        await asyncio.gather(*tasks)
    return results


def fire(base_url, requests):
    """[(method, path, headers, json)] 를 동시에 보내 [{status, body, text, headers}] 를 돌려준다."""
    return asyncio.run(_burst(base_url, requests))


def statuses(results):
    return dict(sorted(Counter(r["status"] for r in results).items()))


def gates(results):
    """X-Hold-Gate 분포 (헤더가 없으면 None 으로 센다)."""
    return dict(sorted(Counter(r["headers"].get("x-hold-gate") for r in results).items(), key=lambda kv: str(kv[0])))


def hold_burst(server, seat_id, n):
    """n 명(user-0..)이 같은 좌석을 동시에 홀드. 입장권은 미리 받아 둔다 (9장). server 는 Server 객체."""
    users = [f"user-{i}" for i in range(n)]
    heads = server.entry_headers_for(users)
    reqs = [("POST", f"/seats/{seat_id}/hold", heads[u], None) for u in users]
    return fire(server.base_url, reqs)
