"""실서버(uvicorn 워커 여러 개) + PostgreSQL 에서 진짜로 겹치는 요청이 이중 홀드·이중 예매를 만들지 않는지 검증."""
import psycopg
import pytest

from concurrency_helpers import database_url, distribution, fire, timing_summary

HOLDERS = 500
CONFIRMS = 20


@pytest.mark.story("US-03", "AC-8")
def test_500_users_hold_same_seat_exactly_one_wins(server):
    sid = server.seat_ids()["A1"]
    reqs = [("POST", f"/seats/{sid}/hold", {"X-User-Id": f"user-{i}"}, None) for i in range(HOLDERS)]
    results, total = fire(server.base_url, reqs)
    dist = distribution(results)
    info = f"워커 {server.workers} · 상태 분포 {dist} · {timing_summary(results, total)}"
    print("\n[hold]", info)

    assert dist == {201: 1, 409: HOLDERS - 1}, f"201 정확히 1건, 409 {HOLDERS - 1}건이어야 함: {info}"
    losers = [r for r in results if r["status"] == 409]
    bad = [r["text"][:120] for r in losers if (r["body"] or {}).get("code") != "SEAT_ALREADY_HELD"]
    assert not bad, f"409 code 가 SEAT_ALREADY_HELD 가 아님: {bad[:3]} / {info}"
    assert server.seat_status(sid) == "HELD", info


@pytest.mark.story("US-04", "AC-7")
def test_20_concurrent_confirms_sell_seat_once(server):
    sid = server.seat_ids()["A2"]  # 다른 테스트와 다른 좌석
    hdr = {"X-User-Id": "buyer-u1"}
    held, _ = fire(server.base_url, [("POST", f"/seats/{sid}/hold", hdr, None)], warm=False)
    assert held[0]["status"] == 201, held[0]["text"]

    reqs = [("POST", "/bookings", hdr, {"seat_id": sid}) for _ in range(CONFIRMS)]
    results, total = fire(server.base_url, reqs)
    dist = distribution(results)
    info = f"워커 {server.workers} · 상태 분포 {dist} · {timing_summary(results, total)}"
    print("\n[confirm]", info)

    assert dist == {201: 1, 409: CONFIRMS - 1}, f"201 정확히 1건, 409 {CONFIRMS - 1}건이어야 함: {info}"
    bad = [r["text"][:120] for r in results if r["status"] == 409 and (r["body"] or {}).get("code") != "SEAT_ALREADY_SOLD"]
    assert not bad, f"409 code 가 SEAT_ALREADY_SOLD 가 아님: {bad[:3]} / {info}"

    with psycopg.connect(database_url()) as conn:
        count = conn.execute("SELECT count(*) FROM bookings WHERE seat_id = %s", (sid,)).fetchone()[0]
    assert count == 1, f"bookings 에 이 좌석 행이 {count}개 (기대 1): {info}"
    assert server.seat_status(sid) == "SOLD", info
