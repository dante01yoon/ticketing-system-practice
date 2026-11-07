"""11장 스모크 (docs/k8s.md ④). 클러스터에 닿지 않거나 네임스페이스 ticketing 이 없으면 skip.

- reset_db 금지: DB 를 지우지 않는다 (사람의 Locust 데이터가 있을 수 있다). 매번 새 사용자 ID.
- 로컬 5432 DB·compose 스택은 건드리지 않는다.
"""
import contextlib
import shutil
import socket
import subprocess
import time
import uuid

import httpx
import pytest

import k8slib

NS = "ticketing"


def kubectl(*args, timeout=30):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=timeout)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _user():
    return f"k8s-{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def cluster():
    if not shutil.which("kubectl"):
        pytest.skip("kubectl 이 없음")
    try:
        r = kubectl("get", "namespace", NS, "-o", "name", timeout=20)
    except subprocess.TimeoutExpired:
        pytest.skip("클러스터에 닿지 않음 (kubectl 시간 초과)")
    if r.returncode != 0:
        pytest.skip(f"클러스터에 닿지 않거나 네임스페이스 {NS} 가 없음: {(r.stderr or r.stdout).strip()[:200]}")
    w = kubectl("-n", NS, "wait", "--for=condition=available", "deploy/app", "--timeout=180s", timeout=200)
    if w.returncode != 0:
        pytest.fail(f"deploy/app 이 180초 안에 준비되지 않음:\n{w.stderr[-800:]}{w.stdout[-400:]}", pytrace=False)
    yield


@pytest.fixture(scope="module")
def base_url(cluster):
    port = _free_port()
    pf = subprocess.Popen(["kubectl", "-n", NS, "port-forward", "svc/app", f"{port}:80"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        last = None
        while time.monotonic() < deadline:
            if pf.poll() is not None:
                pytest.fail(f"port-forward 가 종료됨: {pf.stdout.read()[-500:]}", pytrace=False)
            try:
                if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError as e:
                last = repr(e)
            time.sleep(0.5)
        else:
            pytest.fail(f"port-forward 로 /health 에 닿지 않음: {last}", pytrace=False)
        yield url
    finally:
        pf.terminate()
        with contextlib.suppress(Exception):
            pf.wait(timeout=10)
        if pf.poll() is None:
            pf.kill()


def _app_pod_names():
    docs = k8slib.load_docs()
    sel = k8slib.find(docs, "Deployment", "app")["spec"]["selector"]["matchLabels"]
    label = ",".join(f"{k}={v}" for k, v in sel.items())
    r = kubectl("-n", NS, "get", "pods", "-l", label, "-o", "jsonpath={.items[*].metadata.name}")
    assert r.returncode == 0, r.stderr
    return set(r.stdout.split())


@pytest.mark.story("US-07", "k8s 스모크")
def test_full_booking_flow_through_service(base_url):
    pods = _app_pod_names()
    assert pods, "실행 중인 app Pod 가 없음"
    with httpx.Client(base_url=base_url, timeout=30) as c:
        r = c.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert r.headers.get("X-App-Id") in pods, (r.headers.get("X-App-Id"), pods)

        r = c.get("/concerts")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert items, "공연이 없음 (db-init 시드가 돌았는지 확인)"
        cid = items[0]["id"]

        user = _user()
        r = c.post(f"/concerts/{cid}/queue", headers={"X-User-Id": user})
        assert r.status_code in (200, 201), r.text
        q = r.json()
        assert q["status"] == "ADMITTED" and q["entry_token"], q
        h = {"X-User-Id": user, "X-Entry-Token": q["entry_token"]}

        r = c.get(f"/concerts/{cid}/seats", headers=h)
        assert r.status_code == 200, r.text
        free = [s for s in r.json()["seats"] if s["status"] == "AVAILABLE"]
        assert free, "AVAILABLE 좌석이 없음: 사람의 부하 테스트로 다 팔렸는지 확인 (reset_db 는 쓰지 않는다)"

        seat = None
        for s in free[:20]:  # 다른 사용자와 겹치면 다음 좌석
            r = c.post(f"/seats/{s['id']}/hold", headers=h)
            if r.status_code == 201:
                seat = s
                break
            assert r.status_code == 409, r.text
        assert seat, "20개 좌석을 시도했지만 홀드하지 못함"

        r = c.post("/bookings", json={"seat_id": seat["id"]}, headers={"X-User-Id": user, "Idempotency-Key": uuid.uuid4().hex})
        assert r.status_code == 201, r.text
        b = r.json()
        assert b["status"] == "PENDING", b

        r = c.post("/payments/callback", json={"booking_id": b["id"], "result": "SUCCEEDED"})
        assert r.status_code == 200, r.text

        r = c.get(f"/bookings/{b['id']}", headers={"X-User-Id": user})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CONFIRMED", r.json()
        assert r.headers.get("X-App-Id") in pods


@pytest.mark.story("US-07", "k8s 스모크")
def test_hpa_reports_current_cpu_within_180s(cluster):
    deadline = time.monotonic() + 180
    last = None
    while time.monotonic() < deadline:
        r = kubectl("-n", NS, "get", "hpa", "app", "-o", "jsonpath={.status.currentMetrics[?(@.type=='Resource')].resource.current.averageUtilization}")
        last = (r.stdout or r.stderr).strip()
        if r.returncode == 0 and last.isdigit():
            return
        time.sleep(5)
    desc = kubectl("-n", NS, "get", "hpa", "app").stdout
    pytest.fail(f"180초 안에 HPA 현재 CPU 값이 채워지지 않음 (metrics-server 확인): {last!r}\n{desc}", pytrace=False)
