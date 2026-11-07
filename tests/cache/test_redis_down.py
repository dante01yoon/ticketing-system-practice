"""docs/cache.md ④ 5·6: Redis 가 꺼져도 맞아야 한다 / 기존 테스트 유지.

!! 이 모듈은 `docker compose stop redis` 로 Redis 를 실제로 멈추고(끝나면 다시 켠다), 공용 DB·Redis 를 초기화하고,
   하위 pytest(tests/concurrency 등)를 돌린다. 다른 자리의 pytest·tests/infra 와 동시에 돌리지 않는다.
"""
import subprocess
import sys

from cachelib import ROOT, REDIS_URL, redis_stopped, run_server, wait_redis
from helpers import gates, hold_burst, statuses


def _pytest(*paths, timeout=900):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider", "-x"],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode, (r.stdout + r.stderr)[-3000:]


def test_concurrency_suite_passes_while_redis_is_stopped(redis_on):
    with redis_stopped():
        code, out = _pytest("tests/concurrency")
    assert code == 0, f"Redis 정지 상태에서 tests/concurrency 실패:\n{out}"


def test_seatmap_and_hold_work_without_redis(redis_on):
    with redis_stopped():
        with run_server(REDIS_URL) as s:  # Redis 없이 앱이 시작에 성공해야 한다
            import httpx

            concerts = httpx.get(f"{s.base_url}/concerts", timeout=30).json()["items"]
            url = f"{s.base_url}/concerts/{concerts[0]['id']}/seats"
            for _ in range(2):  # 캐시가 없으니 계속 MISS
                r = httpx.get(url, timeout=30)
                assert r.status_code == 200, r.text
                assert r.headers.get("X-Cache") == "MISS"
            sid = s.seat_ids()["B1"]
            results = hold_burst(s.base_url, sid, 50)
            info = f"상태 {statuses(results)} · 관문 {gates(results)}"
            assert not [r for r in results if r["status"] >= 500], info
            assert statuses(results) == {201: 1, 409: 49}, info
            assert gates(results) == {"DB": 50}, info
    wait_redis(True)  # 다시 켜졌는지 (redis_stopped 가 보장하지만 명시)


def test_existing_suites_still_pass_with_redis_on(redis_on):
    """6: tests/api · tests/contract · tests/concurrency 는 Redis 가 켜진 상태에서도 그대로 통과한다."""
    code, out = _pytest("tests/api", "tests/contract", "tests/concurrency")
    assert code == 0, f"Redis 켠 상태에서 기존 테스트 실패:\n{out}"
