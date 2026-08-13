#!/usr/bin/env bash
# One-off rescore: re-judge round2-full's 33 ragas answers under FACTUAL_MODE="f1"
# (temporary edit in src/judge/ragas_judge.py) into the cloned run round2-full-f1.
cd /c/horizon-scout || exit 1
./.venv/Scripts/python.exe -m src.cli run-bank --run-id round2-full-f1 --resume
echo
echo "exit code: $?"
echo "done - press enter to close"
read -r
