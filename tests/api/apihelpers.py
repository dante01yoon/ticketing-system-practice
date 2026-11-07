"""API 테스트 헬퍼 (Api 래퍼, 시계, 오류 단언). 픽스처는 conftest.py."""
from concurrent.futures import ThreadPoolExecutor
import itertools
from datetime import datetime, timedelta, timezone
import threading

from starlette.testclient import TestClient

T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
HOLD_TTL = timedelta(minutes=5)
VIEWER = "viewer"  # 좌석맵을 구경만 하는 기본 사용자 (9장부터 좌석맵에도 X-User-Id + 입장권이 필요)
AUTO = object()  # confirm(key=AUTO): 호출마다 새 멱등 키를 만든다. key=None 이면 헤더를 보내지 않는다.
SEAT_NUMBERS = [f"{row}{n}" for row in "ABCDEFGHIJ" for n in range(1, 11)]


class Clock:
    def __init__(self):
        self.now = T0

    def advance(self, **kw):
        self.now = self.now + timedelta(**kw)


class Api:
    def __init__(self, app, clock):
        self.app = app
        self.clock = clock
        self.client = TestClient(app)
        self.concert_id = None
        self._seat_ids = {}
        self._keys = itertools.count(1)
        self._entries = {}  # (user, concert_id) -> (entry_token, entry_expires_at)

    # --- 요청 ---
    @staticmethod
    def _h(user):
        return {} if user is None else {"X-User-Id": user}

    # --- 입장권 (9장, docs/queue.md): 좌석맵·홀드를 부르기 전에 대기열에서 받아 붙인다 ---
    def entry(self, user, concert_id=None):
        """user 의 유효한 입장권. 가짜 시계 기준으로 만료됐거나 없으면 POST /concerts/{id}/queue 로 새로 받는다.
        사용자가 없거나(None·빈 값·NUL) 공연이 없으면 None (입장권 없이 보내 서버의 401·404 를 그대로 본다)."""
        cid = concert_id or self.concert_id
        if user is None or not user.strip() or "\x00" in user or cid is None:
            return None
        cached = self._entries.get((user, cid))
        if cached and self.clock.now < cached[1]:
            return cached[0]
        r = TestClient(self.app).post(f"/concerts/{cid}/queue", headers={"X-User-Id": user})
        if r.status_code == 404 and cid != self.concert_id:
            return None  # 일부러 없는 공연을 부른 호출: 서버의 404 를 그대로 보려고 입장권 없이 보낸다
        if r.status_code == 503 and r.json().get("code") == "QUEUE_UNAVAILABLE":
            # Redis 가 없을 때만: 서버와 같은 서명의 입장권을 직접 발급한다 (docs/queue.md ④ 테스트용 접점)
            from backend.app.queue import issue_entry_token

            expires = self.clock.now + timedelta(seconds=120)
            token = issue_entry_token(user, cid, expires)
            self._entries[(user, cid)] = (token, expires)
            return token
        # 그 밖의 실패(기본 공연의 대기열 404 포함)는 폴백 없이 실패로 드러낸다
        assert r.status_code in (200, 201), f"대기열 입장 실패: {r.status_code} {r.text}"
        body = r.json()
        assert body["status"] == "ADMITTED" and body["entry_token"], f"헬퍼는 바로 입장할 수 있어야 함(QUEUE_BATCH 를 크게): {body}"
        self._entries[(user, cid)] = (body["entry_token"], parse_dt(body["entry_expires_at"]))
        return body["entry_token"]

    def entry_headers(self, user, concert_id=None):
        """X-User-Id + X-Entry-Token 헤더 (사용자가 없으면 빈 dict, 입장권을 못 받으면 X-User-Id 만)."""
        headers = self._h(user)
        token = self.entry(user, concert_id)
        if token is not None:
            headers["X-Entry-Token"] = token
        return headers

    def concerts(self, user=None):
        return self.client.get("/concerts", headers=self._h(user))

    def seatmap(self, concert_id=None, user=VIEWER):
        cid = concert_id or self.concert_id
        return self.client.get(f"/concerts/{cid}/seats", headers=self.entry_headers(user, cid))

    def hold(self, seat_id, user="u1", client=None):
        return (client or self.client).post(f"/seats/{seat_id}/hold", headers=self.entry_headers(user))

    def confirm(self, seat_id, user="u1", client=None, key=AUTO):
        """POST /bookings. key 기본값은 호출마다 새 멱등 키, None 이면 Idempotency-Key 헤더 없음."""
        headers = self._h(user)
        if key is AUTO:
            key = f"auto-key-{next(self._keys)}"
        if key is not None:
            headers["Idempotency-Key"] = key
        return (client or self.client).post("/bookings", json={"seat_id": seat_id}, headers=headers)

    def callback(self, booking_id, result="SUCCEEDED", client=None, **extra):
        """POST /payments/callback (모의 결제사). X-User-Id 없이 보낸다. extra 는 본문에 그대로 얹는다."""
        body = {"booking_id": booking_id, "result": result, **extra}
        return (client or self.client).post("/payments/callback", json=body)

    def get_booking(self, booking_id, user="u1", client=None):
        return (client or self.client).get(f"/bookings/{booking_id}", headers=self._h(user))

    def buy(self, seat_id, user="u1"):
        """홀드 → 확정 요청 → 성공 콜백으로 좌석을 SOLD 로 만든다. CONFIRMED 예매 본문을 돌려준다."""
        assert self.hold(seat_id, user).status_code == 201
        r = self.confirm(seat_id, user)
        assert r.status_code == 201, r.text
        r = self.callback(r.json()["id"], "SUCCEEDED")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CONFIRMED", r.text
        return r.json()

    def pending(self, seat_id, user="u1", key=AUTO):
        """홀드 → 확정 요청까지 하고 PENDING 예매 본문을 돌려준다."""
        assert self.hold(seat_id, user).status_code == 201
        r = self.confirm(seat_id, user, key=key)
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "PENDING", r.text
        return r.json()

    def booking_statuses(self, seat_id):
        """DB 의 bookings 에서 이 좌석 예매들의 status 목록 (정렬)."""
        import psycopg

        with psycopg.connect(_dsn()) as conn:
            rows = conn.execute("SELECT status FROM bookings WHERE seat_id = %s", (seat_id,)).fetchall()
        return sorted(r[0] for r in rows)

    # --- 공연·좌석 찾기 (ID 하드코딩 금지) ---
    def discover(self):
        r = self.concerts()
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert items, "시드 공연이 없습니다 (reset_db(seed=True))"
        self.concert_id = items[0]["id"]
        r = self.seatmap()
        assert r.status_code == 200, r.text
        self._seat_ids = {s["number"]: s["id"] for s in r.json()["seats"]}
        return self

    def sid(self, number):
        return self._seat_ids[number]

    def status(self, number):
        """좌석맵을 다시 조회해 해당 좌석 번호의 상태를 돌려준다."""
        seats = {s["number"]: s["status"] for s in self.seatmap().json()["seats"]}
        return seats[number]

    # --- 동시 요청 ---
    def parallel(self, fn, n):
        """n 개 스레드가 장벽에서 동시에 출발해 fn(client, i) 를 실행. 응답 목록을 돌려준다."""
        barrier = threading.Barrier(n)

        def work(i):
            c = TestClient(self.app)
            barrier.wait(timeout=30)
            return fn(c, i)

        with ThreadPoolExecutor(max_workers=n) as ex:
            return [f.result() for f in [ex.submit(work, i) for i in range(n)]]


