#!/usr/bin/env python3
"""티켓 오픈 트래픽 계산 시트. 공식은 docs/capacity.md, 검증은 tests/capacity/.

    uv run python scripts/capacity.py   # 기본 [가정]으로 결과 표(Markdown) 출력
"""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Assumptions:
    """기본값은 모두 [가정]."""

    seats: int = 50_000
    arrivals: int = 1_000_000          # open_window_s 안에 균등하게 접속
    open_window_s: float = 60
    poll_interval_s: float = 5
    admit_rate: float = 200            # 초당 입장 인원
    seat_views_per_user: int = 2
    holds_per_user: int = 2
    pay_ratio: float = 0.4
    payments_per_payer: int = 1
    seats_per_payment: int = 1
    db_ops_per_seat_view: int = 1      # 읽기 1
    db_ops_per_hold: int = 1           # 쓰기 1
    db_ops_per_payment: int = 2        # 좌석 SOLD + 예매 생성


@dataclass(frozen=True)
class Row:
    key: str
    name: str
    people: float
    requests: float
    period_s: float
    rps: float
    peak_rps: float


def _row(key, name, people, requests, period_s, peak_rps=None):
    rps = requests / period_s
    return Row(key, name, people, requests, period_s, rps, rps if peak_rps is None else peak_rps)


def compute(a: Assumptions = Assumptions()) -> List[Row]:
    W = a.open_window_s
    if not a.admit_rate < a.arrivals / W:
        raise ValueError("공식 적용 범위 밖: admit_rate 는 arrivals/open_window_s 보다 작아야 합니다")

    admitted = min(a.arrivals, a.seats / (a.pay_ratio * a.payments_per_payer * a.seats_per_payment))
    T = admitted / a.admit_rate
    if T < W:
        raise ValueError("공식 적용 범위 밖: 입장 기간(T)이 접속 시간(open_window_s)보다 짧습니다")

    # 대기 인원: 0→W 선형 증가해 Q, W→T 선형 감소해 arrivals−admitted (매진 시 폴링 중단)
    Q = a.arrivals - a.admit_rate * W
    waiting_person_seconds = W * Q / 2 + (T - W) * (Q + (a.arrivals - admitted)) / 2
    poll_requests = waiting_person_seconds / a.poll_interval_s

    seat_view = admitted * a.seat_views_per_user
    hold = admitted * a.holds_per_user
    payers = admitted * a.pay_ratio
    payment = payers * a.payments_per_payer
    db = (
        seat_view * a.db_ops_per_seat_view
        + hold * a.db_ops_per_hold
        + payment * a.db_ops_per_payment
    )

    return [
        _row("queue_enter", "대기열 진입", a.arrivals, a.arrivals, W),
        _row("queue_poll", "순번 확인(폴링)", a.arrivals, poll_requests, T, Q / a.poll_interval_s),
        _row("seat_view", "좌석 조회", admitted, seat_view, T),
        _row("hold", "홀드", admitted, hold, T),
        _row("payment", "모의 결제", payers, payment, T),
        _row("db", "DB 작업", admitted, db, T),
    ]


def render_markdown(rows: List[Row]) -> str:
    lines = [
        "| 단계 | 사람 수 | 요청 수 | 기간(초) | 초당 요청 수(평균) | 초당 요청 수(최대) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r.name} | {r.people:,.0f} | {r.requests:,.0f} | {r.period_s:,.0f} "
            f"| {r.rps:,.1f} | {r.peak_rps:,.1f} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_markdown(compute()))
