"""실서버(uvicorn 워커 여러 개) + PostgreSQL 에서 진짜로 겹치는 요청이 이중 홀드·이중 예매를 만들지 않는지 검증."""
import json

import psycopg
import pytest

from concurrency_helpers import database_url, distribution, fire, timing_summary

HOLDERS = 500
CONFIRMS = 20


@pytest.mark.story("US-03", "AC-8")
def test_500_users_hold_same_seat_exactly_one_wins(server):
    sid = server.seat_ids()["A1"]
    heads = server.entry_headers_for([f"user-{i}" for i in range(HOLDERS)])  # 9장: 입장권을 먼저 받는다
    reqs = [("POST", f"/seats/{sid}/hold", heads[f"user-{i}"], None) for i in range(HOLDERS)]
    results, total = fire(server.base_url, reqs)
    dist = distribution(results)
    info = f"워커 {server.workers} · 상태 분포 {dist} · {timing_summary(results, total)}"
    print("\n[hold]", info)

    assert dist == {201: 1, 409: HOLDERS - 1}, f"201 정확히 1건, 409 {HOLDERS - 1}건이어야 함: {info}"
    losers = [r for r in results if r["status"] == 409]
    bad = [r["text"][:120] for r in losers if (r["body"] or {}).get("code") != "SEAT_ALREADY_HELD"]
    assert not bad, f"409 code 가 SEAT_ALREADY_HELD 가 아님: {bad[:3]} / {info}"
    assert server.seat_status(sid) == "HELD", info


def _db_statuses(sid):
    with psycopg.connect(database_url()) as conn:
        return sorted(r[0] for r in conn.execute("SELECT status FROM bookings WHERE seat_id = %s", (sid,)).fetchall())


def _hold_and_confirm(server, number, user):
    sid = server.seat_ids()[number]
    hdr = {"X-User-Id": user}
    held, _ = fire(server.base_url, [("POST", f"/seats/{sid}/hold", server.entry_headers(user), None)], warm=False)
    assert held[0]["status"] == 201, held[0]["text"]
    conf, _ = fire(server.base_url, [("POST", "/bookings", {**hdr, "Idempotency-Key": f"k-{number}"}, {"seat_id": sid})], warm=False)
    assert conf[0]["status"] == 201, conf[0]["text"]
    return sid, conf[0]["body"]["id"]


@pytest.mark.story("US-04", "AC-7")
def test_20_concurrent_confirms_with_different_keys_make_one_booking(server):
    sid = server.seat_ids()["A2"]  # 다른 테스트와 다른 좌석
    hdr = {"X-User-Id": "buyer-u1"}
    held, _ = fire(server.base_url, [("POST", f"/seats/{sid}/hold", server.entry_headers("buyer-u1"), None)], warm=False)
    assert held[0]["status"] == 201, held[0]["text"]

    reqs = [("POST", "/bookings", {**hdr, "Idempotency-Key": f"key-{i}"}, {"seat_id": sid}) for i in range(CONFIRMS)]
    results, total = fire(server.base_url, reqs)
    dist = distribution(results)
    info = f"워커 {server.workers} · 상태 분포 {dist} · {timing_summary(results, total)}"
    print("\n[confirm]", info)

    assert dist == {200: CONFIRMS - 1, 201: 1}, f"201 정확히 1건, 200 {CONFIRMS - 1}건이어야 함: {info}"
    assert len({r["body"]["id"] for r in results}) == 1, f"예매 ID 가 여럿: {info}"
    assert _db_statuses(sid) == ["PENDING"], info
    assert server.seat_status(sid) == "HELD", info  # 확정 요청은 팔지 않는다


@pytest.mark.story("US-04", "AC-9")
def test_20_concurrent_confirms_with_same_key_return_identical_response(server):
    sid = server.seat_ids()["A3"]
    hdr = {"X-User-Id": "buyer-u1", "Idempotency-Key": "same-key"}
    held, _ = fire(server.base_url, [("POST", f"/seats/{sid}/hold", server.entry_headers("buyer-u1"), None)], warm=False)
    assert held[0]["status"] == 201, held[0]["text"]

    results, total = fire(server.base_url, [("POST", "/bookings", hdr, {"seat_id": sid})] * CONFIRMS)
    info = f"상태 분포 {distribution(results)} · {timing_summary(results, total)}"
    print("\n[confirm same key]", info)
    assert distribution(results) == {201: CONFIRMS}, info
    assert len({json.dumps(r["body"], sort_keys=True) for r in results}) == 1, f"응답 본문이 서로 다름: {info}"
    assert _db_statuses(sid) == ["PENDING"], info


@pytest.mark.story("US-05", "AC-5")
def test_20_concurrent_success_callbacks_sell_seat_once(server):
    sid, booking_id = _hold_and_confirm(server, "A4", "buyer-u2")
    reqs = [("POST", "/payments/callback", {}, {"booking_id": booking_id, "result": "SUCCEEDED"})] * CONFIRMS
    results, total = fire(server.base_url, reqs)
    info = f"워커 {server.workers} · 상태 분포 {distribution(results)} · {timing_summary(results, total)}"
    print("\n[callback]", info)
    assert distribution(results) == {200: CONFIRMS}, info
    assert {r["body"]["status"] for r in results} == {"CONFIRMED"}, info
    assert _db_statuses(sid) == ["CONFIRMED"], info
    assert server.seat_status(sid) == "SOLD", info


@pytest.mark.story("US-05", "AC-6")
def test_concurrent_opposite_callbacks_decide_exactly_once(server):
    sid, booking_id = _hold_and_confirm(server, "A5", "buyer-u3")
    reqs = [
        ("POST", "/payments/callback", {}, {"booking_id": booking_id, "result": "SUCCEEDED" if i % 2 else "FAILED"})
        for i in range(CONFIRMS)
    ]
    results, total = fire(server.base_url, reqs)
    info = f"상태 분포 {distribution(results)} · {timing_summary(results, total)}"
    assert distribution(results) == {200: CONFIRMS}, info
    final = {r["body"]["status"] for r in results}
    assert len(final) == 1 and final <= {"CONFIRMED", "FAILED"}, f"{final}: {info}"
    winner = final.pop()
    assert _db_statuses(sid) == [winner], info
    assert server.seat_status(sid) == ("SOLD" if winner == "CONFIRMED" else "HELD"), info
