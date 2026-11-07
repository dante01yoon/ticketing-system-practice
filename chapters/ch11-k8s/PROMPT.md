# 11장 쿠버네티스(minikube) 배포와 부하 테스트 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. lead 칸

```text
11장은 쿠버네티스에 올리는 거야. Mac 한 대에서 minikube(docker 드라이버)를 써. ① deploy/k8s에 매니페스트를 만들어. 앱은 Deployment(처음 1개), Service, readiness·liveness 프로브(/health), CPU·메모리 requests와 limits를 넣어. PostgreSQL과 Redis는 PVC를 쓰는 StatefulSet 하나씩이면 돼. ② metrics-server를 켜고, 앱에 HPA를 걸어. CPU 70%를 넘으면 1개에서 최대 3개까지 늘고, 부하가 끝나면 다시 줄어야 해. ③ 앱의 DB 커넥션 풀 크기를 정해서, Pod 3개 × 풀 크기가 PostgreSQL max_connections보다 넉넉히 작게 해. 계산은 docs/k8s.md에 적어 줘. ④ test에게 tests/k8s에 확인 테스트를 먼저 쓰게 해서 RED를 확인하고 해시를 기록해. 매니페스트에 프로브·requests가 있는지, 커넥션 예산이 맞는지 같은 정적 검사와, 클러스터가 떠 있을 때만 도는 스모크 테스트(좌석 조회·홀드·확정이 Service 주소로 되는지)야. ⑤ server가 구현해서 GREEN을 만든 다음, review에게 배포 설정 검토를 맡겨 줘. 부하 테스트는 내가 Locust로 직접 할 거라 하지 않아도 돼.
```

## 사람이 셸에서 친 명령

```bash
kubectl -n ticketing get pods,hpa
cat loadtest/locustfile.py
minikube service -n ticketing app --url > /private/tmp/tkt/url 2>&1 & TPID=$!; sleep 8; URL=$(grep -m1 -o 'http://[0-9.:]*' /private/tmp/tkt/url); echo $URL
kubectl -n ticketing patch hpa app --type merge -p '{"spec":{"minReplicas":1,"maxReplicas":1}}'; sleep 15; kubectl -n ticketing get pods -l app=app
uvx --from locust==2.32.4 locust -f loadtest/locustfile.py --headless -H $URL --only-summary -u 600 -r 200 -t 60s 2>&1 | grep -E 'Name|Aggregated'
kubectl -n ticketing patch hpa app --type merge -p '{"spec":{"minReplicas":1,"maxReplicas":3}}'; (uvx --from locust==2.32.4 locust -f loadtest/locustfile.py --headless -H $URL --only-summary -u 800 -r 100 -t 150s > /private/tmp/tkt/loc.txt 2>&1 &); for i in $(seq 10); do sleep 15; kubectl -n ticketing get hpa app --no-headers; done
sleep 15; grep Aggregated /private/tmp/tkt/loc.txt | head -1
kubectl -n ticketing patch hpa app --type merge -p '{"spec":{"minReplicas":3,"maxReplicas":3}}'; kubectl -n ticketing rollout status deploy/app; kubectl -n ticketing get pods -l app=app
uvx --from locust==2.32.4 locust -f loadtest/locustfile.py --headless -H $URL --only-summary -u 600 -r 200 -t 60s 2>&1 | grep -E 'Name|Aggregated'
kubectl -n ticketing patch hpa app --type merge -p '{"spec":{"minReplicas":1,"maxReplicas":3}}'; kill $TPID
uv run pytest tests/k8s -q 2>&1 | tail -1
python3 scripts/verify_tests.py check
git add loadtest && git commit -qm "11장 부하 테스트(Locust)" && git log --oneline | head -4
```
