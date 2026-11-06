# 백엔드 구현·테스트 접점 (3장 첫 서버)

test(`tests/api/`)와 server(`backend/`)가 서로 기대하는 이름과 동작. 바꾸려면 lead에게 먼저 알린다.
API 동작 자체는 `docs/openapi.yaml`(계약)과 `docs/stories/US-01~04`(인수 조건)가 기준이다.

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
| `backend.app.deps.get_now() -> datetime` | 현재 시각(UTC, timezone-aware) FastAPI 의존성. 홀드·만료·좌석맵 판단은 **모두 이 값만** 쓴다(DB `now()` 금지). 테스트는 `app.dependency_overrides[get_now]`로 바꾼다 |
| `backend.app.seed.reset_db(seed: bool = True) -> None` | 마이그레이션을 head까지 적용하고 모든 데이터를 지운 뒤, `seed=True`면 시드를 넣는다. 테스트마다 호출 |
| `backend.app.seed.add_concert(title: str, starts_at: datetime, seats: int = 0) -> str` | 공연 하나를 더 만들고(좌석 `seats`개, 번호 A1부터) 공연 ID를 돌려준다. 테스트 준비용 |

## 시드
- 공연 1개: 제목·시작 일시 아무거나.
- 좌석 100석: 번호 `A1`~`J10`(A~J열 × 1~10번), 모두 AVAILABLE.
- ID는 문자열이며 값은 server가 정한다. **테스트는 ID를 하드코딩하지 않고 `GET /concerts`, `GET /concerts/{id}/seats`로 찾는다.**

## 계약에서 놓치기 쉬운 점
- `X-User-Id`가 없거나 빈 문자열이면 **401** `{code: USER_ID_REQUIRED}` (FastAPI 기본 422가 아님).
- 오류 본문은 항상 `{code, message}` (FastAPI 기본 `{detail}`이 아님). 상태 코드·`code` 값은 `docs/openapi.yaml`.
- 좌석맵에서 만료된 HELD는 `AVAILABLE`, 응답에 홀드한 사용자 정보 없음.
- 만료: `get_now() >= held_at + 5분`이면 만료(5분 정각부터 다시 홀드 가능). 4분 59초는 유효.
- **시각은 좌석 잠금을 얻은 뒤에 읽는다.** 요청 시작 때 한 번 받은 값을 쓰면 잠금을 기다리는 사이 만료된 홀드가 팔린다(3장 리뷰 지적).
  테스트는 계속 `app.dependency_overrides[get_now]`만 바꾸므로, 구현은 잠금 뒤에 그 override를 다시 호출해 읽어야 한다
  (예: `request.app.dependency_overrides.get(get_now, get_now)()`를 돌려주는 시계 의존성).
- 요청 본문 형식 오류(`POST /bookings`에 JSON 아님, `seat_id` 누락·null·문자열 아님)는 422 `{code: VALIDATION_ERROR}`.
- 같은 좌석 동시 홀드·동시 확정은 하나만 성공(DB 행 잠금 또는 조건부 UPDATE 등, 방법은 server가 정함).

## 테스트하지 않는 것 (PRD 열린 질문, 미정)
- 한 사용자가 여러 좌석을 홀드하는 경우
- 자기 유효 홀드를 다시 홀드하는 경우
