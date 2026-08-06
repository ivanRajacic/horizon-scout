#!/usr/bin/env bash
# eval/drafts/sonnet-probe/hyb/run.sh
# Sonnet probe, hyb x3. Every factory role runs as claude-sonnet-5, inherited
# from this launch flag - the agent files no longer pin a model.
# Effort stays medium, exactly as every Opus batch ran.
unset NO_COLOR
unset CLAUDE_CODE_CHILD_SESSION
export TERM=xterm-256color
export MSYS_NO_PATHCONV=1
cd /c/horizon-scout
claude --model claude-sonnet-5 --effort medium "/question-orchestrator eval/drafts/sonnet-probe/hyb/packet.json"
