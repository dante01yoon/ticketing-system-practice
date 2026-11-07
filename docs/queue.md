# 9장 계약: 가상 대기열과 입장권

목표: 인기 공연이 열릴 때 몰린 사용자를 Redis 대기열에 줄 세우고, 정해진 속도로만 들여보낸다.
좌석 조회·홀드는 차례가 된 사용자(입장권 보유자)만 할 수 있어 DB에 닿는 요청 수가 입장 속도로 묶인다.
좌석 판매의 진짜 기준은 여전히 PostgreSQL이다(8장 docs/cache.md 그대로).

## 파일과 소유 (9장 한정)

| 파일 | 소유 |
|---|---|
| `backend/`, `docker-compose.yml`(앱 환경 변수) | server |
| `tests/queue/`(새 확인 테스트), 기존 테스트·헬퍼(`tests/api/apihelpers.py`, 실서버 픽스처 등)의 입장권 붙이기 | test |
| `docs/queue.md`, `docs/openapi.yaml`, `docs/backend-interface.md`, `docs/cache.md`, `scripts/test-hashes.json` | lead |

## 설정 (환경 변수, `create_app()` 때 읽는다)

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `QUEUE_BATCH` | `100` | 전광판이 한 번에 올라가는 사람 수(묶음) |
| `QUEUE_INTERVAL_SECONDS` | `10` | 전광판이 올라가는 간격(초) |
| `ENTRY_TTL_SECONDS` | `120` | 입장권 수명(2분) |
| `ENTRY_TOKEN_SECRET` | 개발용 고정 문자열 | 입장권 서명 키. 앱 세 대가 같은 값을 써야 한다(compose 기본값 공유). **기본값은 공개돼 있어 누구나 입장권을 위조할 수 있다 — 실습용. 실제 배포에서는 비공개 값을 세 앱에 같이 넣는다** |

인프로세스 테스트는 `create_app()` 전에 환경 변수를 바꿔 작은 값을 쓸 수 있다. 실서버(uvicorn·compose) 테스트는 서버를 띄울 때
환경 변수로 넘긴다(compose의 app1~3은 `${QUEUE_BATCH:-100}`처럼 호스트 값을 받아 쓴다).

## ① 번호표: `POST /concerts/{concert_id}/queue`

- `X-User-Id` 필수(없음·빈 값·NUL → 401 `USER_ID_REQUIRED`). 없는 공연(NUL 포함) → 404 `CONCERT_NOT_FOUND`.
- 공연별 대기열. 번호는 1부터 Redis `INCR`로 준다.
- **같은 사용자는 같은 번호**: 그 사용자의 번호가 WAITING이거나 ADMITTED(입장권 유효)인 동안 다시 부르면(두 탭에서 동시에 불러도)
  같은 번호를 돌려준다. "이미 있는지 확인 → 없으면 INCR → 사용자에 기록"은 **Lua 하나로 원자적으로** 한다.
  동시에 온 같은 사용자 요청 때문에 번호가 빠지면(INCR만 되고 아무에게도 안 간 번호) 안 된다.
  → 서로 다른 사용자 N명이 줄 서면 번호는 정확히 1..N (겹침·빠짐 없음).
- 입장권이 만료(EXPIRED)된 사용자가 다시 부르면 맨 뒤의 새 번호를 받는다(201).
- 응답: 새 번호 201, 기존 번호 200. 본문은 `GET .../queue/me`와 같은 `QueueStatus`(바로 입장 가능하면 입장권 포함).

## ② 전광판(serving counter)

공연별 Redis 값 두 개: `serving`(이 번호까지 입장 가능)과 `anchor`(마지막으로 올린 시각). 백그라운드 작업 없이
대기열 요청(POST·GET me)이 올 때 Lua가 **넘겨받은 현재 시각(`get_now`)으로** 따라잡는다. Redis `TIME`·TTL로 판단하지 않는다.

- 대기열이 처음 생길 때(첫 번호표): `serving = QUEUE_BATCH`, `anchor = now`. → 처음 100명은 바로 입장.
- 매 요청: `ticks = floor((now - anchor) / INTERVAL)`(음수면 0). `ticks > 0`이면
  `serving = min(serving + ticks × BATCH, issued + BATCH)`, `anchor = anchor + ticks × INTERVAL`.
  (`issued` = 지금까지 준 마지막 번호. 쉬는 동안 쌓이는 입장 여유는 한 묶음까지만 — 한산할 때 온 사람은 바로 들어가고,
  오래 쉰 뒤 몰려도 한 번에 한 묶음만 들어간다.) `serving`은 줄지 않는다.
