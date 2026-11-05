#!/usr/bin/env python3
"""Claude Code PreToolUse hook: server 자리가 테스트 파일을 고치지 못하게 막는다.

대표 실수(server가 Edit/Write로 tests/·e2e/를 고침)를 막는 장치일 뿐이다.
Bash 리다이렉션 등으로 우회할 수 있으므로 최종 확인은 scripts/verify_tests.py로 한다.
"""
import json
import os
import sys

# 이 자리에서만 동작한다. OpenRig가 각 자리 프로세스에 OPENRIG_SESSION_NAME을 넣어 준다.
GUARDED_SEAT_PREFIX = "dev-server@"
PROTECTED_DIRS = ("tests", "e2e")
PROTECTED_FILES = ("scripts/test-hashes.json",)


def main() -> int:
    if not os.environ.get("OPENRIG_SESSION_NAME", "").startswith(GUARDED_SEAT_PREFIX):
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        return 0

    root = os.path.realpath(
        os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    )
    target = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    rel = os.path.relpath(target, root)
    if rel.startswith(".."):
        return 0
    rel = rel.replace(os.sep, "/")

    parts = rel.split("/")
    if parts[0] in PROTECTED_DIRS or rel in PROTECTED_FILES:
        print(
            f"차단: server 자리는 {rel} 를 수정할 수 없습니다. tests/·e2e/는 test 자리 소유입니다. "
            "테스트가 틀렸다고 보면 lead에게 큐로 알리세요 (CLAUDE.md '팀 작업 규칙').",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
