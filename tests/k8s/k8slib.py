"""11장 k8s 확인 도구 (docs/k8s.md ④). deploy/k8s/*.yaml 을 kind+name 으로 찾는다 (파일 이름에 기대지 않는다)."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
K8S_DIR = ROOT / "deploy" / "k8s"


def load_docs():
    """deploy/k8s/*.yaml 의 모든 문서. 폴더·파일이 없으면 실패 ('미구현')."""
    files = sorted(list(K8S_DIR.glob("*.yaml")) + list(K8S_DIR.glob("*.yml"))) if K8S_DIR.is_dir() else []
    if not files:
        pytest.fail("deploy/k8s/*.yaml 이 없음 (11장 미구현)", pytrace=False)
    docs = []
    for f in files:
        for d in yaml.safe_load_all(f.read_text()):
            if isinstance(d, dict) and d.get("kind"):
                docs.append(d)
    return docs


def find(docs, kind, name):
    for d in docs:
        if d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name:
            return d
    pytest.fail(f"{kind}/{name} 이 deploy/k8s 에 없음", pytrace=False)


def pod_spec(doc):
    return doc["spec"]["template"]["spec"]


def containers(doc):
    ps = pod_spec(doc)
    return list(ps.get("containers") or []) + list(ps.get("initContainers") or [])


def main_container(doc, name=None):
    cs = pod_spec(doc).get("containers") or []
    assert cs, f"{doc['kind']}/{doc['metadata']['name']} 에 containers 가 없음"
    if name:
        for c in cs:
            if c.get("name") == name:
                return c
    return cs[0]


def _configmap_data(docs, name):
    return find(docs, "ConfigMap", name).get("data") or {}


def env_value(docs, container, key):
    """컨테이너 env 값을 문자열로. configMapKeyRef / envFrom ConfigMap 도 따라간다. 없으면 실패."""
    for e in container.get("env") or []:
        if e.get("name") != key:
            continue
        if "value" in e:
            return str(e["value"])
        ref = ((e.get("valueFrom") or {}).get("configMapKeyRef")) or {}
        if ref:
            data = _configmap_data(docs, ref["name"])
            if ref["key"] not in data:
                pytest.fail(f"ConfigMap/{ref['name']} 에 키 {ref['key']} 가 없음", pytrace=False)
            return str(data[ref["key"]])
        pytest.fail(f"{key} 를 값으로 읽을 수 없음 (value·configMapKeyRef 가 아님): {e}", pytrace=False)
    for ef in container.get("envFrom") or []:
        ref = ef.get("configMapRef")
        if ref:
            data = _configmap_data(docs, ref["name"])
            if key in data:
                return str(data[key])
    pytest.fail(f"컨테이너 {container.get('name')} 의 env 에 {key} 가 없음", pytrace=False)


def cmdline(container):
    parts = list(container.get("command") or []) + list(container.get("args") or [])
    return " ".join(str(p) for p in parts)


_CPU = re.compile(r"^(\d+(?:\.\d+)?)(m?)$")
_MEM = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "k": 10**3, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "": 1}


def cpu_millis(q):
    m = _CPU.match(str(q))
    assert m, f"CPU 수량을 읽을 수 없음: {q!r}"
    return float(m.group(1)) * (1 if m.group(2) else 1000)


def mem_bytes(q):
    m = re.match(r"^(\d+(?:\.\d+)?)([KMGT]i|[kKMGT]?)$", str(q))
    assert m, f"메모리 수량을 읽을 수 없음: {q!r}"
    return float(m.group(1)) * _MEM[m.group(2)]