- 테스트는 가짜 시계를 `QUEUE_INTERVAL_SECONDS`만큼 옮기면 전광판이 한 묶음 오른다.

## ③ 내 상태: `GET /concerts/{concert_id}/queue/me`

- `X-User-Id` 필수(401). 이 공연에 내 번호가 없으면(없는 공연 포함) 404 `QUEUE_TICKET_NOT_FOUND`.
- 본문 `QueueStatus`:
  - `concert_id`, `number`(내 번호), `serving`(따라잡은 뒤 전광판), `status`: `WAITING | ADMITTED | EXPIRED`
  - `ahead`: WAITING이면 `number - serving - 1`, 아니면 0
  - `retry_after`: WAITING이면 다음 전광판 상승까지 남은 초 `ceil(anchor + INTERVAL - now)`(최소 1), 아니면 null.
    WAITING 응답에는 같은 값의 **`Retry-After` 헤더**도 붙인다(다른 상태에는 없음).
  - `entry_token`, `entry_expires_at`: ADMITTED일 때만 값, 아니면 null.
- `number ≤ serving`이 되면 ADMITTED. **처음 ADMITTED로 판단한 순간** 입장권을 발급한다: 만료 = 그 순간 `get_now` + `ENTRY_TTL_SECONDS`.
  유효한 동안 다시 물으면 같은 만료 시각의 입장권을 준다(물을 때마다 연장되지 않음).
- `get_now() >= entry_expires_at`이면 EXPIRED(입장권 null). 이때 POST를 다시 하면 새 번호(①).

## ④ 입장권 검사: 좌석맵·홀드

- 헤더 **`X-Entry-Token`**. 형식은 server가 정하되, 사용자·공연·만료 시각을 담고 `ENTRY_TOKEN_SECRET`으로 서명(HMAC-SHA256 등)해
  **Redis 없이 검증**할 수 있어야 한다. 위조·변조된 값은 통과하면 안 된다.
- 판단(현재 시각은 `get_now`):
  - 헤더 없음·빈 값 → 403 `ENTRY_TOKEN_REQUIRED`
  - 서명 불일치·형식 오류, 다른 사용자(`X-User-Id`와 다름)의 입장권, 다른 공연의 입장권 → 403 `ENTRY_TOKEN_INVALID`
  - `get_now() >= 만료 시각` → 403 `ENTRY_TOKEN_EXPIRED`
- **`GET /concerts/{id}/seats`**: 이제 `X-User-Id`도 필요하다. 순서: 401 → 404 `CONCERT_NOT_FOUND`(NUL 포함, 지금과 같은 404) → 403 입장권 →
  캐시(8장) → DB. 403은 좌석맵 캐시·DB 좌석 조회 전에 판단한다(공연 존재 확인은 가벼운 조회).
- **`POST /seats/{id}/hold`**: 순서: 401 → 403 입장권(없음·위조·남의 것·만료·**다른 공연**; 모두 Redis 관문 전, `X-Hold-Gate` 헤더 없음) →
  Redis 관문(8장) → DB. 좌석이 없으면 지금처럼 DB 단계의 404 `SEAT_NOT_FOUND`(`X-Hold-Gate: DB`).
  - **다른 공연의 입장권은 좌석 상태·Redis 표시와 상관없이 언제나 403 `ENTRY_TOKEN_INVALID`.** (리뷰 지적: 공연 비교를 DB 단계에서
    하면 관문이 그 좌석의 HELD·SOLD 표시로 먼저 409를 내서, 같은 요청의 답이 Redis 상태에 따라 403/409로 달라졌다.)
    좌석 → 공연 대응은 바뀌지 않는 값이므로 관문 전에 확인한다(가벼운 DB 조회, 또는 앱 메모리·Redis에 기억해 둔 값. 방법은 server).
    알 수 없는 좌석(없는 ID)은 이 검사를 건너뛰고 지금처럼 관문 → DB 404로 간다.
