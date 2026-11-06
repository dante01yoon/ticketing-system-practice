# 동시성: 홀드 방식 비교와 이중 판매 방어

5장 ③ 결과. 같은 좌석에 사용자 500명이 동시에 홀드할 때 **정확히 1명만 성공**해야 한다.
이를 보장하는 두 방식(`SELECT ... FOR UPDATE` vs 조건부 `UPDATE` 한 문장)을 같은 조건에서 재고, 단순한 쪽을 남겼다.

## 결론

**`SELECT ... FOR UPDATE` 를 남긴다.** 두 방식은 이 환경에서 속도 차이가 없었고(아래 표, 실행 간 범위가 겹침),
`FOR UPDATE` 는 확정(`/bookings`)과 같은 코드 경로를 쓰며 "시각은 잠금을 얻은 뒤에 읽는다"는 규칙을 지킨다.
조건부 `UPDATE` 는 시각을 문장 실행 전에 읽어야 해서 그 규칙과 테스트(`test_us03_ac1_hold_time_is_read_after_row_lock`)가 깨진다.
비슷하면 단순한 쪽·테스트 계약을 바꾸지 않는 쪽을 택했다.

## 비교한 두 방식

```python
# A. FOR UPDATE (채택): 행을 잠그고, 잠금 뒤 시각으로 판단하고, 갱신
seat = select(Seat).where(Seat.id == id).with_for_update()
now = clock()                       # 잠금을 얻은 뒤 시각
if seat.status == "SOLD": 409 SEAT_ALREADY_SOLD
if 유효한 홀드:             409 SEAT_ALREADY_HELD
seat.status, seat.held_by, seat.held_at = "HELD", user, now

# B. 조건부 UPDATE 한 문장 (폐기)
now = clock()                       # 문장 실행 전 시각 (잠금 뒤에 읽을 수 없음)
UPDATE seats SET status='HELD', held_by=:u, held_at=:now
 WHERE id=:id AND (status='AVAILABLE' OR (status='HELD' AND held_at <= :now - interval '5 minutes'))
RETURNING id
-- 0행이면 좌석을 읽어 404 / 409 SOLD / 409 HELD 로 구분
```

둘 다 승자는 DB가 정한다(파이썬 락 없음). B 는 왕복이 적지만, 이번 측정에서는 차이가 나타나지 않았다.

## 측정 환경

- 머신: Apple M4 Max (14코어, 36GiB), macOS. 서버·클라이언트·Docker 가 같은 머신.
- uvicorn 워커 4개 (별도 프로세스), 매 실행마다 새 서버·초기화된 DB(`reset_db`)
- DB 연결 풀: 워커당 `pool_size=8` + `max_overflow=4` (합계 48 < Postgres `max_connections` 100)
- PostgreSQL 16.15 (docker), 로컬 연결
- 클라이언트: `tests/concurrency/concurrency_helpers.py` 의 `fire` (httpx, 모든 연결을 먼저 열어 두고 한꺼번에 출발)
- 요청 500건(unique 는 시드 좌석 수인 100건). **방식·케이스마다 5회**, 값은 `중앙값 (최소–최대)`, 단위 ms

| 케이스 | 방식 | p50 | p95 | max | 전체 | 201 | 409 | 5xx |
|---|---|---|---|---|---|---|---|---|
| same (같은 좌석, 500명) | FOR UPDATE | 1775 (1690–1854) | 5362 (5299–5398) | 5446 (5418–5484) | 5446 (5419–5494) | 1 | 499 | 0 |
| same | 조건부 UPDATE | 1816 (1720–1866) | 5313 (5296–5383) | 5431 (5386–5449) | 5432 (5387–5451) | 1 | 499 | 0 |
| spread (좌석 100석에 5명씩) | FOR UPDATE | 1738 (629–1843) | 5357 (1238–5380) | 5453 (1330–5464) | 5454 (1356–5464) | 100 | 400 | 0 |
| spread | 조건부 UPDATE | 1853 (413–1887) | 5301 (758–5376) | 5416 (853–5457) | 5418 (866–5458) | 100 | 400 | 0 |
| unique (100석 각 1명, 경합 없음, 100건) | FOR UPDATE | 541 (528–545) | 967 (941–980) | 1042 (1002–1066) | 1071 (1032–1104) | 100 | 0 | 0 |
| unique | 조건부 UPDATE | 555 (509–580) | 990 (910–1001) | 1048 (994–1100) | 1106 (1023–1116) | 100 | 0 | 0 |

