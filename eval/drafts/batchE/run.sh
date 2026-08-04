#!/usr/bin/env bash
# eval/drafts/batchE/run.sh
unset NO_COLOR
unset CLAUDE_CODE_CHILD_SESSION
export TERM=xterm-256color
# Git Bash rewrites a leading-slash argument into a Windows path before it
# reaches a native exe. Without this the skill argument arrives mangled and
# the tab dies while `wt` still returns exit 0.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
cd /c/horizon-scout
claude --model claude-opus-5 --effort medium "/question-orchestrator eval/drafts/batchE/packet.json"
