# 10장 계약: 예매 확정 알림 (outbox → Kafka → 알림함)

목표: 예매가 CONFIRMED가 되면 사용자 앱 알림함에 알림을 한 줄 넣는다.
**알림이 늦거나 두 번 와도 예매는 절대 틀리면 안 된다.** 예매 판단의 기준은 여전히 PostgreSQL이고,
Kafka가 멈춰도 결제 콜백(확정)은 지금처럼 성공한다. 알림은 "적어도 한 번" 전달하고, 받는 쪽에서 중복을 없앤다.

```
결제 콜백 ─(한 트랜잭션)─> bookings.status=CONFIRMED + outbox 한 줄
릴레이 ─ outbox의 안 보낸 줄 ─> Kafka booking-events (키 = 공연 id) ─ 보낸 줄 표시
notifier 소비자 그룹 ─ booking-events ─> notifications 한 줄(사용자별, 이벤트당 하나) ─ 그다음 오프셋 커밋
```

## 파일과 소유 (10장 한정)

| 파일 | 소유 |
|---|---|
| `backend/`(마이그레이션, 릴레이, notifier), `docker-compose.yml`(`kafka` 서비스) | server |
| `tests/events/`(새 확인 테스트와 픽스처) | test |
| `docs/events.md`, `docs/backend-interface.md`, `docs/stories/US-09-*.md`, `pyproject.toml`·`uv.lock`(`confluent-kafka` 추가), `scripts/test-hashes.json` | lead |

## ① Kafka (docker compose `events` 프로필)

- 서비스 이름 `kafka`, `profiles: ["events"]`. 기본 `docker compose up`에는 뜨지 않는다. 띄우기: `docker compose --profile events up -d --wait kafka`.
- 브로커 1대, **KRaft 모드**(ZooKeeper 없음). 이미지는 server가 정한다(공식 `apache/kafka` 권장, 태그 고정).
- 호스트에서 `localhost:9092`로 접속할 수 있어야 한다(advertised listener가 `localhost:9092`). 컨테이너 안에서 쓸 리스너는 선택이다.
- 토픽 `booking-events`: 파티션 3개, 복제 1. 만드는 책임은 앱이다. 릴레이 시작 때 없으면 만든다(브로커 자동 생성에 기대지 않는다).
- 데이터 볼륨은 두지 않는다(실습용, 컨테이너를 다시 만들면 비워진다). 테스트는 `stop`/`start`만 하고 다시 만들지 않는다.
- healthcheck가 있어 `--wait`가 브로커 준비까지 기다린다.

## 설정 (환경 변수)

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `KAFKA_BOOTSTRAP` | `localhost:9092` | 브로커 주소 |
| `BOOKING_EVENTS_TOPIC` | `booking-events` | 토픽 이름 |
| `NOTIFIER_GROUP` | `notifier` | 소비자 그룹 id |

함수는 호출될 때 환경 변수를 읽는다. 테스트는 `monkeypatch.setenv`로 바꿀 수 있다.

## ② outbox: 확정과 같은 트랜잭션

- `POST /payments/callback`이 예매를 PENDING → CONFIRMED로 바꾸는 **같은 DB 트랜잭션** 안에서 `outbox`에 한 줄을 쓴다.
  확정이 롤백되면 outbox 줄도 없고, 확정이 커밋되면 outbox 줄도 반드시 있다.
- **이벤트는 확정마다 한 번**이다. 중복·동시 성공 콜백, 반대 결과 콜백, FAILED·REFUND_REQUIRED 처리는 outbox 줄을 만들지 않는다(DB 제약으로도 막는다. 예: `(booking_id, event_type)` UNIQUE).
- 콜백 요청은 Kafka에 접속하지 않는다. Kafka가 꺼져 있어도 콜백의 응답·시간·결과는 9장과 같다.
- 테스트가 DB에서 직접 보는 열(이름 고정): 테이블 `outbox`

