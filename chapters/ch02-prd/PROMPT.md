# 2장 · PRD · 유저 스토리 · API 계약 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
test가 이 컴퓨터에 pytest가 없다고 했어. 누구나 같은 환경을 만들 수 있게 uv로 루트에 pyproject.toml(개발용 pytest·pyyaml)을 만들고, 테스트는 uv run pytest로 돌게 해 줘. CLAUDE.md와 AGENTS.md에도 실행법을 한 줄 적어 줘.
```

## 2. lead 칸

```text
docs/PRD.md에 1차 범위(MVP)를 써 줘. 공연 목록과 좌석맵 보기, 좌석 하나를 5분 동안 잡기(홀드), 모의 결제로 예매 확정까지. 좌석 상태는 AVAILABLE → HELD → SOLD이고, 만료된 HELD만 다시 잡을 수 있고 SOLD는 다시 잡을 수 없어. 로그인은 모의 사용자 헤더 X-User-Id로 대신해. 화면은 이번 범위에서 빼고, 나중에 API 계약 파일로 타입을 만들어 붙인다고 적어 줘. 유저 스토리는 docs/stories/에 US-01~US-04로 나눠서 Given/When/Then 인수 조건까지 써 줘. API 계약 파일은 아직 쓰지 마.
```

## 3. test 칸

```text
docs/stories를 읽고, API 계약이 지켜야 할 것을 tests/contract/test_openapi.py로 써 줘. docs/openapi.yaml을 읽어서 스토리마다 필요한 경로·메서드·성공 응답·오류 코드(예: 좌석이 이미 잡힘 409)가 있는지 검사하고, 각 테스트에 스토리 번호 표시를 달아 줘. 스토리별 통과표를 출력하는 scripts/story_report.py도 만들어 줘. 지금은 계약 파일이 없으니 uv run으로 실행해서 실패(RED)하는 걸 확인해 줘.
```

## 4. lead 칸

```text
tests/contract 테스트가 통과하도록 docs/openapi.yaml을 써 줘. 테스트 파일은 고치지 말고, 쓰기 전에 RED를 확인하고 해시를 기록한 뒤, 다 쓰면 테스트를 돌려서 결과를 알려 줘.
```

## 사람이 셸에서 친 명령

```bash
uv run python scripts/story_report.py
uv run python scripts/story_report.py
python3 scripts/verify_tests.py check
git add .
git commit -qm "2장 PRD·스토리·API 계약"
git log --oneline
```
