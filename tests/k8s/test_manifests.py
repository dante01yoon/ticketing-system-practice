"""11장 정적 검사 (docs/k8s.md ④ 1~7). 클러스터·DB 없이 deploy/k8s/*.yaml 만 읽는다."""
import re

import pytest
import yaml

import k8slib
from k8slib import K8S_DIR, cmdline, containers, cpu_millis, env_value, find, main_container, mem_bytes, pod_spec


@pytest.fixture(scope="module")
def docs():
    return k8slib.load_docs()


# ---------- 1. kustomization + 리소스 ----------
@pytest.mark.story("US-07", "k8s 1")
def test_kustomization_lists_existing_files_and_sets_namespace():
    kfile = K8S_DIR / "kustomization.yaml"
    assert kfile.is_file(), "deploy/k8s/kustomization.yaml 이 없음 (11장 미구현)"
    k = yaml.safe_load(kfile.read_text())
    assert k.get("namespace") == "ticketing", k.get("namespace")
    res = k.get("resources") or []
    assert res, "kustomization 에 resources 가 없음"
    for r in res:
        assert (K8S_DIR / r).is_file(), f"kustomization 이 나열한 {r} 파일이 없음"


@pytest.mark.story("US-07", "k8s 1")
@pytest.mark.parametrize(
    "kind,name",
    [
        ("Namespace", "ticketing"),
        ("Secret", "ticketing-secrets"),
        ("ConfigMap", "app-config"),
        ("StatefulSet", "postgres"),
        ("Service", "postgres"),
        ("StatefulSet", "redis"),
        ("Service", "redis"),
        ("Job", "db-init"),
        ("Deployment", "app"),
        ("Service", "app"),
        ("HorizontalPodAutoscaler", "app"),
    ],
)
def test_required_resources_exist(docs, kind, name):
    find(docs, kind, name)


@pytest.mark.story("US-07", "k8s 1")
def test_all_listed_resource_files_are_in_kustomization(docs):
    """deploy/k8s 의 매니페스트 파일이 kustomization 에서 빠지지 않았다 (apply -k 한 번으로 전부)."""
    k = yaml.safe_load((K8S_DIR / "kustomization.yaml").read_text())
    listed = {str(r) for r in k.get("resources") or []}
    for f in K8S_DIR.glob("*.y*ml"):
        if f.name == "kustomization.yaml":
            continue
        assert f.name in listed, f"{f.name} 이 kustomization resources 에 없음"


@pytest.mark.story("US-07", "k8s 1")
def test_postgres_and_redis_images_and_replicas(docs):
    pg = find(docs, "StatefulSet", "postgres")
    rd = find(docs, "StatefulSet", "redis")
    assert main_container(pg)["image"] == "postgres:16"
    assert main_container(rd)["image"] == "redis:7.4-alpine"
    assert pg["spec"]["replicas"] == 1 and rd["spec"]["replicas"] == 1


@pytest.mark.story("US-07", "k8s 1")
def test_secret_and_configmap_roles(docs):
    sec = find(docs, "Secret", "ticketing-secrets")
    sdata = {**(sec.get("stringData") or {}), **(sec.get("data") or {})}
    assert "ENTRY_TOKEN_SECRET" in sdata, f"Secret 에 ENTRY_TOKEN_SECRET 이 없음: {sorted(sdata)}"
    cm = find(docs, "ConfigMap", "app-config").get("data") or {}
    assert "ENTRY_TOKEN_SECRET" not in cm, "Secret 값을 ConfigMap 에 넣지 않는다"
    assert not any("PASSWORD" in k.upper() for k in cm), f"비밀번호를 ConfigMap 에 넣지 않는다: {sorted(cm)}"


@pytest.mark.story("US-07", "k8s 1")
def test_db_init_runs_seed_if_empty_and_is_retryable(docs):
    job = find(docs, "Job", "db-init")
    c = main_container(job)
    assert "backend.app.seed" in cmdline(c) and "--if-empty" in cmdline(c), cmdline(c)
    assert c["image"].startswith("ticketing-app:") and not c["image"].endswith(":latest")
    assert job["spec"].get("backoffLimit", 6) >= 1
    assert pod_spec(job).get("restartPolicy") in ("OnFailure", "Never")


