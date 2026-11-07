# 7장 Nginx로 서버 세 대 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
7장은 서버를 세 대로 늘리고 앞에 Nginx를 세우는 거야. ① docker-compose.yml에 같은 이미지로 app1·app2·app3과 nginx를 추가해서 8080 포트 하나로 받게 하고, 응답 헤더 X-App-Id로 어느 앱이 받았는지 보이게 해 줘. ② Nginx에 사용자(X-User-Id)별로 초당 5건, burst 10의 속도 제한을 걸고, 넘으면 429와 Retry-After, 우리 오류 모양(code RATE_LIMITED)으로 답하게 해. ③ 앱 하나가 죽으면 조회는 다른 앱으로 넘기되, 홀드·확정 같은 POST는 Nginx가 자동으로 다시 보내지 않게 해. 대신 같은 Idempotency-Key로 다시 보내면 중복 예매가 없어야 해. ④ test에게 tests/infra에 확인 테스트를 먼저 쓰게 해서 RED를 확인하고 해시를 기록해. 요청 30번이 세 앱에 고르게 나뉘는지, 30건을 한꺼번에 보내면 429가 나오는지, app2를 멈춘 뒤 10초 안에 나머지가 받는지, 같은 키 재시도로 예매가 하나인지야. ⑤ server가 구현해서 GREEN을 만든 다음, review에게 설정 검토를 맡겨 줘.
```

## 사람이 셸에서 친 명령

```bash
docker compose ps --format '{{.Name}}  {{.Status}}'
for i in $(seq 30); do curl -s -D - -o /dev/null -H "X-User-Id: u$i" localhost:8080/concerts | grep -i x-app-id; done | sort | uniq -c
seq 30 | xargs -P30 -I{} curl -s -o /dev/null -w '%{http_code}\n' -H 'X-User-Id: fan' localhost:8080/concerts | sort | uniq -c
seq 15 | xargs -P15 -I{} curl -s -o /dev/null -H 'X-User-Id: fan2' localhost:8080/concerts; curl -s -i -H 'X-User-Id: fan2' localhost:8080/concerts | grep -iE 'HTTP/|retry-after|RATE'
uv run pytest tests/infra -q
uv run python scripts/story_report.py tests
python3 scripts/verify_tests.py check
git add .
git commit -qm "7장 Nginx와 서버 세 대"
git log --oneline
```
