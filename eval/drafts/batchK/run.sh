#!/usr/bin/env bash
# eval/drafts/batchK/run.sh - 3 adversarial slots, one per costume route.
unset NO_COLOR                      # inherited from the PowerShell tool env; forces b/w
unset CLAUDE_CODE_CHILD_SESSION     # else the session is a nested child: transcript
                                    # never saved, --resume and agent-trace broken
export TERM=xterm-256color
# claude.exe is a native Windows exe, so MSYS rewrites any argument that looks
# like a POSIX path - it turned the leading "/" of the slash command into
# "C:/Program Files/Git/question-orchestrator" and the session started on a
# prompt that named a file which does not exist. Excluding all args stops it.
export MSYS2_ARG_CONV_EXCL='*'
export MSYS_NO_PATHCONV=1
cd /c/horizon-scout
claude --model claude-opus-5 --effort medium "/question-orchestrator eval/drafts/batchK/packet.json"
