"""7장: 앱 3대 + Nginx (docs/infra.md '확인' 1~4 + 정적 확인)."""
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
import pytest

from conftest import APPS, BASE, ROOT, compose, entry_headers, fresh_user, get_concerts, stopped_app, wait_ready

NGINX_CONF = ROOT / "deploy" / "nginx" / "nginx.conf"


def _dsn():
    import os

    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


# ---------- 1. 분배 ----------
@pytest.mark.story("US-07", "분배")
def test_requests_are_spread_evenly_across_three_apps(seeded, client):
    ids = []
    for _ in range(30):
        r = get_concerts(client)  # 사용자마다 다르게: 속도 제한에 걸리지 않게
        assert r.status_code == 200, r.text
        ids.append(r.headers.get("X-App-Id"))
    counts = Counter(ids)
    assert set(counts) == APPS, f"X-App-Id 가 app1·app2·app3 이어야 함: {counts}"
    for app, n in counts.items():
        assert 8 <= n <= 12, f"{app} 가 {n}건: 고르지 않음 {counts}"


@pytest.mark.story("US-07", "X-App-Id")
def test_error_responses_from_apps_also_carry_x_app_id(seeded, client):
    r = client.get(f"{BASE}/concerts/no-such-id/seats", headers={"X-User-Id": fresh_user()})
    assert r.status_code == 404
    assert r.headers.get("X-App-Id") in APPS, dict(r.headers)
    r = client.post(f"{BASE}/bookings", json={}, headers={"X-User-Id": fresh_user()})  # 멱등 키 없음 → 400
    assert r.status_code == 400
    assert r.headers.get("X-App-Id") in APPS, dict(r.headers)


# ---------- 2. 속도 제한 ----------
@pytest.mark.story("US-07", "속도 제한")
def test_one_user_burst_gets_429_with_contract_and_others_unaffected(seeded):
    user = fresh_user()

    def hit(_):
        with httpx.Client(timeout=10.0) as c:
            return c.get(f"{BASE}/concerts", headers={"X-User-Id": user})

    with ThreadPoolExecutor(max_workers=30) as ex:
        rs = list(ex.map(hit, range(30)))
    ok = [r for r in rs if r.status_code == 200]
    limited = [r for r in rs if r.status_code == 429]
    assert len(ok) + len(limited) == 30, Counter(r.status_code for r in rs)
    assert len(limited) >= 1, "한꺼번에 30건을 보냈는데 429 가 하나도 없음"
    assert 1 <= len(ok) <= 14, f"성공 {len(ok)}건: burst(10)+α 를 넘음"
    for r in limited:
        assert r.headers.get("Retry-After") == "1", dict(r.headers)
        assert r.headers.get("Content-Type", "").startswith("application/json"), dict(r.headers)
        body = r.json()
        assert body.get("code") == "RATE_LIMITED", body
        assert isinstance(body.get("message"), str) and body["message"], body
        assert "X-App-Id" not in r.headers, "429 는 Nginx 가 직접 답하므로 X-App-Id 가 없어야 함"
    with httpx.Client(timeout=10.0) as c:
        assert get_concerts(c, fresh_user()).status_code == 200  # 다른 사용자는 영향 없음


@pytest.mark.story("US-07", "속도 제한")
def test_rate_limit_comes_before_app_validation(seeded):
    """429 는 앱의 400 보다 먼저: 같은 사용자가 멱등 키 없는 POST 를 연달아 보내면 400 이 아니라 429 가 섞인다."""
    user = fresh_user()

    def hit(_):
        with httpx.Client(timeout=10.0) as c:
            return c.post(f"{BASE}/bookings", json={"seat_id": "x"}, headers={"X-User-Id": user})

    with ThreadPoolExecutor(max_workers=30) as ex:
        codes = Counter(r.status_code for r in ex.map(hit, range(30)))
    assert codes.get(429, 0) >= 1, codes
    assert codes.get(400, 0) >= 1, codes  # burst 안 요청은 앱까지 가서 400


# ---------- 3. 장애 넘김 ----------
@pytest.mark.story("US-07", "장애 넘김")
def test_gets_survive_one_app_stopped(seeded, client):
    with stopped_app("app2"):
        start = time.monotonic()
        for i in range(10):
            r = get_concerts(client)
            assert r.status_code == 200, f"{i}번째 GET: {r.status_code} {r.text[:120]}"
            assert r.headers.get("X-App-Id") in {"app1", "app3"}, dict(r.headers)
        assert time.monotonic() - start < 10, "정지 뒤 10초 안에 모두 처리돼야 함"


