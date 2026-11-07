# 6장 · 홀드 만료와 모의 결제 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
6장은 홀드 만료와 모의 결제야. ① PRD의 열린 질문 2·3·4에 답을 정해서 PRD와 스토리에 반영하고 새 스토리 US-05(모의 결제 콜백)를 써 줘. 자기 홀드를 다시 홀드해도 만료 시각은 늘리지 않고, 모의 결제는 성공과 실패를 흉내 낼 수 있게 하고, 이미 확정한 예매를 같은 사람이 다시 요청하면 같은 예매를 돌려줘. ② 결제를 두 단계로 바꿔. 확정 요청은 Idempotency-Key 헤더를 받아 결제 대기(PENDING) 예매를 만들고, 모의 결제사가 나중에 성공이나 실패 콜백을 보내면 그때 좌석이 SOLD가 돼. 같은 키로 다시 보내면 같은 응답, 같은 콜백이 두 번 와도 한 번만 반영, 홀드가 만료된 뒤 도착한 성공 콜백은 좌석을 팔지 말고 환불 필요로 기록해. 시각은 모두 서버 시각만 써. ③ test에게 경계 테스트를 먼저 쓰게 해서 RED를 확인하고 해시를 기록해. 만료 4분 59초·5분 정각·5분 1초, 남의 홀드로 확정하면 403, 같은 키 재요청, 중복 콜백, 늦은 콜백이야. ④ openapi.yaml도 같이 고치고, server가 구현해서 GREEN을 만든 다음, review에게 결제 흐름 검토를 맡겨 줘.
```

## 사람이 셸에서 친 명령

```bash
sed -n '38,48p' docs/PRD.md
uv run pytest tests/api/test_us05_callback.py -q
uv run python scripts/story_report.py tests
python3 scripts/verify_tests.py check
git add .
git commit -qm "6장 홀드 만료와 모의 결제"
git log --oneline
```
