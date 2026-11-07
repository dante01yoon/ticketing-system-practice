# 11장 계약: 쿠버네티스(minikube)에 올리기

목표: Mac 한 대의 minikube(docker 드라이버)에 앱·PostgreSQL·Redis를 올린다. 앱은 1개로 시작해 CPU 70%를 넘으면
최대 3개까지 늘고, 부하가 끝나면 다시 1개로 줄어든다. Pod가 최대로 늘어나도(롤링 업데이트 중 하나 더 떠도)
DB 연결이 PostgreSQL `max_connections`를 넉넉히 밑돈다.

부하 테스트(Locust)는 사람이 직접 한다. 이 장의 테스트는 **정적 검사**와 **클러스터가 떠 있을 때만 도는 스모크**뿐이다.

## 파일과 소유 (11장 한정)

| 파일 | 소유 |
|---|---|
| `deploy/k8s/*.yaml`, `deploy/k8s/kustomization.yaml` | server |
| `backend/`(`GET /health`, `seed --if-empty`) | server |
| `tests/k8s/` (정적 검사 + 스모크 + `/health` 앱 내부 테스트) | test |
| `docs/k8s.md`, `docs/openapi.yaml`(`/health`), `docs/backend-interface.md`, `scripts/test-hashes.json` | lead |

`docker-compose.yml`과 7~10장 구성은 그대로 둔다(로컬 개발·`tests/infra`용). k8s는 그와 별개인 두 번째 배포 경로다.

## 범위 밖

- Kafka·릴레이·notifier(10장)는 k8s에 올리지 않는다. 확정은 그대로 성공하고 outbox 줄은 보내지 않은 채 쌓인다(10장 계약상 정상).
- Nginx·Ingress, 속도 제한(7장 ②). Service가 Pod들에 나눈다.
- 실제 레지스트리, TLS, 여러 노드, PostgreSQL·Redis 복제. 부하 테스트 자체.

## 실행 순서 (사람·lead·server 공통)

```bash
minikube start --driver=docker --cpus=4 --memory=4096
minikube addons enable metrics-server
minikube image build -t ticketing-app:ch11 -f deploy/app.Dockerfile .   # 레지스트리 없이 클러스터 안에 이미지
kubectl apply -k deploy/k8s                                             # 네임스페이스 ticketing 에 전부
kubectl -n ticketing wait --for=condition=complete job/db-init --timeout=180s
kubectl -n ticketing rollout status deploy/app --timeout=180s
kubectl -n ticketing port-forward svc/app 8081:80                       # 호스트에서 http://localhost:8081
uv run pytest tests/k8s                                                 # 정적 검사 + (클러스터가 있으면) 스모크
```

Locust는 `port-forward` 주소를 쓰면 **Pod 하나에만** 붙는다(port-forward는 연결 시점에 Pod 하나를 고른다).
호스트의 Locust로 늘어난 Pod들에 나누려면 **`minikube service -n ticketing app --url`** 이 출력한 주소를 쓴다
(그 터미널을 열어 둔 동안만 터널 유지). minikube v1.39 docker 드라이버는 ClusterIP Service에도 노드 안 Service IP로 가는
ssh 터널(`-L <포트>:<ClusterIP>:80`)을 열어 kube-proxy를 거친다. lead가 Pod 2개 Ready 상태에서 이 주소로 새 연결 30번을 보내
15/15로 나뉘는 것을 확인했다(2026-11-06). 나뉨은 **연결 단위**다: Locust 사용자 하나는 keep-alive 연결 하나를 계속 쓰므로
사용자 수가 Pod 수보다 충분히 많아야 고르게 나뉘고, 이미 연결된 사용자는 새 Pod로 옮겨 가지 않는다(늘어난 뒤 새로 생기는 사용자·연결부터 나뉜다).
HPA 확인은 `kubectl -n ticketing get hpa app -w`.

## ① 매니페스트 (deploy/k8s)