# ---------- 2. Deployment/app ----------
@pytest.mark.story("US-07", "k8s 2")
def test_deployment_app_rollout_and_image(docs):
    d = find(docs, "Deployment", "app")
    assert d["spec"]["replicas"] == 1
    st = d["spec"]["strategy"]
    assert st["type"] == "RollingUpdate"
    ru = st["rollingUpdate"]
    assert ru["maxSurge"] == 1 and isinstance(ru["maxSurge"], int), ru
    assert ru["maxUnavailable"] == 0 and isinstance(ru["maxUnavailable"], int), ru
    c = main_container(d, "app")
    image = c["image"]
    assert ":" in image.rsplit("/", 1)[-1], f"이미지에 태그가 있어야 함: {image}"
    assert not image.endswith(":latest"), image
    assert c.get("imagePullPolicy") == "Never", c.get("imagePullPolicy")


@pytest.mark.story("US-07", "k8s 2")
def test_deployment_app_command_has_single_worker(docs):
    c = main_container(find(docs, "Deployment", "app"), "app")
    cmd = cmdline(c)
    assert "uvicorn" in cmd and "backend.app.main:app" in cmd, cmd
    assert re.search(r"--workers[ =]1\b", cmd), f"command 에 --workers 1 이 없음: {cmd}"
    assert "--port 8000" in cmd or "--port=8000" in cmd, cmd


@pytest.mark.story("US-07", "k8s 2")
@pytest.mark.parametrize("probe", ["readinessProbe", "livenessProbe"])
def test_deployment_app_probes_hit_health(docs, probe):
    c = main_container(find(docs, "Deployment", "app"), "app")
    p = c.get(probe)
    assert p, f"{probe} 가 없음"
    hg = p.get("httpGet")
    assert hg and hg["path"] == "/health" and int(hg["port"]) == 8000, p


@pytest.mark.story("US-07", "k8s 2")
def test_liveness_is_more_lenient_than_readiness(docs):
    c = main_container(find(docs, "Deployment", "app"), "app")
    r, l = c["readinessProbe"], c["livenessProbe"]
    r_start = r.get("initialDelaySeconds", 0) + r.get("periodSeconds", 10) * r.get("failureThreshold", 3)
    l_start = l.get("initialDelaySeconds", 0) + l.get("periodSeconds", 10) * l.get("failureThreshold", 3)
    assert l_start > r_start, f"liveness({l_start}s)는 readiness({r_start}s)보다 느슨해야 함"


