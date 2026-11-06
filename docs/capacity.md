# 트래픽 계산 시트 (오픈 시점)

티켓 오픈 직후 단계별로 요청이 얼마나 몰리는지 계산한다. 계산은 `scripts/capacity.py`가 하고,
공식은 `tests/capacity/`의 테스트가 검증한다. 다시 계산: `uv run python scripts/capacity.py`

- **[가정]**: 정해진 값이 아니라 가정한 입력. 바꾸면 `scripts/capacity.py`의 `Assumptions`를 고친다.
- **[실측 필요]**: 부하 테스트로 재야 채울 수 있는 값.

## 입력값

| 입력 | 값 | 표시 |
|---|---|---|
| 좌석 수 (`seats`) | 50,000석 | [가정] |
| 오픈 후 접속 인원 (`arrivals`) | 1,000,000명 | [가정] |
| 접속이 몰리는 시간 (`open_window_s`) | 60초, 그 안에 균등하게 접속 | [가정] |
| 순번 확인 주기 (`poll_interval_s`) | 5초마다 1번 | [가정] |
| 입장 속도 (`admit_rate`) | 초당 200명 | [가정] |
| 입장한 사람의 좌석 조회 (`seat_views_per_user`) | 1인당 2번 | [가정] |
| 입장한 사람의 홀드 (`holds_per_user`) | 1인당 2번 | [가정] |
| 결제하는 비율 (`pay_ratio`) | 입장 인원의 40% | [가정] |
| 결제 횟수 (`payments_per_payer`) | 결제자 1인당 1번 | [가정] |
| 결제 1번에 팔리는 좌석 (`seats_per_payment`) | 1석 | [가정] |
| 매진 후 | 입장 중단, 남은 대기자는 순번 확인을 멈춤 | [가정] |
| DB 작업 수 (`db_ops_per_*`) | 좌석 조회 읽기 1 · 홀드 쓰기 1 · 결제 쓰기 2(좌석 SOLD + 예매 생성) | [가정] |
| 대기열 진입·순번 확인의 DB 사용 | 쓰지 않음 (별도 저장소) | [가정] |

## 계산 공식

기호: `W` = 접속이 몰리는 시간, `admitted` = 입장 인원, `T` = 입장 기간.

- `admitted = min(arrivals, seats / (pay_ratio × payments_per_payer × seats_per_payment))` — 매진되면 입장을 멈춘다
- `T = admitted / admit_rate`
- 모든 행에서 `초당 요청 수(rps) = 요청 수 / 기간`. `최대 초당(peak_rps)`은 순번 확인 외에는 `rps`와 같다.

| 행 (`key`) | 사람 수 | 요청 수 | 기간 |
|---|---|---|---|
| 대기열 진입 (`queue_enter`) | `arrivals` | `arrivals` × 1 | `W` |
| 순번 확인 (`queue_poll`) | `arrivals` | 아래 식 | `T` |
| 좌석 조회 (`seat_view`) | `admitted` | `admitted × seat_views_per_user` | `T` |
| 홀드 (`hold`) | `admitted` | `admitted × holds_per_user` | `T` |
| 모의 결제 (`payment`) | `admitted × pay_ratio` | 사람 수 × `payments_per_payer` | `T` |
| DB 작업 (`db`) | `admitted` | 좌석 조회 × `db_ops_per_seat_view` + 홀드 × `db_ops_per_hold` + 결제 × `db_ops_per_payment` | `T` |

순번 확인은 대기 인원 × (1 / 확인 주기)만큼 요청이 생긴다.
대기 인원은 0초부터 `W`까지 `(arrivals/W − admit_rate)·t`로 늘다가, `W` 이후 초당 `admit_rate`씩 줄고, `T`에 매진되면 0이 된다.

- `Q = arrivals − admit_rate × W` (대기 인원이 가장 많을 때)
- 요청 수 = `[ W·Q/2 + (T−W)·(Q + (arrivals − admitted))/2 ] / poll_interval_s`
- 최대 초당 = `Q / poll_interval_s`
- 이 식은 `admit_rate < arrivals/W`이고 `T ≥ W`일 때만 맞다. 아니면 `compute()`가 `ValueError`를 낸다.

## 코드 인터페이스 (`scripts/capacity.py`)

```python
@dataclass(frozen=True)
class Assumptions:          # 기본값은 모두 위 입력값 [가정]
    seats: int = 50_000
    arrivals: int = 1_000_000
    open_window_s: float = 60
    poll_interval_s: float = 5
    admit_rate: float = 200
    seat_views_per_user: int = 2
    holds_per_user: int = 2
    pay_ratio: float = 0.4
    payments_per_payer: int = 1
    seats_per_payment: int = 1
    db_ops_per_seat_view: int = 1
    db_ops_per_hold: int = 1
    db_ops_per_payment: int = 2

@dataclass(frozen=True)
class Row:
    key: str
    name: str
    people: float
    requests: float
    period_s: float
    rps: float        # requests / period_s
    peak_rps: float   # 최대 초당 요청 수

def compute(a: Assumptions = Assumptions()) -> list[Row]
# 행 순서와 key 고정: queue_enter, queue_poll, seat_view, hold, payment, db
```

## 결과 (기본 입력값)

<!-- scripts/capacity.py 출력. 입력을 바꾸면 다시 실행해 붙여 넣는다 -->
| 단계 | 사람 수 | 요청 수 | 기간(초) | 초당 요청 수(평균) | 초당 요청 수(최대) |
|---|---:|---:|---:|---:|---:|
| 대기열 진입 | 1,000,000 | 1,000,000 | 60 | 16,666.7 | 16,666.7 |
| 순번 확인(폴링) | 1,000,000 | 111,187,500 | 625 | 177,900.0 | 197,600.0 |
| 좌석 조회 | 125,000 | 250,000 | 625 | 400.0 | 400.0 |
| 홀드 | 125,000 | 250,000 | 625 | 400.0 | 400.0 |
| 모의 결제 | 50,000 | 50,000 | 625 | 80.0 | 80.0 |
| DB 작업 | 125,000 | 600,000 | 625 | 960.0 | 960.0 |

- 입장 인원 125,000명 = 50,000석 ÷ 40%. 초당 200명씩 입장하면 625초(약 10분 25초) 뒤 매진된다.
- 가장 무거운 것은 **순번 확인(폴링)**: 최대 초당 197,600건(60초 시점 대기 988,000명 ÷ 5초). 좌석 조회·홀드의 약 500배다.
- 대기열 진입·순번 확인은 DB를 쓰지 않는다고 [가정]했으므로, DB는 초당 960건(읽기 400 + 쓰기 560)이다.

## 서버 처리량

| 단계 | 최대 초당 요청 수 | 서버 1대 처리량(초당) | 필요한 서버 수 (= 최대 초당 ÷ 1대 처리량) |
|---|---:|---|---|
| 대기열 진입 | 16,666.7 | [실측 필요] | [실측 필요] |
| 순번 확인(폴링) | 197,600.0 | [실측 필요] | [실측 필요] |
| 좌석 조회 | 400.0 | [실측 필요] | [실측 필요] |
| 홀드 | 400.0 | [실측 필요] | [실측 필요] |
| 모의 결제 | 80.0 | [실측 필요] | [실측 필요] |
| DB 작업 | 960.0 | [실측 필요] | [실측 필요] |