모두 네임스페이스 `ticketing`. `kustomization.yaml`이 `namespace: ticketing`과 모든 리소스 파일을 나열해
`kubectl apply -k deploy/k8s` 한 번으로 전부 만든다. 파일 나누기는 server가 정한다. 테스트는 **파일 이름이 아니라 kind+name으로** 찾는다.

| kind / name | 요구 |
|---|---|
| `Namespace/ticketing` | |
| `Secret/ticketing-secrets` | DB 비밀번호, `ENTRY_TOKEN_SECRET`. 개발용 값(평문 커밋 허용, 실제 비밀 아님) |
| `ConfigMap/app-config` | 앱 환경 변수(아래). Secret 값은 넣지 않는다 |
| `StatefulSet/postgres` + `Service/postgres` | 이미지 `postgres:16`, replicas 1, `volumeClaimTemplates`(PVC, `ReadWriteOnce`, 1Gi 이상), 데이터 경로는 `PGDATA`를 마운트 아래 하위 폴더로(빈 폴더 아님 오류 회피). **`max_connections`를 args `-c max_connections=100`으로 명시**한다. readiness 프로브 `pg_isready`. Service 포트 5432 |
| `StatefulSet/redis` + `Service/redis` | 이미지 `redis:7.4-alpine`, replicas 1, `volumeClaimTemplates`(PVC), `/data` 마운트, `--appendonly yes`(PVC를 쓰는 의미). Service 포트 6379 |
| `Job/db-init` | 앱 이미지로 `python -m backend.app.seed --if-empty` 실행: 마이그레이션 head + 공연이 하나도 없을 때만 시드. **데이터를 지우지 않는다**(다시 돌려도 안전). Postgres가 준비될 때까지 기다리거나 재시도(`backoffLimit`)로 버틴다 |
| `Deployment/app` | 아래 |
| `Service/app` | `ClusterIP`, 포트 `80` → 컨테이너 `8000`, selector가 Deployment Pod 라벨과 일치 |
| `HorizontalPodAutoscaler/app` | ②에서 |

`Deployment/app`:
- `replicas: 1` (처음 1개). 전략 `RollingUpdate`, `maxSurge: 1`, `maxUnavailable: 0` (정수로 명시, 연결 예산 계산에 쓰인다).
- 이미지 `ticketing-app:ch11`(`latest` 금지), `imagePullPolicy: Never`(minikube 안에 빌드한 이미지만 쓴다. 없으면 바로 `ErrImageNeverPull`로 드러난다).
- 컨테이너 `command`를 매니페스트에 명시: `uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1`. 워커 수가 예산 계산에 들어가므로 Dockerfile CMD에 기대지 않는다.
- 프로브: `readinessProbe`·`livenessProbe` 모두 `httpGet` `/health` 포트 8000. liveness는 readiness보다 느슨하게(시작 지연·실패 허용 횟수를 더 크게) 둬서 느린 시작에 죽이지 않는다.
- initContainer `wait-for-db`(리뷰 반영): 앱과 같은 이미지로 `python -m backend.app.seed --wait-schema`. DB에 닿고 스키마가 alembic head가 될 때까지
  기다린 뒤 끝난다. 그래서 첫 배포에서 **스키마가 head가 되기 전에는** 앱 컨테이너가 뜨지 않아 Service에 들어가지 않는다(첫 배포 때 실제로 본
  `Connection refused` 500을 막는다). 보장하는 것은 마이그레이션 완료까지다: `db-init`은 마이그레이션 → 시드 순서라, 시드가 커밋되기 전
  잠깐 `/concerts`가 200 빈 목록일 수 있다(정상 응답이라 받아들임). 다른 컨테이너처럼 cpu·memory requests·limits를 둔다.