| 열 | 뜻 |
|---|---|
| `event_id` | 이벤트 id(문자열, UNIQUE). **전역 유일(UUID 등)**. `reset_db` 뒤 1부터 다시 세는 일련번호는 안 된다. Kafka에 남은 이전 테스트의 메시지와 겹치지 않아야 한다 |
| `booking_id` | 확정된 예매 id |
| `sent_at` | 릴레이가 Kafka 전송 확인을 받고 표시한 시각. 안 보냈으면 NULL |

그 밖의 열(본문 JSON, 생성 시각, 순서용 일련번호 등)은 server가 정한다.

### 이벤트 메시지 (Kafka 값, UTF-8 JSON)

```json
{"event_id": "…", "type": "BookingConfirmed", "booking_id": "…", "user_id": "…",
 "concert_id": "…", "seat_id": "…", "confirmed_at": "2026-…Z"}
```
- 키: `concert_id`(UTF-8). 같은 공연의 이벤트는 한 파티션으로 가서, **Kafka에 들어간 순서대로** 소비된다.
  릴레이가 여러 개 동시에 돌면 같은 공연이라도 outbox 순서(확정 순서)와 Kafka 순서가 다를 수 있다. 지금 이벤트는 확정 하나뿐이고
  중복은 `event_id`로 없애므로 결과에 영향이 없다. 순서가 중요한 이벤트(예: 취소)를 더하려면 설계를 넓혀야 한다(10장 리뷰).
- `confirmed_at`은 확정할 때의 `get_now()` 값(ISO 8601, UTC)이다.
- 알 수 없는 `type`이나 JSON이 아닌 메시지는 notifier가 알림 없이 건너뛰고 오프셋을 넘긴다. 이런 메시지 때문에 소비가 멈추면 안 된다.

## ③ 릴레이

- 테스트 접점 `backend.app.events.relay_once(limit: int = 100) -> int`
  - `sent_at IS NULL`인 줄을 오래된 순서로 최대 `limit`개 읽어 `booking-events`에 보낸다.
    프로듀서는 `acks=all`, idempotence를 켠다.
  - **브로커의 전송 확인(delivery report)을 받은 줄만** `sent_at`을 채운다. 표시한 줄 수를 돌려준다.
  - Kafka에 닿지 못하면 어떤 줄도 표시하지 않고, 예외를 내거나 0을 돌려준다(둘 다 허용). 10초 안에 돌아와야 한다(무한 대기 금지).
    다음 호출이 그 줄을 다시 보낸다.
  - 여러 릴레이가 동시에 돌아도 된다(예: `FOR UPDATE SKIP LOCKED`). 그래도 같은 줄이 두 번 나갈 수는 있다(전송 뒤 표시 전에 죽은 경우). 이 중복은 notifier가 없앤다.
- 상시 실행: `python -m backend.app.relay`(간격을 두고 `relay_once` 반복). compose 서비스로 띄우는 것은 이번 장 범위 밖이다.

## ④ notifier 소비자 (그룹 `notifier`)

- 테스트 접점 `backend.app.notifier.Notifier()`
  - `poll_once(timeout: float = 5.0) -> int`: `timeout` 안에 메시지를 받아 처리하고, 이번에 처리한(건너뛴 것 포함) 메시지 수를 돌려준다.
  - `close()`: 소비자를 닫는다. 닫은 뒤 새 `Notifier()`는 **커밋된 오프셋부터 이어 받는다**.
  - 설정: `group.id = NOTIFIER_GROUP`, `enable.auto.commit = false`, 커밋된 오프셋이 없으면 `earliest`부터 읽는다.
- 처리 순서(바꾸면 안 된다): 메시지 하나(또는 묶음)를 **notifications에 쓰고 DB 커밋한 뒤에** 그 오프셋을 동기 커밋한다.
  DB 쓰기가 실패하면 오프셋을 커밋하지 않는다(다음에 다시 받는다).
