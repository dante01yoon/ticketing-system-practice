"""7장 인프라 확인 픽스처 (docs/infra.md). 실제 docker compose 스택(app1~3 + nginx :8080)을 쓴다.

- 스택이 못 뜨거나 8080 이 준비되지 않으면 skip 이 아니라 실패한다.
- 이 테스트는 컨테이너를 멈추고 공유 DB(5432)를 초기화한다: 다른 자리의 테스트와 동시에 돌리지 않는다.
- app2 를 멈춘 테스트는 끝나면 반드시 다시 켜고 준비될 때까지 기다린다 (stopped_app 컨텍스트).
"""
import contextlib
import itertools
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://localhost:8080"
APPS = {"app1", "app2", "app3"}
_n = itertools.count(1)
_run = uuid.uuid4().hex[:6]


def compose(*args, check=True, timeout=600):
    r = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        pytest.fail(f"docker compose {' '.join(args)} 실패 ({r.returncode}):\n{r.stderr[-1500:]}{r.stdout[-500:]}", pytrace=False)
    return r


def fresh_user():
    """테스트·요청마다 서로 다른 사용자 (속도 제한 키가 겹치지 않게)."""
    return f"infra-{_run}-{next(_n)}"


def get_concerts(client, user=None):
    return client.get(f"{BASE}/concerts", headers={"X-User-Id": user or fresh_user()})


def wait_ready(expected_apps, timeout=90.0):
    """GET /concerts 가 200 이고 expected_apps 모두에서 응답이 올 때까지 기다린다. 실패하면 pytest.fail."""
    deadline = time.monotonic() + timeout
    seen, last = set(), None
    with httpx.Client(timeout=3.0) as c:
        while time.monotonic() < deadline:
            try:
                r = get_concerts(c)
                last = f"{r.status_code} {r.text[:120]}"
                if r.status_code == 200:
                    seen.add(r.headers.get("X-App-Id"))
                    if set(expected_apps) <= seen:
                        return
            except httpx.HTTPError as e:
                last = repr(e)
            time.sleep(0.3)
    pytest.fail(f"{timeout:.0f}초 안에 {BASE} 가 준비되지 않음 (필요: {sorted(expected_apps)}, 본 앱: {sorted(x for x in seen if x)}, 마지막: {last})", pytrace=False)


def entry_headers(client, user, concert_id):
    """9장: 대기열에서 입장권을 받아 좌석맵·홀드용 헤더를 만든다 (compose 는 큰 QUEUE_BATCH 로 바로 입장)."""
    r = client.post(f"{BASE}/concerts/{concert_id}/queue", headers={"X-User-Id": user})
    assert r.status_code in (200, 201), f"대기열 입장 실패: {r.status_code} {r.text[:200]}"
    body = r.json()
    assert body["status"] == "ADMITTED" and body["entry_token"], body
    return {"X-User-Id": user, "X-Entry-Token": body["entry_token"]}


@pytest.fixture(scope="session")
def stack():
    # 9장: docker-compose.yml 이 ${QUEUE_BATCH:-100} 식으로 호스트 값을 받는다. 기다림 없이 입장하도록 크게
    os.environ.setdefault("QUEUE_BATCH", "1000000")
    os.environ.setdefault("ENTRY_TTL_SECONDS", "3600")
    up = compose("up", "-d", "--build", "--wait", check=False)  # --wait 가 안 되는 서비스가 있어도 아래에서 직접 판정
    r = compose("ps", "--format", "{{.Service}}", check=True)
    running = set(r.stdout.split())
    missing = {"db", "app1", "app2", "app3", "nginx"} - running
    if missing:
        pytest.fail(f"compose 에 떠 있어야 할 서비스가 없음: {sorted(missing)} (실행 중: {sorted(running)})\ncompose up 종료코드 {up.returncode}:\n{up.stderr[-1200:]}", pytrace=False)
    yield
    # 스택은 내리지 않는다 (다음 실행·눈으로 확인용)


@pytest.fixture
def seeded(stack):
    """공유 DB 를 시드 상태로 되돌리고 스택이 준비될 때까지 기다린다."""
    from backend.app.seed import reset_db

    reset_db(seed=True)
    wait_ready(APPS)


@pytest.fixture
def client():
    with httpx.Client(timeout=10.0) as c:
        yield c


@contextlib.contextmanager
def stopped_app(name):
    """compose 서비스 하나를 멈춘다. 나갈 때 반드시 app 과 nginx 를 다시 켜고 3대가 모두 응답할 때까지 기다린다.
    (테스트 중 nginx 가 죽었어도 복구한다: 안 그러면 다음 테스트가 전부 깨진다)"""
    compose("stop", name)
    try:
        yield
    finally:
        compose("start", name, check=False)
        compose("up", "-d", "--no-recreate", "nginx", check=False)  # 꺼졌거나 죽었으면 다시 켠다
        r = compose("ps", "--format", "{{.Service}} {{.State}}", "nginx", check=False)
        if "running" not in r.stdout:
            compose("restart", "nginx", check=False)
        wait_ready(APPS, timeout=90.0)
