# 5장 · 동시성 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
5장은 동시성이야. ① test에게 tests/concurrency/에 두 가지 테스트를 쓰게 해 줘. 같은 좌석에 서로 다른 사용자 500명이 동시에 홀드하면 201은 정확히 1건, 나머지는 409, 5xx는 0건이어야 해. 같은 사람이 예매 확정을 동시에 20번 보내도 예매는 1건이어야 해. 진짜 동시에 겹치도록 실제 서버(uvicorn 워커 여러 개)와 PostgreSQL로 돌려. ② 지금 서버는 이미 잠금(FOR UPDATE)을 쓰고 있으니, 테스트가 겹침을 정말 잡는지 보려고 잠금을 잠깐 뺀 상태로 돌려서 실패(RED)하는 걸 먼저 확인하고 되돌려 줘. ③ 그다음 server에게 홀드를 조건부 UPDATE 한 번으로 바꾼 버전과 지금의 FOR UPDATE 버전을 같은 500건으로 비교하게 해서 p50·p95와 성공·충돌 수를 docs/concurrency.md에 표로 남겨. 더 단순하고 빠른 쪽을 남기고, 예매 표에는 좌석당 예매가 하나만 생기도록 UNIQUE 제약을 마이그레이션으로 추가해. ④ 마지막으로 review에게 동시성 관점으로 검토를 맡겨 줘.
```

## 사람이 셸에서 친 명령

```bash
uv run pytest tests/concurrency -q
sed -n '/## 비교/,/^$/p' docs/concurrency.md
uv run python scripts/story_report.py tests
python3 scripts/verify_tests.py check
git add .
git commit -qm "5장 동시성"
git log --oneline
```