- **같은 이벤트가 두 번 와도 알림은 하나**: `notifications.event_id` UNIQUE + `INSERT … ON CONFLICT DO NOTHING`처럼 DB가 중복을 막는다.
  메모리 집합이나 Redis만으로 막는 것은 안 된다(프로세스를 다시 켜면 잊는다).
- 상시 실행: `python -m backend.app.notifier`(`poll_once` 반복).
- 테스트가 DB에서 직접 보는 열(이름 고정): 테이블 `notifications`

| 열 | 뜻 |
|---|---|
| `user_id` | 알림을 받을 사용자(= 예매한 사용자) |
| `event_id` | 원인 이벤트 id(UNIQUE) |
| `booking_id` | 확정된 예매 id |
| `created_at` | 알림을 넣은 시각 |

알림 문구 등 나머지 열은 server가 정한다. 알림함 조회 API는 이번 장 범위 밖이다.

## reset_db와 테스트 격리

- `reset_db()`는 `outbox`와 `notifications`도 비운다. **Kafka 토픽과 소비자 그룹 오프셋은 지우지 않는다.**
- 그래서 토픽에는 이전 테스트의 메시지가 남을 수 있고, notifier가 그 메시지로 알림을 넣을 수 있다.
  **테스트는 알림 수를 전체로 세지 않고, 테스트마다 새로 만든 사용자 id(UUID 접미사)와 그 테스트의 `booking_id`·`event_id`로 센다.**
- 이전 메시지가 많이 쌓여도 테스트가 느려지지 않도록, 테스트 픽스처는 시작할 때 notifier를 한 번 끝까지 비워 둘 수 있다(`poll_once`가 0을 돌려줄 때까지).
- tests/events는 공유 DB를 지우고 `docker compose stop/start kafka`를 하므로 tests/infra처럼 **다른 자리의 테스트와 겹치지 않게 돌린다.**
  Kafka가 안 떠 있으면 skip이 아니라 실패한다.

## ⑤ 확인 (tests/events, 스토리 US-09)

1. **확정 1건 → 알림 1건**: 콜백으로 CONFIRMED → outbox 1줄(`sent_at` NULL) → `relay_once` → 그 줄 `sent_at` 채워짐 →
   notifier가 처리 → 그 사용자·예매의 알림 정확히 1건. 중복 성공 콜백을 더 보내도 outbox는 1줄이다. 실패·늦은 콜백(FAILED·REFUND_REQUIRED)은 outbox 0줄이다.
2. **Kafka가 잠깐 멈춤**: `docker compose stop kafka` 상태에서 성공 콜백 → 200 CONFIRMED, 좌석 SOLD, outbox 1줄 미발송.
   `relay_once`는 표시하지 않는다. `docker compose start kafka` 뒤 `relay_once` → 표시 → 알림 1건.
3. **소비자를 껐다 켜도 이어 받음**: 여러 건 확정·릴레이 → `Notifier` 하나가 일부만 처리하고 `close()` → 더 확정·릴레이 →
   새 `Notifier()`가 이어 받아, 확정한 예매마다 알림이 정확히 1건(빠짐·중복 없음).
4. **같은 이벤트를 다시 보내도 알림이 늘지 않음**: 이미 처리한 이벤트 메시지를 테스트가 같은 토픽에 그대로 다시 보냄(프로듀서 직접 사용)
   또는 outbox `sent_at`을 NULL로 되돌리고 `relay_once` 재실행 → notifier 처리 뒤에도 그 이벤트의 알림은 1건.

## 범위 밖

- 이메일·푸시 같은 실제 발송, 알림 읽음 표시, 알림함 조회 API
- 릴레이·notifier를 compose 서비스로 상시 운영, 소비자 여러 대의 파티션 재분배 시험
- outbox 줄 정리(보관 기간)