- **받아들인 한계**: `/health`는 DB를 보지 않으므로, 앱이 이미 떠 있는 동안 PostgreSQL이 재시작되면 그 사이 DB가 필요한 요청은 500이다
  (DB가 돌아오면 `pool_pre_ping`으로 다시 붙는다). readiness를 DB 확인 경로(`/ready` 503)로 나누면 막을 수 있지만, 이 장은 프로브를
  `/health` 하나로 두기로 했고 DB가 하나뿐이라 그때는 어느 Pod도 처리할 수 없어 Service에서 빼도 얻는 것이 적다.
- 자원: **모든 컨테이너**(app, postgres, redis, db-init)에 `requests`와 `limits`의 `cpu`·`memory`가 모두 있고 limits ≥ requests.
  권장 시작값: app requests `250m`/`256Mi`, limits `1`/`512Mi`. HPA 70%는 **requests 기준**이라 app CPU requests가 너무 크면 늘지 않고 너무 작으면 쉽게 늘어난다.
- 환경 변수: `APP_ID`는 `fieldRef: metadata.name`(Pod 이름. 응답 `X-App-Id`로 어느 Pod가 받았는지 보인다),
  `DATABASE_URL`(호스트 `postgres`), `REDIS_URL`(호스트 `redis`), `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `ENTRY_TOKEN_SECRET`(Secret에서),
  `QUEUE_BATCH=1000000`, `ENTRY_TTL_SECONDS=3600`. 대기열을 사실상 끈 값이다: 11장의 부하는 HPA를 보려는 것이므로 대기열이 막지 않게 한다
  (대기열을 켠 부하를 보려면 ConfigMap 값을 바꿔 다시 배포한다).

## ② metrics-server와 HPA

- metrics-server는 `minikube addons enable metrics-server`로 켠다(매니페스트에 넣지 않는다).
- `HorizontalPodAutoscaler/app`: `autoscaling/v2`, 대상 `Deployment/app`, `minReplicas: 1`, `maxReplicas: 3`,
  `metrics`에 `Resource` `cpu` `Utilization` `averageUtilization: 70`.
- 줄어드는 속도: 기본 안정화 창은 300초라 부하가 끝나고 5분 넘게 그대로다. 사람이 결과를 보기 쉽게
  `behavior.scaleDown.stabilizationWindowSeconds`를 **120초 이하**(권장 60)로 둔다. 늘어나는 쪽은 기본값.
- 주의: Deployment에 `replicas: 1`이 있으므로 HPA가 3으로 늘린 상태에서 `kubectl apply -k`를 다시 하면 1로 돌아갔다가 HPA가 다시 늘린다.
  부하 중에는 다시 apply하지 않는다.

## ③ DB 연결 예산

uvicorn 워커 프로세스마다 SQLAlchemy 풀이 따로 있다. Pod 하나의 최대 연결 = 워커 수 × (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`).

| 항목 | 값 |
|---|---|
| PostgreSQL `max_connections` | 100 (매니페스트에 명시) |
| `superuser_reserved_connections` (Postgres 기본) | 3 |
| Pod당 워커 | 1 |
| `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` | 8 + 4 = **12** |
| 동시에 떠 있을 수 있는 앱 Pod 최대 | HPA `maxReplicas` 3 + 롤링 업데이트 `maxSurge` 1 = **4** |
| 앱 최대 연결 A | 4 × 1 × 12 = **48** |
| 남는 연결 | 100 − 3 − 48 = **49** (db-init Job, `kubectl exec psql`, 디버깅, 끝나지 않은 연결 정리 지연) |

규칙(정적 테스트가 매니페스트 값으로 계산한다):
**A = (maxReplicas + maxSurge) × workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW) ≤ 0.6 × max_connections** (48 ≤ 60).
"넉넉히"를 60%로 정했다: 나머지 40%는 예약 연결·Job·운영 접속·종료 중인 Pod의 연결이 늦게 닫히는 몫이다.

