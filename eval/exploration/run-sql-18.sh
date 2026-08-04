#!/usr/bin/env bash
# eval/exploration/run-sql-18.sh - /explore-corpus sql=18 distributions=6
unset NO_COLOR
unset CLAUDE_CODE_CHILD_SESSION
export TERM=xterm-256color
# Git Bash rewrites a leading-slash argument into a Windows path before it
# reaches a native exe, turning "/explore-corpus ..." into
# "C:/Program Files/Git/explore-corpus ...". Disable that conversion.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
cd /c/horizon-scout
claude --model claude-opus-5 --effort low "/explore-corpus sql=18 distributions=6"
