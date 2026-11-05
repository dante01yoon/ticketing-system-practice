#!/usr/bin/env python3
"""스토리별 테스트 통과표를 출력한다. 사용: uv run python scripts/story_report.py [pytest 인자...]

테스트의 @pytest.mark.story("US-03", "AC-2") 표시를 읽어 스토리별로 모은다.
기본 대상은 tests/contract. 실패가 하나라도 있으면 종료 코드 1.
"""
import os
import sys
from collections import defaultdict

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Collector:
    def __init__(self):
        self.marks = {}
        self.results = {}

    def pytest_collection_modifyitems(self, items):
        for it in items:
            m = it.get_closest_marker("story")
            self.marks[it.nodeid] = tuple(m.args) if m else ("(표시 없음)", "-")

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
            self.results[report.nodeid] = report.outcome


def main(argv):
    os.chdir(ROOT)
    col = Collector()
    pytest.main(["-q", "-p", "no:cacheprovider", *(argv or ["tests/contract"])], plugins=[col])
    stories = defaultdict(list)
    for nodeid, (us, ac, *_) in ((n, m + ("-",)) for n, m in col.marks.items()):
        stories[us].append((ac, col.results.get(nodeid, "skipped")))
    print("\n스토리별 통과표")
    print(f"{'스토리':<8}{'통과':>5}{'전체':>5}  상태   실패/미통과 AC")
    failed = False
    for us in sorted(stories):
        rows = stories[us]
        ok = sum(1 for _, o in rows if o == "passed")
        bad = [ac for ac, o in sorted(rows) if o != "passed"]
        failed |= bool(bad)
        print(f"{us:<8}{ok:>5}{len(rows):>5}  {'PASS' if not bad else 'FAIL'}   {', '.join(bad) or '-'}")
    total = sum(len(r) for r in stories.values())
    passed = sum(1 for r in stories.values() for _, o in r if o == "passed")
    print(f"합계: {passed}/{total} 통과")
    return 1 if failed or total == 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
