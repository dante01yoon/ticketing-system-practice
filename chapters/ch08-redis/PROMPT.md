# 8장 Redis 캐시와 홀드 관문 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
8장은 Redis를 붙이는 거야. ① docker-compose.yml에 redis를 추가하고 세 앱이 같은 Redis를 쓰게 해 줘. ② 좌석 조회(GET /concerts/{id}/seats)에 캐시 어사이드를 적용해. TTL은 5초, 응답 헤더 X-Cache로 HIT/MISS를 보이게 하고, 홀드·확정·만료로 좌석이 바뀌면 그 공연 캐시를 지워 줘. ③ 홀드 요청은 DB에 닿기 전에 Redis Lua 스크립트로 한 번에 확인하고 표시해서, 이미 누가 잡은 좌석이면 바로 409로 돌려보내. 진짜 기준은 PostgreSQL이야. DB에서 실패하면 Redis 표시를 되돌리고, Redis가 꺼져도 DB만으로 맞게 동작해야 해. ④ test에게 tests/cache에 확인 테스트를 먼저 쓰게 해서 RED를 확인하고 해시를 기록해. 처음 조회는 MISS·다음은 HIT인지, 홀드 뒤 바로 조회하면 바뀐 좌석이 보이는지, 같은 좌석에 동시 홀드 200건을 보내면 성공 1건이고 DB까지 간 요청이 확 줄어드는지, Redis를 멈춰도 5장 동시성 테스트가 그대로 통과하는지야. ⑤ server가 구현해서 GREEN을 만든 다음, review에게 캐시와 Lua 경계 검토를 맡겨 줘.
```

## 사람이 셸에서 친 명령

```bash
docker compose ps --format '{{.Name}}  {{.Status}}'
cid=$(curl -s -H 'X-User-Id: a' localhost:8080/concerts | python3 -c "import json,sys;print(json.load(sys.stdin)['items'][0]['id'])")
sleep 6; for i in 1 2 3; do curl -s -o /dev/null -D - -H 'X-User-Id: a' localhost:8080/concerts/$cid/seats | grep -iE 'x-cache|x-app'; done
docker compose exec -T redis redis-cli --scan; docker compose exec -T redis redis-cli pttl seatmap:$cid
uv run pytest tests/cache -q
uv run python scripts/story_report.py tests
python3 scripts/verify_tests.py check
git add .
git commit -qm "8장 Redis 캐시와 홀드 관문"
git log --oneline
```
