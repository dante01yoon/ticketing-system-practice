# 1장 · 에이전트 팀 만들기와 테스트 지키기 — 사용한 프롬프트

강의 영상에서 실제로 입력한 프롬프트 원문이에요. 영상에서 프롬프트가 길면 여기서 그대로 복사해 쓰세요.
팀(lead · test · server · review)은 OpenRig로 띄우고, 프롬프트는 Herdr의 해당 칸에 입력했어요.

## 1. 터미널 (rig send)

```text
rig send operator-agent@kernel "$PWD 저장소에서 쓸 새 팀 tkt를 만들어 줘. 팀 파일은 rigs/tkt/rig.yaml에 둬. 자리는 넷: lead는 Claude Code opus(기획·일 나누기·통합), test는 Claude Code sonnet(테스트만 작성), server는 Claude Code sonnet(backend 폴더), review는 Codex(리뷰). 모든 자리는 permission_policy를 builtin:yolo로 해 줘. 팀 파일만 만들고 rig up은 하지 마."
```

## 2. lead 칸

```text
팀 작업 규칙을 만들어 줘. ① 루트 CLAUDE.md와 AGENTS.md에 역할별 폴더 소유(test=tests/·e2e/, server=backend/, lead=docs/·scripts/·루트 설정)와 「테스트 먼저(RED) → 구현(GREEN)」 순서를 짧게 적고 ② server 자리가 tests/나 e2e/ 파일을 Edit·Write로 고치려 하면 막는 Claude Code 훅을 만들고 ③ 보호할 테스트 파일의 해시를 기록·확인하는 scripts/verify_tests.py를 만들어 줘. 훅은 대표 실수를 막는 장치라 우회될 수 있으니 최종 확인은 verify_tests.py로 한다고 적어 줘. 다 만들면 훅이 적용되게 server 자리를 다시 시작해 줘.
```

## 3. test 칸

```text
tests/test_smoke.py에 1 더하기 1은 2라는 테스트 하나만 쓰고, 실행해서 통과하는지 보고, verify_tests.py로 해시를 기록해 줘.
```

## 4. server 칸

```text
훅이 제대로 막는지 확인하려고 해. tests/test_smoke.py 맨 아래에 주석 한 줄을 Edit 도구로 직접 넣어 봐. 막히면 어떤 메시지가 나왔는지 그대로 알려 줘.
```

## 사람이 셸에서 친 명령

```bash
ls -a
rig ps
rig capture operator-agent@kernel --lines 22
mkdir -p backend
rig up rigs/tkt/rig.yaml
rig terminal open tkt --provider herdr
셸
python3 scripts/verify_tests.py check
git add .
git commit -qm "1장 팀 규칙"
git log --oneline
echo '# 몰래 고침' >> tests/test_smoke.py
python3 scripts/verify_tests.py check
git checkout -- tests/test_smoke.py
python3 scripts/verify_tests.py check
```
