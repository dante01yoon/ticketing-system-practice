# 9장 가상 대기열과 입장권 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
9장은 가상 대기열이야. ① Redis로 공연별 대기열을 만들어. POST /concerts/{id}/queue는 INCR로 번호표를 주고, 같은 사용자가 다시 부르거나 두 탭에서 불러도 같은 번호를 돌려줘. ② 전광판 숫자(serving counter)를 두고 정해진 간격마다 몇 명씩 올려. 테스트에서는 6장의 가짜 시계로 시간을 움직여 올릴 수 있게 해 줘. GET /concerts/{id}/queue/me는 내 번호, 전광판 숫자, 내 앞에 남은 사람 수, 다음에 물어볼 때까지 기다릴 초(Retry-After)를 돌려줘. ③ 내 차례가 되면 2분짜리 입장권을 주고, 좌석 조회와 홀드는 그 입장권이 있어야 하게 해. 만료된 입장권과 다른 사람의 입장권은 403이야. ④ test에게 tests/queue에 확인 테스트를 먼저 쓰게 해서 RED를 확인하고 해시를 기록해. 1000명이 동시에 줄 서도 번호가 겹치거나 빠지지 않는지, 같은 사용자는 같은 번호인지, 전광판이 닿기 전엔 입장권이 없는지, 만료·남의 입장권은 막히는지, 기존 테스트는 입장권을 붙여 그대로 통과하는지야. ⑤ server가 구현해서 GREEN을 만든 다음, review에게 대기열 검토를 맡겨 줘.
```

## 사람이 셸에서 친 명령

```bash
QUEUE_BATCH=3 docker compose up -d --wait app1 app2 app3
docker compose exec -T redis redis-cli flushdb; cid=$(curl -s -H 'X-User-Id: a' localhost:8080/concerts | python3 -c "import json,sys;print(json.load(sys.stdin)['items'][0]['id'])")
for u in u1 u2 u3 u4 u5; do curl -s -X POST -H "X-User-Id: $u" localhost:8080/concerts/$cid/queue; echo; done
curl -s -i -H 'X-User-Id: u5' localhost:8080/concerts/$cid/queue/me | grep -iE 'retry-after|^\{'
curl -s -i -H 'X-User-Id: u5' localhost:8080/concerts/$cid/seats | grep -E 'HTTP/|^\{'
sleep 11; tok=$(curl -s -H 'X-User-Id: u5' localhost:8080/concerts/$cid/queue/me | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['status'],d['entry_expires_at'],file=sys.stderr);print(d['entry_token'])")
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-User-Id: u5' -H "X-Entry-Token: $tok" localhost:8080/concerts/$cid/seats
uv run pytest tests/queue -q
docker compose up -d --wait app1 app2 app3
uv run python scripts/story_report.py tests
python3 scripts/verify_tests.py check
git log --oneline
```