# ---------- 4. 같은 키 재시도 ----------
def _until(fn, ok, tries=30):
    last = None
    for _ in range(tries):
        try:
            last = fn()
            if ok(last):
                return last
        except httpx.HTTPError as e:
            last = e
        time.sleep(0.2)
    pytest.fail(f"성공하지 못함: {last!r}" if isinstance(last, Exception) else f"성공하지 못함: {last.status_code} {last.text[:200]}", pytrace=False)


@pytest.mark.story("US-07", "멱등 재시도")
def test_same_idempotency_key_retries_make_one_booking_with_app_down(seeded, client):
    user = fresh_user()
    h = {"X-User-Id": user}
    cid = get_concerts(client).json()["items"][0]["id"]
    seat = client.get(f"{BASE}/concerts/{cid}/seats", headers=entry_headers(client, fresh_user(), cid)).json()["seats"][0]
    hold_h = entry_headers(client, user, cid)  # 9장: 입장권은 서명 검증이라 어느 앱에서나 통한다
    with stopped_app("app2"):
        # 홀드: Nginx 가 POST 를 대신 재전송하지 않으므로 5xx 면 클라이언트가 다시 보낸다
        _until(lambda: client.post(f"{BASE}/seats/{seat['id']}/hold", headers=hold_h), lambda r: r.status_code == 201)
        key = {"Idempotency-Key": f"infra-key-{user}", **h}
        first = _until(lambda: client.post(f"{BASE}/bookings", json={"seat_id": seat["id"]}, headers=key), lambda r: r.status_code in (200, 201))
        replays = [client.post(f"{BASE}/bookings", json={"seat_id": seat["id"]}, headers=key) for _ in range(4)]
    ids = {first.json()["id"]}
    for r in replays:
        assert r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}"
        ids.add(r.json()["id"])
    assert len(ids) == 1, f"성공 응답의 예매 ID 가 모두 같아야 함: {ids}"
    with psycopg.connect(_dsn()) as conn:
        n = conn.execute("SELECT count(*) FROM bookings WHERE seat_id = %s", (seat["id"],)).fetchone()[0]
    assert n == 1, f"그 좌석 bookings 행이 {n}개"


@pytest.mark.story("US-07", "POST 재전송 금지")
def test_nginx_conf_does_not_retry_non_idempotent_requests():
    assert NGINX_CONF.exists(), f"{NGINX_CONF.relative_to(ROOT)} 가 없음 (미구현)"
    text = re.sub(r"#.*", "", NGINX_CONF.read_text())  # 주석 제거
    assert "proxy_next_upstream" in text, "GET 넘김을 위해 proxy_next_upstream 이 있어야 함"
    for line in re.findall(r"proxy_next_upstream\s+[^;]*;", text):
        assert "non_idempotent" not in line, line


# ---------- 5. 앱이 멈춘 상태에서 nginx 시작·재시작 ----------
@pytest.mark.story("US-07", "nginx 재시작")
def test_nginx_restarts_while_one_app_is_stopped_then_app_rejoins(seeded, client):
    with stopped_app("app2"):
        # 설정 검사: 멈춘 앱의 이름을 못 찾아도 통과해야 한다 (host not found in upstream 이면 실패)
        t = compose("exec", "-T", "nginx", "nginx", "-t", check=False)
        assert t.returncode == 0, f"앱 정지 중 nginx -t 실패: {t.stderr[-400:]}"
        # 실제 재시작: 진입점이 살아나야 한다
        r = compose("restart", "nginx", check=False)
        assert r.returncode == 0, f"앱 정지 중 nginx 재시작 실패: {r.stderr[-400:]}"
        wait_ready({"app1", "app3"}, timeout=30.0)
        for i in range(10):
            resp = get_concerts(client)
            assert resp.status_code == 200, f"{i}번째 GET: {resp.status_code} {resp.text[:120]}"
            assert resp.headers.get("X-App-Id") in {"app1", "app3"}, dict(resp.headers)
        # 멈췄던 앱을 켜면 30초 안에 다시 요청을 받는다
        compose("start", "app2")
        wait_ready({"app1", "app2", "app3"}, timeout=30.0)
