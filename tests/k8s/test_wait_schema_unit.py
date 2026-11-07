"""11장 후속: wait_for_schema 대기 동작 단위 테스트 (docs/k8s.md 「앱 변경」). DB·Redis·실제 대기를 쓰지 않는다 (mock + 가짜 시계).

'항상 True' 로 바뀌는 회귀(첫 배포 보호인 initContainer wait-for-db 가 무력화)를 잡는 것이 목적.
"""
import runpy
import sys
import time
import types

import pytest


class Runaway(BaseException):
    """timeout 이 없어져 무한 대기하는 회귀를 hang 이 아니라 실패로 드러내려는 신호 (except Exception 에 안 잡힌다)."""


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        assert s >= 0
        self.sleeps.append(s)
        if len(self.sleeps) > 5000:
            raise Runaway("timeout 이 지나도 wait_for_schema 가 끝나지 않음")
        self.now += s


@pytest.fixture
def seed_mod(monkeypatch):
    from backend.app import seed

    clock = FakeClock()
    monkeypatch.setattr(seed, "time", types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    return seed, clock


def _script(monkeypatch, seed, outcomes):
    """_schema_is_head 가 호출될 때마다 outcomes 를 차례로 소비한다 (예외 클래스면 raise). 호출 횟수를 돌려준다."""
    calls = []
    it = iter(outcomes)

    def fake():
        calls.append(1)
        o = next(it)
        if isinstance(o, BaseException):
            raise o
        return o

    monkeypatch.setattr(seed, "_schema_is_head", fake)
    return calls


@pytest.mark.story("US-07", "wait-schema 대기")
def test_retries_after_exception_and_not_head_then_returns_true(seed_mod, monkeypatch):
    seed, clock = seed_mod
    calls = _script(monkeypatch, seed, [ConnectionError("refused"), False, True])
    assert seed.wait_for_schema(timeout=60, interval=2) is True
    assert len(calls) == 3, f"검사 횟수 {len(calls)} (접속 실패 → 리비전 다름 → 성공 = 3회여야 함)"
    assert len(clock.sleeps) == 2 and all(0 < s <= 2 for s in clock.sleeps), clock.sleeps


@pytest.mark.story("US-07", "wait-schema 대기")
def test_returns_true_immediately_when_already_head(seed_mod, monkeypatch):
    seed, clock = seed_mod
    calls = _script(monkeypatch, seed, [True])
    assert seed.wait_for_schema(timeout=60, interval=2) is True
    assert len(calls) == 1 and clock.sleeps == []


@pytest.mark.story("US-07", "wait-schema 대기")
@pytest.mark.parametrize("failure", [False, ConnectionError("down")], ids=["not-head", "exception"])
def test_returns_false_after_timeout_without_real_waiting(seed_mod, monkeypatch, failure):
    seed, clock = seed_mod
    calls = []

    def always_fail():
        calls.append(1)
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr(seed, "_schema_is_head", always_fail)
    start = clock.now
    assert seed.wait_for_schema(timeout=10, interval=2) is False
    waited = clock.now - start
    assert 10 <= waited <= 12, f"가짜 시계로 {waited}초 기다림: timeout(10) 근처여야 함"
    assert len(calls) >= 5, f"interval 마다 다시 봐야 함 (검사 {len(calls)}회)"


class _DownEngine:
    def connect(self):
        raise ConnectionError("db down")


@pytest.mark.story("US-07", "wait-schema CLI")
def test_cli_wait_schema_exits_1_when_schema_never_ready(monkeypatch):
    """__main__ 분기를 runpy 로 실행: 엔진이 항상 접속 실패 → 가짜 시계로 300초를 건너뛰고 종료 코드 1."""
    import backend.app.db as db

    clock = FakeClock()
    monkeypatch.setattr(db, "engine", _DownEngine())
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(sys, "argv", ["seed", "--wait-schema"])
    with pytest.raises(SystemExit) as e:
        runpy.run_module("backend.app.seed", run_name="__main__")
    assert e.value.code == 1, f"종료 코드 {e.value.code!r} (시간 초과면 1이어야 함)"
    assert clock.now - 1000.0 >= 300 - 1e-6, "기본 timeout(300초)을 기다렸어야 함"