모든 실행에서 이중 홀드 0건(201 수가 기대와 일치), 5xx 0건.

## 해석

- 두 방식은 이 측정에서 구분되지 않았다. 중앙값 차이는 실행 간 변동 안에 있다. spread 의 넓은 범위(예: 629–1843)는 방식이 아닌 실행 간 편차이며, 두 방식 모두 같은 모양이다.
- **경합 여부와 배치 처리량은 거의 무관**했다. 전체 소요 ÷ 요청 수(배치 처리량의 역수)가 same 5.4s/500건, unique 1.07s/100건으로 둘 다 약 10.7ms/건이다.
  이 값은 **개별 요청의 지연이 아니다**(개별 지연은 위 표의 p50/p95). 잠금 대기가 지배적이었다면 same 이 훨씬 느렸을 것이라는 점만 시사한다.
- **병목이 홀드 쿼리의 잠금 바깥(클라이언트 처리량, 서버 요청 처리 등)에 있다는 것은 확인되지 않은 가설**이다. 원인을 가르려면 다음 계측이 필요하다:
  워커별 처리 요청 수, 클라이언트의 요청 송신 시각 분포(정말 동시에 나갔는지), 서버 안에서 잠금 대기·풀 대기 시간.
- 풀 실험: 이 실험(same 케이스 3회)에서 워커당 풀을 25로 키워도(합계 100) **개선을 관찰하지 못했고**, 일부 실행에서 `too many clients` 5xx 가 4건 생겼다.
  이 결과만으로 풀 크기가 원인이 아니라고 단정하지는 않는다. 연결 예산(워커 × (pool+overflow) + 다른 프로세스 + Postgres 예약 연결 < max_connections)은 지켜야 한다.
- 두 방식이 "구분되지 않는다"는 **이 환경에서의 관측**이지 두 방식이 동등하다는 증명은 아니다(반복 5회, 로컬 한 대).
- 한계: 한 대의 로컬 머신, 로컬 Postgres, 매우 짧은 트랜잭션. 네트워크 지연이 큰 DB 에서는 왕복이 적은 B 가 유리할 수 있다.

## 이중 예매 방어: `bookings.seat_id UNIQUE`

`bookings.seat_id UNIQUE`(DB 제약명 `bookings_seat_id_key`)는 `0001_initial` 마이그레이션에 **이미 있다**. 새 마이그레이션은 필요 없다.
이 제약은 **잠금이 없어도 이중 예매를 막는다.** lead 실험(잠금을 뺀 복사본, 별도 DB):

| 제거한 방어 | 결과 |
|---|---|
| 행 잠금 + UNIQUE 둘 다 제거 | 홀드 201 이 5건, 확정 예매가 2건 생김 (이중 판매) |
| 행 잠금만 제거 (UNIQUE 유지) | 홀드 201 이 10건(이중 홀드), 확정은 UNIQUE 덕분에 1건 |

정리: **UNIQUE 는 이중 예매의 마지막 방어선, 행 잠금은 이중 홀드를 막는 장치**다. 확정은 `FOR UPDATE`와 UNIQUE 를 둘 다 쓰고, UNIQUE 위반(`IntegrityError`)은 409 `SEAT_ALREADY_SOLD` 로 바꾼다.

## 재현

```bash
docker compose up -d --wait && uv sync
export DATABASE_URL=postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing_server   # 전용 DB (reset_db 가 데이터를 지운다)
uv run python -m backend.bench.hold_bench --runs 5     # 현재 구현(FOR UPDATE)만 측정. 세 케이스(same/spread/unique)
uv run pytest tests/concurrency -q -s                  # 500명 홀드·20 동시 확정 실서버 테스트
```
- 벤치에는 방식 선택 옵션이 없다. 조건부 UPDATE 와 다시 비교하려면 위 B 의 SQL 로 `hold_seat` 를 임시 대체하고 같은 명령을 돌린다(비교 후 코드에서는 제거했다).
- 같은 DB 를 쓰는 다른 실행(pytest 등)과 동시에 돌리면 안 된다. 위 표는 공용 DB 에서, 다른 실행이 없는 상태로 쟀다.
