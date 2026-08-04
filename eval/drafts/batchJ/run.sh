#!/usr/bin/env bash
# eval/drafts/batchJ/run.sh
unset NO_COLOR
unset CLAUDE_CODE_CHILD_SESSION
export TERM=xterm-256color
export MSYS_NO_PATHCONV=1
cd /c/horizon-scout
claude --model claude-opus-5 --effort medium "/question-orchestrator eval/drafts/batchJ/packet.json"