- 입장권이 필요 없는 경로(지금 그대로): `GET /concerts`, `POST /bookings`, `GET /bookings/{id}`, `POST /payments/callback`.
  홀드한 뒤 입장권이 만료돼도 홀드(5분)·확정·콜백은 영향받지 않는다.

### 테스트용 접점: 입장권 직접 발급

`backend.app.queue.issue_entry_token(user_id: str, concert_id: str, expires_at: datetime) -> str`
— 환경 변수 `ENTRY_TOKEN_SECRET`(없으면 기본값)으로 대기열이 주는 것과 **같은 형식·같은 서명**의 입장권을 만든다. HTTP 경로가 아니다.
테스트 헬퍼는 **Redis가 멈춰 대기열이 503일 때만** 이것으로 입장권을 만든다(8장 Redis 정지 시험이 Redis 없이 띄운 서버에서
홀드하려면 필요). 그 밖에는 반드시 대기열 경로로 받는다.

## ⑤ Redis가 없을 때

- 대기열 경로(POST queue, GET me)는 Redis 없이 판단할 수 없으므로 **503 `QUEUE_UNAVAILABLE`**(5xx 예외가 아닌 정해진 본문).
  새 입장은 막힌다.
- 이미 받은 입장권은 서명으로 검증하므로 좌석맵·홀드는 8장처럼 DB로 계속 동작한다.
- `reset_db()`의 `FLUSHDB`가 대기열도 지운다(테스트마다 새 대기열).

## ⑥ 확인 (tests/queue) — test가 먼저 쓰고(RED), lead가 해시를 기록한 뒤 server가 GREEN

1. **번호 정합성**: 서로 다른 사용자 1000명이 동시에 줄 서면 번호는 정확히 1..1000(겹침·빠짐 없음).
   실서버(uvicorn 워커 2개 이상, 8장 `run_server`류) 권장. 같은 1000명이 다시 부르면 같은 번호.
2. **같은 사용자**: 한 사용자가 동시에 여러 번(두 탭) 불러도 같은 번호이고, 그 뒤 다른 사용자의 번호는 바로 다음 번호(빠짐 없음).
3. **전광판 전에는 입장권 없음**: (작은 `QUEUE_BATCH`로) 전광판 밖 번호는 WAITING, `entry_token` null, `ahead`·`retry_after`·
   `Retry-After` 헤더가 계약대로. 입장권 없이 좌석맵·홀드 → 403 `ENTRY_TOKEN_REQUIRED`. 가짜 시계를 간격만큼 옮기면 다음 묶음이
   ADMITTED가 되고 입장권으로 좌석맵 200·홀드 201.
4. **만료·남의 입장권**: 만료 시각(2분 정각)에 좌석맵·홀드 403 `ENTRY_TOKEN_EXPIRED`(1분 59초는 통과), 다른 사용자의 입장권 403
   `ENTRY_TOKEN_INVALID`, 변조한 입장권 403 `ENTRY_TOKEN_INVALID`. 거절된 홀드는 좌석을 바꾸지 않는다. 만료 뒤 다시 줄 서면 새 번호.
5. **Redis 정지**: 대기열 경로 503 `QUEUE_UNAVAILABLE`, 미리 받은 입장권으로 좌석맵·홀드는 동작(8장 Redis 정지 시험처럼 다른
   자리 테스트와 겹치지 않게, 끝나면 redis를 다시 켠다).
6. **기존 테스트**: `tests/api`, `tests/concurrency`, `tests/cache`, `tests/contract`(, `tests/infra`)는 헬퍼가 입장권을 받아 붙이는 것 외에는
   기대값을 바꾸지 않고 통과한다. 가짜 시계를 2분 넘게 옮기는 테스트는 헬퍼가 입장권을 다시 받는다. 실서버 테스트는
   큰 `QUEUE_BATCH`·`ENTRY_TTL_SECONDS`를 넘겨 기다림 없이 입장한다. 기대값을 바꿔야 하는 기존 테스트가 있으면 이유와 함께 lead에게 먼저 알린다.

## 범위 밖

- 대기열 이탈 처리(창을 닫은 사람 건너뛰기), 봇 차단, 입장권 1회용·동시 사용 제한, 사용자당 좌석 수 제한.
- 입장권 만료 뒤 진행 중인 홀드·확정을 끊는 것.