- Pod 3개만 보면 3 × 12 = 36. 롤링 업데이트 중 새 Pod가 먼저 뜨는 몫(maxSurge)까지 넣어 48로 잡는다.
- 48은 **살아 있는(종료 중이 아닌) Pod** 기준이다. 쿠버네티스는 종료 중(Terminating) Pod를 maxSurge 계산에서 빼므로, 3개일 때 롤링 업데이트
  한 번의 최악은 새 Pod 4개 + 아직 연결을 닫는 중인 이전 Pod 3개 = 7 × 12 = **84**, 예약 3을 더해 87 < 100으로 버틴다
  (종료 중 Pod는 uvicorn이 끝나며 연결을 닫는다, 기본 유예 30초). 롤아웃을 연달아(이전 롤아웃의 Pod가 다 내려가기 전에) 겹쳐 하면 이 여유도 넘을 수 있으므로
  부하 중에는 롤아웃·재 apply를 하지 않는다(②의 주의와 같다).
- 풀(8)이 아니라 풀+overflow(12)를 쓴다: 부하 때는 overflow까지 연다.
- 풀이 꽉 차면 요청은 `DB_POOL_TIMEOUT`(기본 60초)까지 기다린다(`backend/app/db.py`). 연결 부족은 Postgres 오류가 아니라 앱 쪽 대기로 나타난다.
- HPA `maxReplicas`나 풀 크기를 올리려면 이 표와 규칙을 먼저 고친다.

## 앱 변경 (backend)

- `GET /health` → `200`, 본문 `{"status": "ok"}`. `X-User-Id`·입장권이 필요 없다. **DB·Redis를 보지 않는다**: liveness가 같은 경로라
  DB가 잠깐 끊겼을 때 모든 앱 Pod가 재시작되는 연쇄를 막는다(프로세스가 살아 요청을 받는지만 본다). 다른 응답처럼 `X-App-Id`가 붙는다.
- `python -m backend.app.seed --if-empty` / `backend.app.seed.seed_if_empty() -> bool`: `migrate_head()` 뒤 공연이 하나도 없을 때만
  `insert_seed()`하고 True, 이미 있으면 아무것도 바꾸지 않고 False. 아무것도 지우지 않는다(TRUNCATE·`cache.flush_db` 없음).
- `python -m backend.app.seed --wait-schema` / `backend.app.seed.wait_for_schema(timeout: float = 300.0, interval: float = 2.0) -> bool`:
  **마이그레이션하지 않는다.** DB에 접속되고 현재 alembic 리비전이 head와 같으면 True. 아니면(접속 실패·테이블 없음·리비전 다름)
  `interval`초마다 다시 보고 `timeout`초가 지나면 False. CLI는 True면 종료 코드 0, False면 1(initContainer가 실패하면 kubelet이 다시 시도한다).
  `timeout`은 **검사 사이의 재시도 기한**이지 전체 실행의 엄격한 상한이 아니다: 검사 하나(접속·쿼리)가 멎으면 그만큼 더 걸린다.
- 인자 없는 `python -m backend.app.seed`는 지금처럼 `reset_db(seed=True)`.

## ④ 확인 (tests/k8s)

**정적 검사**(클러스터 없이, 기본 `uv run pytest`에서 돈다): `deploy/k8s/*.yaml`을 읽어(여러 문서 YAML) kind+name으로 찾는다.
1. `kustomization.yaml`이 있고 `namespace: ticketing`, 나열한 리소스 파일이 모두 존재한다. 위 표의 리소스가 모두 있다.
2. `Deployment/app`: replicas 1, RollingUpdate `maxSurge` 1·`maxUnavailable` 0, 이미지 태그가 `latest`가 아니고 `imagePullPolicy: Never`,
   command에 `--workers 1`, readiness·liveness가 `httpGet /health` 8000, `APP_ID`가 `metadata.name` fieldRef, `DB_POOL_SIZE`·`DB_MAX_OVERFLOW`가 있다.
