# 백엔드 구현·테스트 접점 (3장 첫 서버, 6장 결제 두 단계)

test(`tests/api/`)와 server(`backend/`)가 서로 기대하는 이름과 동작. 바꾸려면 lead에게 먼저 알린다.
API 동작 자체는 `docs/openapi.yaml`(계약)과 `docs/stories/US-01~05`(인수 조건)가 기준이다.

## 실행
```bash
docker compose up -d --wait            # PostgreSQL 16 (localhost:5432, ticketing/ticketing/ticketing)
uv sync
uv run alembic -c backend/alembic.ini upgrade head   # 테이블 생성
uv run python -m backend.app.seed                    # 시드: 공연 1개 · 좌석 100석
uv run uvicorn backend.app.main:app --reload         # 서버
uv run pytest                                        # 전체 테스트
```
- DB 주소는 환경변수 `DATABASE_URL`, 기본값 `postgresql+psycopg://ticketing:ticketing@localhost:5432/ticketing`.
- 의존성(`pyproject.toml`)과 `docker-compose.yml`은 lead 소유. 필요한 패키지가 더 있으면 lead에게 요청한다.
  현재: fastapi, uvicorn, sqlalchemy 2, psycopg 3, alembic / 개발용 pytest, pyyaml, httpx.

## server가 제공하는 것 (test가 import)
| 이름 | 설명 |
|---|---|
| `backend.app.main.create_app() -> FastAPI` | 새 앱 인스턴스 |
| `backend.app.main.app` | `create_app()` 결과 (uvicorn용) |
| `backend.app.deps.get_now() -> datetime` | 현재 시각(UTC, timezone-aware) FastAPI 의존성. 홀드·만료·좌석맵·확정 요청·콜백 판단과 기록 시각은 **모두 이 값만** 쓴다(DB `now()`, 요청 본문의 시각 금지). 테스트는 `app.dependency_overrides[get_now]`로 바꾼다 |
| `backend.app.seed.reset_db(seed: bool = True) -> None` | 마이그레이션을 head까지 적용하고 모든 데이터를 지운 뒤, `seed=True`면 시드를 넣는다. 테스트마다 호출 |
| `backend.app.seed.add_concert(title: str, starts_at: datetime, seats: int = 0) -> str` | 공연 하나를 더 만들고(좌석 `seats`개, 번호 A1부터) 공연 ID를 돌려준다. 테스트 준비용 |

## 시드
- 공연 1개: 제목·시작 일시 아무거나.
- 좌석 100석: 번호 `A1`~`J10`(A~J열 × 1~10번), 모두 AVAILABLE.
- ID는 문자열이며 값은 server가 정한다. **테스트는 ID를 하드코딩하지 않고 `GET /concerts`, `GET /concerts/{id}/seats`로 찾는다.**

## 계약에서 놓치기 쉬운 점
- `X-User-Id`가 없거나 빈 문자열이면 **401** `{code: USER_ID_REQUIRED}` (FastAPI 기본 422가 아님).
- 헤더의 NUL 문자(U+0000): PostgreSQL text에 넣을 수 없어 그대로 두면 500이 난다. `X-User-Id`에 있으면 401 `USER_ID_REQUIRED`,
  `Idempotency-Key`에 있으면 400 `IDEMPOTENCY_KEY_INVALID`. DB에 닿기 전에 거르고 상태·멱등 기록을 남기지 않는다.
  판단 순서는 그대로(X-User-Id → Idempotency-Key → 본문).
- 오류 본문은 항상 `{code, message}` (FastAPI 기본 `{detail}`이 아님). 상태 코드·`code` 값은 `docs/openapi.yaml`.
- 좌석맵에서 만료된 HELD는 `AVAILABLE`, 응답에 홀드한 사용자 정보 없음.
- 만료: `get_now() >= held_at + 5분`이면 만료(5분 정각부터 다시 홀드 가능). 4분 59초는 유효.
- **시각은 좌석 잠금을 얻은 뒤에 읽는다.** 요청 시작 때 한 번 받은 값을 쓰면 잠금을 기다리는 사이 만료된 홀드가 팔린다(3장 리뷰 지적).
  테스트는 계속 `app.dependency_overrides[get_now]`만 바꾸므로, 구현은 잠금 뒤에 그 override를 다시 호출해 읽어야 한다
  (예: `request.app.dependency_overrides.get(get_now, get_now)()`를 돌려주는 시계 의존성).
