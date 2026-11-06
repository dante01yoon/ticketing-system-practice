"""API 테스트 헬퍼 (Api 래퍼, 시계, 오류 단언). 픽스처는 conftest.py."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

from starlette.testclient import TestClient

T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
HOLD_TTL = timedelta(minutes=5)
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

    # --- 요청 ---
    @staticmethod
    def _h(user):
        return {} if user is None else {"X-User-Id": user}

    def concerts(self, user=None):
        return self.client.get("/concerts", headers=self._h(user))

    def seatmap(self, concert_id=None, user=None):
        return self.client.get(f"/concerts/{concert_id or self.concert_id}/seats", headers=self._h(user))

    def hold(self, seat_id, user="u1", client=None):
        return (client or self.client).post(f"/seats/{seat_id}/hold", headers=self._h(user))

    def confirm(self, seat_id, user="u1", client=None):
        return (client or self.client).post("/bookings", json={"seat_id": seat_id}, headers=self._h(user))

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
    conn = psycopg.connect(_dsn())
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
            waiting = conn.execute(
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
        if thread is not None:
            thread.join(timeout=timeout)
    assert not thread.is_alive(), "잠금 해제 후에도 요청이 끝나지 않았습니다"
    if "exc" in result:
        raise result["exc"]
    return result["resp"]
