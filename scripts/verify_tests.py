#!/usr/bin/env python3
"""보호할 테스트 파일(tests/, e2e/)의 SHA-256 해시를 기록하고 확인한다.

    python3 scripts/verify_tests.py record   # lead: RED 테스트를 받아들인 뒤 기준선 기록
    python3 scripts/verify_tests.py check    # 통합 전 최종 확인 (기본 동작)

check는 기준선과 다르면(수정·추가·삭제) 목록을 출력하고 종료 코드 1을 돌려준다.
훅(scripts/hooks/guard_server_tests.py)은 우회될 수 있으므로 최종 판단은 이 스크립트로 한다.
표준 라이브러리만 사용한다.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTECTED_DIRS = ("tests", "e2e")
MANIFEST = os.path.join(ROOT, "scripts", "test-hashes.json")
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules"}
SKIP_SUFFIXES = (".pyc", ".pyo")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def current_hashes():
    result = {}
    for top in PROTECTED_DIRS:
        base = os.path.join(ROOT, top)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                if name.endswith(SKIP_SUFFIXES):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                result[rel] = sha256(full)
    return result


def record():
    hashes = current_hashes()
    if not hashes:
        print("기록할 테스트 파일이 없습니다 (tests/, e2e/ 비어 있음).", file=sys.stderr)
        return 1
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump({"algorithm": "sha256", "files": hashes}, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"{len(hashes)}개 파일 해시를 {os.path.relpath(MANIFEST, ROOT)}에 기록했습니다.")
    return 0


def check():
    if not os.path.exists(MANIFEST):
        print(f"기준선이 없습니다: {os.path.relpath(MANIFEST, ROOT)} — 먼저 record를 실행하세요.",
              file=sys.stderr)
        return 1
    with open(MANIFEST, encoding="utf-8") as f:
        expected = json.load(f)["files"]
    actual = current_hashes()
    modified = sorted(p for p in expected if p in actual and actual[p] != expected[p])
    removed = sorted(p for p in expected if p not in actual)
    added = sorted(p for p in actual if p not in expected)
    for label, paths in (("수정됨", modified), ("삭제됨", removed), ("추가됨", added)):
        for p in paths:
            print(f"{label}: {p}")
    if modified or removed or added:
        print("FAIL: 보호된 테스트 파일이 기준선과 다릅니다.")
        return 1
    print(f"OK: 보호된 테스트 파일 {len(expected)}개가 기준선과 일치합니다.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="check", choices=["record", "check"])
    args = parser.parse_args()
    return record() if args.command == "record" else check()


if __name__ == "__main__":
    sys.exit(main())
