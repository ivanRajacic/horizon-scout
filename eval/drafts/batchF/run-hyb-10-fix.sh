#!/usr/bin/env bash
# eval/drafts/batchF/run-hyb-10-fix.sh - redraft the rejected hyb-10
unset NO_COLOR
unset CLAUDE_CODE_CHILD_SESSION
export TERM=xterm-256color
# Git Bash rewrites a leading-slash argument into a Windows path before it
# reaches a native exe. Without this a skill argument arrives mangled and the
# tab dies while `wt` still returns exit 0.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
cd /c/horizon-scout
claude --model claude-fable-5 --effort medium "Read eval/drafts/batchF/hyb-10-fix-brief.md in full and follow it. It is a handoff brief: it explains why the hyb-10 draft was rejected at the human gate and what the fix has to satisfy. Do not start drafting until you have read it."
