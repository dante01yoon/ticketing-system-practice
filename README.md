# ticketing-system-practice

콘서트 좌석 예매 시스템을 AI 에이전트 팀(Claude Code + OpenRig + Herdr)과 함께 만들며 백엔드 시스템 설계를 배우는 실습 저장소예요.

- 서버: Python FastAPI · PostgreSQL · Redis · Nginx · Kafka · 쿠버네티스(minikube) · Locust

## 장별 프롬프트

강의에서 실제로 입력한 프롬프트와 셸 명령은 장마다 `chapters/<장>/PROMPT.md`에 있어요. 영상의 프롬프트가 길면 여기서 복사하세요.

| 장 | 프롬프트 |
| --- | --- |
| 1장 팀 규칙 | [chapters/ch01-team/PROMPT.md](chapters/ch01-team/PROMPT.md) |
| 2장 PRD·스토리·API 계약 | [chapters/ch02-prd/PROMPT.md](chapters/ch02-prd/PROMPT.md) |
| 3장 첫 서버 | [chapters/ch03-mvp/PROMPT.md](chapters/ch03-mvp/PROMPT.md) |
| 4장 트래픽 계산 | [chapters/ch04-traffic/PROMPT.md](chapters/ch04-traffic/PROMPT.md) |
| 5장 동시성 | [chapters/ch05-concurrency/PROMPT.md](chapters/ch05-concurrency/PROMPT.md) |
| 6장 홀드 만료·모의 결제 | [chapters/ch06-hold/PROMPT.md](chapters/ch06-hold/PROMPT.md) |
| 7장 Nginx·429 | [chapters/ch07-nginx/PROMPT.md](chapters/ch07-nginx/PROMPT.md) |
| 8장 Redis 캐시·홀드 관문 | [chapters/ch08-redis/PROMPT.md](chapters/ch08-redis/PROMPT.md) |
| 9장 가상 대기열 | [chapters/ch09-queue/PROMPT.md](chapters/ch09-queue/PROMPT.md) |
| 10장 Kafka 알림 | [chapters/ch10-kafka/PROMPT.md](chapters/ch10-kafka/PROMPT.md) |
| 11장 쿠버네티스·부하 테스트 | [chapters/ch11-k8s/PROMPT.md](chapters/ch11-k8s/PROMPT.md) |

## OpenRig 팀 띄우기

`rigs/tkt/rig.yaml`의 `NPM_ROOT`를 내 컴퓨터의 전역 npm 경로로 바꾼 뒤 띄우세요.

```bash
sed -i '' "s#NPM_ROOT#$(npm root -g)#" rigs/tkt/rig.yaml
```
