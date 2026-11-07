"""docs/queue.md ⑥ 1·2: 번호표는 겹침·빠짐 없이, 같은 사용자는 같은 번호 (실서버 uvicorn 워커 4개)."""
import pytest

from queuelib import join_burst

N = 1000


def _numbers(results):
    return [r["body"]["number"] for r in results]


@pytest.mark.story("US-08", "번호 정합성")
def test_1000_concurrent_users_get_exactly_numbers_1_to_1000(fresh_queue_server):
    s = fresh_queue_server
    assert s.workers >= 2
    cid = s.concert_id()
    users = [f"user-{i}" for i in range(N)]
    results = join_burst(s.base_url, cid, users)
    bad = [r for r in results if r["status"] != 201]
    assert not bad, f"201 이 아닌 응답 {len(bad)}건: {[(r['status'], r['text']) for r in bad[:3]]}"
    assert sorted(_numbers(results)) == list(range(1, N + 1)), "번호가 1..1000 과 정확히 일치해야 함(겹침·빠짐 없음)"

    first = {r["user"]: r["body"]["number"] for r in results}
    again = join_burst(s.base_url, cid, users)  # 같은 1000명이 다시 부른다
    assert all(r["status"] == 200 for r in again), {r["status"] for r in again}
    assert {r["user"]: r["body"]["number"] for r in again} == first, "다시 불렀을 때 번호가 바뀜"


@pytest.mark.story("US-08", "같은 사용자")
def test_same_user_many_concurrent_calls_get_one_number_and_no_gap(fresh_queue_server):
    s = fresh_queue_server
    cid = s.concert_id()
    others = [f"other-{i}" for i in range(30)]
    users = ["dup"] * 20 + others  # dup 이 20번 동시에, 다른 30명과 섞여서
    results = join_burst(s.base_url, cid, users)
    assert all(r["status"] in (200, 201) for r in results), [(r["status"], r["text"]) for r in results if r["status"] not in (200, 201)][:3]

    dup = [r for r in results if r["user"] == "dup"]
    assert len({r["body"]["number"] for r in dup}) == 1, f"dup 의 번호가 여럿: {sorted(_numbers(dup))}"
    assert sorted(r["status"] for r in dup) == [200] * 19 + [201], "새 번호 201 은 정확히 한 번"
    # 번호가 빠지지 않는다: dup 1명 + 다른 30명 = 1..31
    owners = {r["user"]: r["body"]["number"] for r in results}
    assert len(owners) == 31
    assert sorted(owners.values()) == list(range(1, 32)), f"번호가 빠지거나 겹침: {sorted(owners.values())}"

    # 그 뒤 새 사용자는 바로 다음 번호
    nxt = join_burst(s.base_url, cid, ["after"])[0]
    assert (nxt["status"], nxt["body"]["number"]) == (201, 32), nxt
