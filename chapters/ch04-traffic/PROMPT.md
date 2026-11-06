# 4장 · 트래픽과 서버 부하 계산 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
트래픽 계산 시트를 만들자. docs/capacity.md에 대기열 진입, 순번 확인(폴링), 좌석 조회, 홀드, 모의 결제, DB 작업을 행으로 나눠서 행마다 사람 수·요청 수·기간·초당 요청 수를 적어 줘. 입력값은 모두 [가정]으로 표시해: 좌석 5만 석, 오픈 1분 안에 100만 명 접속, 5초마다 순번 확인, 초당 200명 입장, 입장한 사람은 좌석 조회 2번·홀드 2번, 그중 40%만 결제 1번. 계산은 scripts/capacity.py가 하게 하고, test에게 계산 공식 테스트를 먼저 쓰게 해서 실패(RED)를 확인한 뒤 통과(GREEN)시켜 줘. 서버 1대가 몇 건을 버티는지는 아직 모르니 [실측 필요]로 비워 두고, docs/PRD.md에 성능 목표 초안 NFR-01을 숫자 칸만 비운 채 추가해 줘.
```

## 사람이 셸에서 친 명령

```bash
uv run python scripts/capacity.py
uv run pytest -q
python3 scripts/verify_tests.py check
git add .
git commit -qm "4장 트래픽 계산 시트"
git log --oneline
```
