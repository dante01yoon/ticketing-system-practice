"""홀드 동시성 벤치: uvicorn 워커를 띄워 동시 홀드를 쏘고 응답 시간·상태 분포를 잰다.
측정 대상은 **현재 구현**(좌석 행 SELECT ... FOR UPDATE) 하나뿐이다. 방식 비교 결과는 docs/concurrency.md.

  uv run python -m backend.bench.hold_bench --runs 5

케이스
  same   : 같은 좌석에 서로 다른 사용자 500명 (경합)
  spread : 서로 다른 좌석 100석에 사용자 500명 → 좌석당 5명 (경합 약함)
  unique : 시드 좌석 100석 각각에 1명씩 (경합 없음, 100건)
tests/concurrency/concurrency_helpers.py 의 fire 를 그대로 쓴다 (테스트 파일은 수정하지 않는다).
DATABASE_URL 환경변수를 서버·reset_db 모두 사용한다 (reset_db 가 데이터를 지우므로 전용 DB 에서 돌릴 것).
"""
import argparse
import os
import socket
import statistics
import subprocess
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "concurrency"))
from concurrency_helpers import distribution, fire  # noqa: E402


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _raise_fd_limit(want=8192):
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = want if hard == resource.RLIM_INFINITY else min(want, hard)
    if soft < target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))


def start_server(workers):
    from backend.app.seed import reset_db

    reset_db(seed=True)
    port = _free_port()
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--workers", str(workers), "--port", str(port),
         "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/concerts", timeout=2).status_code == 200:
                return proc, base
        except httpx.HTTPError:
            time.sleep(0.3)
    proc.kill()
    raise SystemExit("uvicorn 이 준비되지 않음")


def seat_ids(base):
    cid = httpx.get(f"{base}/concerts", timeout=30).json()["items"][0]["id"]
    seats = httpx.get(f"{base}/concerts/{cid}/seats", timeout=30).json()["seats"]
    return [s["id"] for s in seats]


def build(case, ids, n):
    if case == "same":
        return [("POST", f"/seats/{ids[0]}/hold", {"X-User-Id": f"user-{i}"}, None) for i in range(n)]
    if case == "spread":
        return [("POST", f"/seats/{ids[i % 100]}/hold", {"X-User-Id": f"user-{i}"}, None) for i in range(n)]
    return [("POST", f"/seats/{ids[i]}/hold", {"X-User-Id": f"user-{i}"}, None) for i in range(min(n, len(ids)))]


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def run(case, runs, workers, n):
    rows = []
    for _ in range(runs):
        proc, base = start_server(workers)  # 매번 새 서버·초기화된 DB
        try:
            results, total = fire(base, build(case, seat_ids(base), n))
        finally:
            proc.terminate()
            proc.wait(timeout=20)
        el = [r["elapsed"] for r in results]
        d = distribution(results)
        rows.append({
            "p50": pct(el, 0.5), "p95": pct(el, 0.95), "max": max(el), "total": total,
            "201": d.get(201, 0), "409": d.get(409, 0), "5xx": sum(v for k, v in d.items() if k >= 500),
            "other": sum(v for k, v in d.items() if k not in (201, 409) and k < 500),
        })
    return rows


def fmt(rows, key, scale=1000):
    xs = [r[key] * scale for r in rows]
    return f"{statistics.median(xs):.0f} ({min(xs):.0f}–{max(xs):.0f})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="+", default=["same", "spread", "unique"])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--requests", type=int, default=500)
    a = ap.parse_args()
    _raise_fd_limit()
    print("| case | 구현 | p50 ms | p95 ms | max ms | 전체 ms | 201 | 409 | 5xx | 기타 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for case in a.cases:
        rows = run(case, a.runs, a.workers, a.requests)
        c = lambda k: sorted({r[k] for r in rows})  # noqa: E731
        print(f"| {case} | FOR UPDATE | {fmt(rows,'p50')} | {fmt(rows,'p95')} | {fmt(rows,'max')} | "
              f"{fmt(rows,'total')} | {c('201')} | {c('409')} | {c('5xx')} | {c('other')} |", flush=True)


if __name__ == "__main__":
    main()
