# 10장 예매 확정 알림: outbox → Kafka → 알림함 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
먼저 하나 고쳐 줘. 통과표(story_report)로 tests 전체를 한 번에 돌리면 tests/infra의 nginx 재시작 테스트가 from conftest import에서 tests/queue/conftest.py를 가져와 ImportError로 실패해. 폴더별로는 통과하지만 전체 실행에서 깨지니까, 테스트를 먼저 고치고 전체 실행으로 확인해 줘. 그다음 10장은 예매가 확정되면 알림을 보내는 거야. 알림이 늦거나 두 번 가도 예매는 절대 틀리면 안 돼. ① docker-compose.yml에 events 프로필로 Kafka 1대(KRaft)를 추가해. ② 예매가 CONFIRMED가 되는 같은 DB 트랜잭션 안에서 outbox 테이블에 이벤트를 한 줄 써. ③ 릴레이가 outbox에서 아직 안 보낸 줄을 읽어 booking-events 토픽에 공연 id를 키로 보내고, 보낸 줄은 표시해. ④ notifier 소비자 그룹이 토픽을 읽어 앱 알림함(notifications 테이블)에 사용자별로 한 줄 넣어. 같은 이벤트가 두 번 와도 알림은 하나여야 하고, 처리한 뒤에 오프셋을 커밋해. ⑤ test에게 tests/events에 확인 테스트를 먼저 쓰게 해서 RED를 확인하고 해시를 기록해. 확정 1건이면 알림 1건, Kafka가 잠깐 멈춰도 예매 확정은 성공하고 다시 켜지면 알림이 오는지, 소비자를 껐다 켜도 빠진 것 없이 이어 받는지, 같은 이벤트를 다시 보내도 알림이 늘지 않는지야. ⑥ server가 구현해서 GREEN을 만든 다음, review에게 outbox와 중복 처리 검토를 맡겨 줘.
```

## 사람이 셸에서 친 명령

```bash
docker compose --profile events ps --format '{{.Name}}  {{.Status}}'
docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic booking-events
uv run pytest tests/events -v 2>&1 | grep -E 'PASSED|FAILED|passed|failed' | sed 's/.*:://'
docker compose exec -T kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group notifier
uv run python scripts/story_report.py tests
python3 scripts/verify_tests.py check
git log --oneline | head -5
```