- 요청 본문 형식 오류(`POST /bookings`에 JSON 아님, `seat_id` 누락·null·문자열 아님)는 422 `{code: VALIDATION_ERROR}`.
- 같은 좌석 동시 홀드·동시 확정은 하나만 성공(DB 행 잠금 또는 조건부 UPDATE 등, 방법은 server가 정함).

## 6장: 결제 두 단계 (US-04 개정, US-05 신규)
- 경로: `POST /bookings`(헤더 `Idempotency-Key` 필수) → PENDING 예매, `POST /payments/callback` `{booking_id, result: SUCCEEDED|FAILED}`,
  `GET /bookings/{id}`(X-User-Id, 본인만). 상태 코드·`code`·판단 순서는 `docs/openapi.yaml`.
- `POST /bookings`는 **좌석을 팔지 않는다.** 성공 콜백이 판다. 기존 "확정 = SOLD" 테스트는 콜백을 거치도록 바뀐다.
- 만료 경계는 홀드·확정 요청·콜백 모두 같다: `now >= held_at + 5분`이면 만료. 콜백은 `now >= booking.expires_at`이면 REFUND_REQUIRED.
- **콜백도 좌석 행 잠금을 얻은 뒤에 시각을 읽는다**(`lock_clock` 패턴). 늦은 성공 콜백과 다른 사용자의 재홀드가 같은 좌석에서 직렬화돼야 한다.
- 콜백 반영은 예매가 PENDING일 때 한 번뿐(중복·동시 콜백, 반대 결과 콜백은 무변경 200).
- 멱등 키는 `(user_id, key)` 단위. 같은 키·같은 seat_id 재요청은 처음 응답(상태 코드·본문)을 그대로. 동시에 같은 키가 와도 예매 하나.
- 한 좌석의 CONFIRMED 예매는 최대 하나(DB 제약으로도 막는다). 기존 `bookings.seat_id` UNIQUE는 실패·환불 필요 예매가 생기므로 바뀐다 — 스키마는 server가 새 마이그레이션으로 정한다.
- 테스트 헬퍼가 DB를 직접 볼 때 쓰는 이름: 테이블 `bookings`, 열 `seat_id`, `status`(`PENDING|CONFIRMED|FAILED|REFUND_REQUIRED`). 그 밖의 열 이름은 테스트가 가정하지 않는다.

## 테스트하지 않는 것 (PRD 열린 질문, 미정)
- 한 사용자가 여러 좌석을 홀드하는 경우
- 결제사 콜백 서명 검증, 환불 실행

## 8장: Redis 캐시와 홀드 관문 (docs/cache.md)
- 패키지 `redis`(redis-py) 추가됨. Redis 주소는 `REDIS_URL`, 기본 `redis://localhost:6379/0`(compose `redis` 서비스, 호스트 6379).
- `reset_db()`는 DB와 함께 `REDIS_URL`의 Redis DB도 `FLUSHDB`한다(Redis가 꺼져 있으면 넘어감).
- 좌석맵 200 응답 헤더 `X-Cache: HIT|MISS`, 홀드 응답(401·입장권 403 제외) 헤더 `X-Hold-Gate: REDIS|DB`.
- 테스트는 Redis 키 이름·값 형식을 가정하지 않는다(필요하면 `FLUSHDB`만 쓴다). 동작은 헤더와 API 응답으로 확인한다.
- Redis 만료 판단도 `get_now`(테스트의 가짜 시각)만 쓴다. Redis TTL은 저장 정리와 앱이 죽었을 때의 임대에만 쓴다.

## 9장: 가상 대기열과 입장권 (docs/queue.md)
- 새 경로 `POST /concerts/{id}/queue`, `GET /concerts/{id}/queue/me`. 좌석맵·홀드는 `X-User-Id` + `X-Entry-Token` 필요(403 `ENTRY_TOKEN_*`).
- 설정은 환경 변수(`QUEUE_BATCH`·`QUEUE_INTERVAL_SECONDS`·`ENTRY_TTL_SECONDS`·`ENTRY_TOKEN_SECRET`)를 `create_app()` 때 읽는다.
- 전광판·입장권 만료 판단은 `get_now`만 쓴다(가짜 시계를 간격만큼 옮기면 전광판이 한 묶음 오른다).
- 테스트는 대기열 Redis 키 이름·입장권 형식을 가정하지 않는다(변조 테스트는 받은 값의 일부를 바꾸는 것만).
- 테스트 접점 `backend.app.queue.issue_entry_token(user_id, concert_id, expires_at) -> str`: 대기열이 503일 때만 헬퍼가 쓴다(docs/queue.md ④).
