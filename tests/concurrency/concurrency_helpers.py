"""실서버(별도 프로세스) 동시성 테스트 헬퍼."""
import asyncio
import os
import time
from collections import Counter

import httpx


def database_url():
    """psycopg 직접 연결용 DSN (SQLAlchemy 접두어 제거)."""
    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


async def _burst(base_url, requests, warm):
    n = len(requests)
    limits = httpx.Limits(max_connections=n + 10, max_keepalive_connections=n + 10)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=httpx.Timeout(120.0)) as client:
        if warm:  # 모든 연결을 먼저 열어 두어, 본 요청이 한꺼번에 출발하게 한다
            await asyncio.gather(*[client.get("/concerts") for _ in range(n)])
        go = asyncio.Event()
        results = [None] * n

        async def one(i, req):
            method, path, headers, body = req
            await go.wait()
            t = time.perf_counter()
            r = await client.request(method, path, headers=headers, json=body)
            try:
                data = r.json()
            except ValueError:
                data = None
            results[i] = {"status": r.status_code, "body": data, "text": r.text, "elapsed": time.perf_counter() - t}

        tasks = [asyncio.create_task(one(i, q)) for i, q in enumerate(requests)]
        await asyncio.sleep(0.2)  # 모든 task 가 go.wait() 에 도달할 시간
        t0 = time.perf_counter()
        go.set()
        await asyncio.gather(*tasks)
        total = time.perf_counter() - t0
    return results, total


def fire(base_url, requests, warm=True):
    """requests: [(method, path, headers, json_body)]. 동시에 보내고 (응답 목록, 전체 소요 초)를 돌려준다.
    응답: {status, body, text, elapsed}. 하나라도 연결 오류가 나면 예외(=테스트 실패)."""
    return asyncio.run(_burst(base_url, requests, warm))


def distribution(results):
    return dict(sorted(Counter(r["status"] for r in results).items()))


def timing_summary(results, total):
    xs = sorted(r["elapsed"] for r in results)
    pick = lambda q: xs[min(len(xs) - 1, int(len(xs) * q))]  # noqa: E731
    return (
        f"전체 {total:.2f}s · 응답 p50 {pick(0.5):.3f}s p95 {pick(0.95):.3f}s max {xs[-1]:.3f}s ({len(xs)}건)"
    )