@pytest.mark.story("US-07", "k8s 2")
def test_deployment_app_env(docs):
    c = main_container(find(docs, "Deployment", "app"), "app")
    app_id = next((e for e in c.get("env") or [] if e.get("name") == "APP_ID"), None)
    assert app_id, "APP_ID env 가 없음"
    assert app_id["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.name", app_id
    for key in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW"):
        assert int(env_value(docs, c, key)) > 0
    assert int(env_value(docs, c, "QUEUE_BATCH")) >= 1_000_000
    assert env_value(docs, c, "ENTRY_TTL_SECONDS") == "3600"
    assert "postgres" in env_value(docs, c, "DATABASE_URL")
    assert "redis" in env_value(docs, c, "REDIS_URL")
    # ENTRY_TOKEN_SECRET 은 Secret 에서
    e = next((e for e in c.get("env") or [] if e.get("name") == "ENTRY_TOKEN_SECRET"), None)
    assert e and "secretKeyRef" in (e.get("valueFrom") or {}), f"ENTRY_TOKEN_SECRET 은 secretKeyRef 여야 함: {e}"
    assert e["valueFrom"]["secretKeyRef"]["name"] == "ticketing-secrets"


@pytest.mark.story("US-07", "k8s 8")
def test_app_has_wait_for_db_init_container(docs):
    dep = find(docs, "Deployment", "app")
    inits = pod_spec(dep).get("initContainers") or []
    ic = next((c for c in inits if c.get("name") == "wait-for-db"), None)
    assert ic, f"Deployment/app 에 initContainer wait-for-db 가 없음 (있는 것: {[c.get('name') for c in inits]})"
    assert ic["image"] == main_container(dep, "app")["image"], (ic["image"], main_container(dep, "app")["image"])
    cmd = cmdline(ic)
    assert "backend.app.seed" in cmd and "--wait-schema" in cmd, cmd
    res = ic.get("resources") or {}
    for section in ("requests", "limits"):
        for r in ("cpu", "memory"):
            assert (res.get(section) or {}).get(r), f"wait-for-db: resources.{section}.{r} 없음"
    assert cpu_millis(res["limits"]["cpu"]) >= cpu_millis(res["requests"]["cpu"])
    assert mem_bytes(res["limits"]["memory"]) >= mem_bytes(res["requests"]["memory"])


# ---------- 3. 자원 ----------
@pytest.mark.story("US-07", "k8s 3")
@pytest.mark.parametrize("kind,name", [("Deployment", "app"), ("StatefulSet", "postgres"), ("StatefulSet", "redis"), ("Job", "db-init")])
def test_every_container_has_requests_and_limits(docs, kind, name):
    cs = containers(find(docs, kind, name))
    assert cs
    for c in cs:
        res = c.get("resources") or {}
        for section in ("requests", "limits"):
            for r in ("cpu", "memory"):
                assert (res.get(section) or {}).get(r), f"{kind}/{name} 컨테이너 {c.get('name')}: resources.{section}.{r} 없음"
        assert cpu_millis(res["limits"]["cpu"]) >= cpu_millis(res["requests"]["cpu"]), f"{kind}/{name} {c.get('name')}: cpu limits < requests"
        assert mem_bytes(res["limits"]["memory"]) >= mem_bytes(res["requests"]["memory"]), f"{kind}/{name} {c.get('name')}: memory limits < requests"


# ---------- 4. Service ----------
@pytest.mark.story("US-07", "k8s 4")
def test_service_app_routes_80_to_8000_and_selects_pods(docs):
    svc = find(docs, "Service", "app")
    assert svc["spec"].get("type", "ClusterIP") == "ClusterIP"
    ports = svc["spec"]["ports"]
    assert any(int(p["port"]) == 80 and int(p["targetPort"]) == 8000 for p in ports), ports
    labels = find(docs, "Deployment", "app")["spec"]["template"]["metadata"]["labels"]
    sel = svc["spec"]["selector"]
    assert sel and sel.items() <= labels.items(), f"selector {sel} 가 Pod 라벨 {labels} 의 부분집합이 아님"
    dsel = find(docs, "Deployment", "app")["spec"]["selector"]["matchLabels"]
    assert dsel.items() <= labels.items(), "Deployment selector 가 Pod 라벨과 맞지 않음"


@pytest.mark.story("US-07", "k8s 4")
@pytest.mark.parametrize("name,port,sts", [("postgres", 5432, "postgres"), ("redis", 6379, "redis")])
def test_data_services_ports_and_selectors(docs, name, port, sts):
    svc = find(docs, "Service", name)
    assert any(int(p["port"]) == port for p in svc["spec"]["ports"]), svc["spec"]["ports"]
    labels = find(docs, "StatefulSet", sts)["spec"]["template"]["metadata"]["labels"]
    sel = svc["spec"]["selector"]
    assert sel and sel.items() <= labels.items(), f"Service/{name} selector {sel} 가 Pod 라벨 {labels} 와 맞지 않음"


# ---------- 5. 상태 저장소 ----------
@pytest.mark.story("US-07", "k8s 5")
@pytest.mark.parametrize("name", ["postgres", "redis"])
def test_statefulset_has_mounted_pvc(docs, name):
    sts = find(docs, "StatefulSet", name)
    vcts = sts["spec"].get("volumeClaimTemplates") or []
    assert vcts, f"StatefulSet/{name} 에 volumeClaimTemplates 가 없음"
    claim_names = {v["metadata"]["name"] for v in vcts}
    mounted = {m["name"] for c in main_container(sts) and [main_container(sts)] for m in c.get("volumeMounts") or []}
    assert claim_names & mounted, f"PVC {claim_names} 를 컨테이너에 마운트하지 않음 (마운트: {mounted})"
    for v in vcts:
        assert "ReadWriteOnce" in v["spec"]["accessModes"]
        size = v["spec"]["resources"]["requests"]["storage"]
        assert mem_bytes(size) >= 2**30, f"PVC 크기 {size} < 1Gi"


@pytest.mark.story("US-07", "k8s 5")
def test_postgres_args_and_pgdata_and_probe(docs):
    c = main_container(find(docs, "StatefulSet", "postgres"))
    assert re.search(r"max_connections=\d+", cmdline(c)), f"postgres args 에 max_connections 가 없음: {cmdline(c)}"
    pgdata = env_value(docs, c, "PGDATA")
    mounts = [m["mountPath"] for m in c.get("volumeMounts") or []]
    assert any(pgdata.startswith(m.rstrip("/") + "/") and pgdata != m for m in mounts), f"PGDATA {pgdata} 는 마운트 {mounts} 아래 하위 폴더여야 함"
    probe = c.get("readinessProbe") or {}
    assert "pg_isready" in " ".join((probe.get("exec") or {}).get("command") or []), probe


@pytest.mark.story("US-07", "k8s 5")
def test_redis_uses_appendonly_and_data_mount(docs):
    c = main_container(find(docs, "StatefulSet", "redis"))
    assert re.search(r"--appendonly[ =]yes|appendonly\s+yes", cmdline(c)), cmdline(c)
    assert "/data" in [m["mountPath"] for m in c.get("volumeMounts") or []]


# ---------- 6. HPA ----------
@pytest.mark.story("US-07", "k8s 6")
def test_hpa_targets_app_cpu_70(docs):
    h = find(docs, "HorizontalPodAutoscaler", "app")
    assert h["apiVersion"] == "autoscaling/v2", h["apiVersion"]
    ref = h["spec"]["scaleTargetRef"]
    assert (ref["kind"], ref["name"]) == ("Deployment", "app"), ref
    assert h["spec"]["minReplicas"] == 1 and h["spec"]["maxReplicas"] == 3
    cpu = [m for m in h["spec"]["metrics"] if m.get("type") == "Resource" and m["resource"]["name"] == "cpu"]
    assert cpu, h["spec"]["metrics"]
    tgt = cpu[0]["resource"]["target"]
    assert tgt["type"] == "Utilization" and tgt["averageUtilization"] == 70, tgt
    win = h["spec"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"]
    assert 0 <= win <= 120, f"scaleDown 안정화 창 {win}초 > 120"


# ---------- 7. 연결 예산 ----------
@pytest.mark.story("US-07", "k8s 7")
def test_connection_budget_within_60_percent_of_max_connections(docs):
    hpa = find(docs, "HorizontalPodAutoscaler", "app")["spec"]["maxReplicas"]
    dep = find(docs, "Deployment", "app")
    surge = dep["spec"]["strategy"]["rollingUpdate"]["maxSurge"]
    assert isinstance(surge, int), f"maxSurge 는 정수여야 계산할 수 있음: {surge!r}"
    app = main_container(dep, "app")
    m = re.search(r"--workers[ =](\d+)", cmdline(app))
    assert m, f"command 에 --workers N 이 없음: {cmdline(app)}"
    workers = int(m.group(1))
    per_proc = int(env_value(docs, app, "DB_POOL_SIZE")) + int(env_value(docs, app, "DB_MAX_OVERFLOW"))
    pg = main_container(find(docs, "StatefulSet", "postgres"))
    mc = re.search(r"max_connections=(\d+)", cmdline(pg))
    assert mc, f"postgres args 에 max_connections 가 없음: {cmdline(pg)}"
    max_conn = int(mc.group(1))
    a = (hpa + surge) * workers * per_proc
    assert a <= 0.6 * max_conn, f"앱 최대 연결 A=({hpa}+{surge})×{workers}×{per_proc}={a} > 0.6×{max_conn}={0.6 * max_conn}"
