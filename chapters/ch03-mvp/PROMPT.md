# 3장 · 계약대로 첫 서버 만들기 (FastAPI · PostgreSQL · Docker Compose) — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
2장 계약대로 첫 서버를 만들자. 화면은 이번에도 빼. ① 먼저 test에게 tests/api/에 US-01~US-04 API 테스트를 쓰게 하고, 서버가 없으니 실패(RED)하는 걸 확인한 뒤 해시를 기록해 줘. ② 그다음 server에게 backend/에 FastAPI와 PostgreSQL로 계약대로 API를 만들게 해. PostgreSQL은 루트의 docker-compose.yml로 띄우고, 테이블을 만드는 마이그레이션과 시드(공연 1개·좌석 100석)를 넣어. ③ 테스트가 통과하면 review에게 계약과 구현이 맞는지 검토를 맡기고, 지적이 있으면 고친 뒤 결과를 알려 줘. 테스트는 uv run pytest로 돌려.
```

## 사람이 셸에서 친 명령

```bash
docker compose ps
curl -s localhost:8000/concerts
uv run python scripts/story_report.py tests
python3 scripts/verify_tests.py check
git add .
git commit -qm "3장 첫 서버(FastAPI·PostgreSQL)"
git log --oneline
uv run python -m backend.app.seed
uv run uvicorn backend.app.main:app --port 8000 > /tmp/server.log 2>&1 &
curl -s localhost:8000/concerts | jq
C=$(curl -s localhost:8000/concerts | jq -r '.items[0].id')
curl -s localhost:8000/concerts/$C/seats | jq -c '.seats[:3]'
S=$(curl -s localhost:8000/concerts/$C/seats | jq -r '.seats[0].id')
curl -s -w '\nHTTP %{http_code}\n' -X POST localhost:8000/seats/$S/hold -H 'X-User-Id: u1'
curl -s -w '\nHTTP %{http_code}\n' -X POST localhost:8000/seats/$S/hold -H 'X-User-Id: u2'
curl -s localhost:8000/concerts/$C/seats | jq -c '.seats[:3]'
kill %1
```