3. 모든 워크로드 컨테이너(Deployment, StatefulSet 2개, Job)에 cpu·memory requests·limits, limits ≥ requests.
4. `Service/app`: ClusterIP, 80 → 8000, selector가 Deployment 템플릿 라벨의 부분집합. `Service/postgres` 5432, `Service/redis` 6379.
5. `StatefulSet/postgres`·`StatefulSet/redis`가 `volumeClaimTemplates`를 갖고 그 볼륨을 컨테이너에 마운트한다. postgres args에 `max_connections`.
6. `HorizontalPodAutoscaler/app`: v2, 대상 Deployment/app, min 1, max 3, cpu Utilization 70, scaleDown 안정화 창 ≤ 120.
7. 연결 예산 규칙 ③ (값은 ConfigMap/env·HPA·Deployment·postgres args에서 읽는다. ConfigMap 참조(`configMapKeyRef`/`envFrom`)도 따라간다).
8. (리뷰 반영) `Deployment/app`에 initContainer `wait-for-db`: 이미지가 app 컨테이너와 같고, command에 `backend.app.seed`와 `--wait-schema`,
   cpu·memory requests·limits(limits ≥ requests).

**앱 내부 테스트**(클러스터 없이): `create_app()`에 `GET /health`가 헤더 없이 200 `{"status": "ok"}`.

**seed 테스트**(리뷰 반영, 로컬 테스트 DB `localhost:5432`, 기존 테스트처럼 `reset_db` 픽스처 사용. 클러스터 DB는 쓰지 않는다):
- 빈 DB(`reset_db(seed=False)`): `seed_if_empty()`가 True, 공연 1개·좌석 100석. 바로 다시 부르면 False이고 공연·좌석 수 그대로.
- 데이터가 있는 DB: 시드 뒤 좌석 하나를 홀드·예매(PENDING이든 CONFIRMED든)해 둔 상태에서 `seed_if_empty()`가 False이고,
  공연 ID·좌석 ID·좌석 상태·예매 행·`idempotency_keys`가 그대로다.
- CLI 분기: `python -m backend.app.seed --if-empty`를 같은 `DATABASE_URL`로 하위 프로세스로 돌리면 위와 같이 데이터를 지우지 않는다
  (분기가 `reset_db`로 잘못 연결되면 실패해야 한다).
- `wait_for_schema(timeout=5)`가 head까지 마이그레이션된 DB에서 바로 True. `--wait-schema` CLI가 종료 코드 0.

**스모크**(클러스터가 있을 때만):
- `kubectl`로 클러스터에 닿지 않거나 네임스페이스 `ticketing`이 없으면 **skip**(이유 출력). 네임스페이스가 있는데
  `deploy/app`이 180초 안에 준비되지 않으면 **실패**.
- `kubectl -n ticketing port-forward svc/app <빈 포트>:80`으로 Service 주소에 붙는다(테스트가 띄우고 끝나면 정리).
- 흐름: `/health` 200 → `/concerts` → 대기열 입장권 → 좌석맵에서 AVAILABLE 좌석 하나 → 홀드 → `POST /bookings`(Idempotency-Key)
  → `POST /payments/callback` SUCCEEDED → `GET /bookings/{id}`가 CONFIRMED. 응답 `X-App-Id`가 실행 중인 app Pod 이름 중 하나.
- metrics-server: 180초 안에 `kubectl -n ticketing get hpa app`의 현재 CPU 값이 채워진다(`<unknown>`이 아님).
- **DB를 지우지 않는다**(`reset_db` 금지, 사람의 Locust 데이터가 있을 수 있다). 매번 새 사용자 ID를 쓰고 남은 AVAILABLE 좌석을 고른다.
  AVAILABLE 좌석이 없으면 이유를 적고 실패한다.
- 로컬 `localhost:5432` DB와 compose 스택은 건드리지 않는다. 그래서 다른 자리의 pytest와 겹쳐도 된다(정적 검사도 마찬가지).
