# ticketing-system-practice

콘서트 좌석 예매 시스템을 AI 에이전트 팀(Claude Code + OpenRig + Herdr)과 함께 만들며 백엔드 시스템 설계를 배우는 실습 저장소예요.

- 서버: Python FastAPI · PostgreSQL · Redis

## 장별 프롬프트

강의에서 실제로 입력한 프롬프트와 셸 명령은 장마다 `chapters/<장>/PROMPT.md`에 있어요. 영상의 프롬프트가 길면 여기서 복사하세요.

| 장 | 프롬프트 |
| --- | --- |
| 1장 팀 규칙 | [chapters/ch01-team/PROMPT.md](chapters/ch01-team/PROMPT.md) |
| 2장 PRD·스토리·API 계약 | [chapters/ch02-prd/PROMPT.md](chapters/ch02-prd/PROMPT.md) |
| 3장 첫 서버 | [chapters/ch03-mvp/PROMPT.md](chapters/ch03-mvp/PROMPT.md) |
| 4장 트래픽 계산 | [chapters/ch04-traffic/PROMPT.md](chapters/ch04-traffic/PROMPT.md) |
| 5장 동시성 | [chapters/ch05-concurrency/PROMPT.md](chapters/ch05-concurrency/PROMPT.md) |

## OpenRig 팀 띄우기

`rigs/tkt/rig.yaml`의 `NPM_ROOT`를 내 컴퓨터의 전역 npm 경로로 바꾼 뒤 띄우세요.

```bash
sed -i '' "s#NPM_ROOT#$(npm root -g)#" rigs/tkt/rig.yaml
```