def assert_error(resp, status, code):
    assert resp.status_code == status, f"{resp.status_code} {resp.text}"
    body = resp.json()
    assert "detail" not in body, f"FastAPI 기본 오류 본문이 노출됨: {body}"
    assert body.get("code") == code, body
    assert isinstance(body.get("message"), str) and body["message"], body


def parse_dt(value):
    dt = datetime.fromisoformat(value)
    assert dt.tzinfo is not None, f"시간대 정보가 없는 일시: {value}"
    return dt


# ---------- 잠금 대기 중 시계 이동 (리뷰 지적 1) ----------
def _dsn():
    import os

    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def request_blocked_by_row_lock(api, seat_id, send, clock_at_send, clock_after_wait, timeout=10.0):
    """별도 DB 연결로 좌석 행을 FOR UPDATE 잠근 채, 시계를 clock_at_send 로 두고 send(client) 를 스레드로 시작한다.
    그 요청이 실제로 잠금 대기 중임을 pg_stat_activity 로 확인한 뒤 시계를 clock_after_wait 로 옮기고 잠금을 푼다.
    요청의 응답을 돌려준다. 요청이 잠금을 기다리지 않으면 AssertionError (잠금 없이 처리하는 구현)."""
    import time

    import psycopg

    result = {}
    conn = psycopg.connect(_dsn())  # 좌석 행을 잠그는 연결 (트랜잭션 유지)
    # 대기 확인은 별도 autocommit 연결로: 트랜잭션 안에서는 pg_stat_activity 스냅샷이 캐시돼 뒤늦게 생긴 대기자가 안 보인다
    monitor = psycopg.connect(_dsn(), autocommit=True)
    thread = None
    try:
        conn.execute("SELECT id FROM seats WHERE id = %s FOR UPDATE", (seat_id,))
        api.clock.now = clock_at_send

        def run():
            try:
                result["resp"] = send(TestClient(api.app))
            except BaseException as e:  # noqa: BLE001 - 스레드 예외를 테스트로 전달
                result["exc"] = e

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        deadline = time.monotonic() + timeout
        waiting = 0
        while time.monotonic() < deadline and "resp" not in result and "exc" not in result:
            waiting = monitor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'"
            ).fetchone()[0]
            if waiting:
                break
            time.sleep(0.05)
        assert waiting, f"요청이 좌석 행 잠금을 기다리지 않았습니다 (결과: {result.get('resp') or result.get('exc')})"
        api.clock.now = clock_after_wait
    finally:
        conn.rollback()
        conn.close()
        monitor.close()
        if thread is not None:
            thread.join(timeout=timeout)
    assert not thread.is_alive(), "잠금 해제 후에도 요청이 끝나지 않았습니다"
    if "exc" in result:
        raise result["exc"]
    return result["resp"]
