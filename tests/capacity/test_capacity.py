"""scripts/capacity.py 의 트래픽 계산 공식 검증 (docs/capacity.md).

기본값 표는 손 계산 결과. 입력을 바꾸면 공식대로 변하는지, 공식 적용 범위 밖이면 ValueError 인지 확인한다.
"""
from dataclasses import replace

import pytest

from scripts.capacity import Assumptions, compute

KEYS = ["queue_enter", "queue_poll", "seat_view", "hold", "payment", "db"]

# key: (people, requests, period_s, rps, peak_rps)
DEFAULT_TABLE = {
    "queue_enter": (1_000_000, 1_000_000, 60, 16_666.67, 16_666.67),
    "queue_poll": (1_000_000, 111_187_500, 625, 177_900, 197_600),
    "seat_view": (125_000, 250_000, 625, 400, 400),
    "hold": (125_000, 250_000, 625, 400, 400),
    "payment": (50_000, 50_000, 625, 80, 80),
    "db": (125_000, 600_000, 625, 960, 960),
}


def by_key(a=None):
    rows = compute(a) if a is not None else compute()
    return {r.key: r for r in rows}


def test_row_order_and_keys_fixed():
    assert [r.key for r in compute()] == KEYS


@pytest.mark.parametrize("key", KEYS)
def test_default_table(key):
    people, requests, period, rps, peak = DEFAULT_TABLE[key]
    r = by_key()[key]
    assert r.people == pytest.approx(people)
    assert r.requests == pytest.approx(requests)
    assert r.period_s == pytest.approx(period)
    assert r.rps == pytest.approx(rps, abs=0.01)
    assert r.peak_rps == pytest.approx(peak, abs=0.01)


def test_compute_default_argument_equals_default_assumptions():
    assert compute() == compute(Assumptions())


def test_sold_out_stops_admission():
    # admitted = min(1_000_000, 50_000 / 0.4) = 125_000
    assert by_key()["seat_view"].people == pytest.approx(125_000)


@pytest.mark.parametrize(
    "a",
    [
        Assumptions(),
        Assumptions(poll_interval_s=10),
        Assumptions(pay_ratio=0.5),
        Assumptions(arrivals=100_000),
        Assumptions(seats_per_payment=2, payments_per_payer=2),
    ],
)
def test_rps_is_requests_over_period_for_every_row(a):
    for r in compute(a):
        assert r.rps == pytest.approx(r.requests / r.period_s), r.key


def test_poll_interval_doubled_halves_poll_requests_and_peak():
    base = by_key()["queue_poll"]
    slow = by_key(Assumptions(poll_interval_s=10))["queue_poll"]
    assert slow.requests == pytest.approx(base.requests / 2)
    assert slow.peak_rps == pytest.approx(base.peak_rps / 2)
    assert slow.period_s == pytest.approx(base.period_s)


def test_pay_ratio_half_means_100k_admitted():
    # 50_000 / 0.5 = 100_000 입장, T = 500
    rows = by_key(Assumptions(pay_ratio=0.5))
    assert rows["seat_view"].people == pytest.approx(100_000)
    assert rows["hold"].people == pytest.approx(100_000)
    assert rows["seat_view"].period_s == pytest.approx(500)
    assert rows["payment"].people == pytest.approx(50_000)


def test_arrivals_below_capacity_admits_everyone():
    # 입장 한도 125_000 보다 접속이 적으면 전원 입장: admitted=100_000, T=500
    rows = by_key(Assumptions(arrivals=100_000))
    assert rows["seat_view"].people == pytest.approx(100_000)
    assert rows["seat_view"].period_s == pytest.approx(500)
    assert rows["payment"].people == pytest.approx(40_000)
    # Q = 100_000 - 200*60 = 88_000, 남은 대기자 0 → poll = [60*88000/2 + 440*88000/2]/5
    assert rows["queue_poll"].requests == pytest.approx((60 * 88_000 / 2 + 440 * 88_000 / 2) / 5)
    assert rows["queue_poll"].peak_rps == pytest.approx(88_000 / 5)


def test_per_user_counts_scale_requests():
    rows = by_key(Assumptions(seat_views_per_user=4, holds_per_user=3, payments_per_payer=1))
    assert rows["seat_view"].requests == pytest.approx(125_000 * 4)
    assert rows["hold"].requests == pytest.approx(125_000 * 3)


def test_payments_and_seats_per_payment_change_admission():
    # admitted = 50_000 / (0.4 * 2 * 1) = 62_500
    rows = by_key(Assumptions(payments_per_payer=2))
    assert rows["seat_view"].people == pytest.approx(62_500)
    assert rows["payment"].people == pytest.approx(25_000)
    assert rows["payment"].requests == pytest.approx(50_000)
    # 좌석 2석씩 팔면 마찬가지로 입장 한도가 절반
    rows = by_key(Assumptions(seats_per_payment=2))
    assert rows["seat_view"].people == pytest.approx(62_500)


def test_db_requests_formula_and_no_queue_db_usage():
    a = Assumptions(db_ops_per_seat_view=3, db_ops_per_hold=5, db_ops_per_payment=7)
    rows = by_key(a)
    expected = (
        rows["seat_view"].requests * 3 + rows["hold"].requests * 5 + rows["payment"].requests * 7
    )
    assert rows["db"].requests == pytest.approx(expected)
    # 대기열 행은 DB 요청에 들어가지 않는다: 폴링 간격을 바꿔도 db 는 그대로
    assert by_key(Assumptions(poll_interval_s=1))["db"].requests == pytest.approx(
        by_key()["db"].requests
    )


@pytest.mark.parametrize(
    "a",
    [
        replace(Assumptions(), admit_rate=20_000),            # admit_rate > arrivals/W
        replace(Assumptions(), admit_rate=1_000_000 / 60),    # admit_rate == arrivals/W (경계, 엄격 부등호)
        replace(Assumptions(), seats=1_000),                  # admitted=2_500, T=12.5 < W
    ],
    ids=["admit_rate_too_high", "admit_rate_equals_arrival_rate", "sold_out_before_window_ends"],
)
def test_out_of_range_raises_value_error(a):
    with pytest.raises(ValueError):
        compute(a)


def test_boundary_t_equals_w_is_valid():
    # T == W 이면 유효: admit_rate=200, W=60 → admitted=12_000, seats=4_800
    rows = by_key(Assumptions(seats=4_800))
    assert rows["seat_view"].period_s == pytest.approx(60)
    assert rows["queue_poll"].requests == pytest.approx(60 * 988_000 / 2 / 5)
