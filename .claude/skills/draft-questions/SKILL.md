---
name: draft-questions
description: Plan and launch a multi-tab drafting run for the Horizon Scout M5 bank. Takes a plain order ("nine questions, all vector"), works out the cells from the gap report, pulls distinct topics from the corpus profile, assigns all ids centrally, probes the servers, shows the full slot list and WAITS for approval - then launches one /question-orchestrator tab per group of three with a finished packet each. Does the shared setup once instead of once per tab. Never drafts, never promotes; the human gate on the drafts stays promote-drafts.
argument-hint: <order, e.g. "nine questions, all vector">
---

# /draft-questions

You are the planner and launcher for a drafting run. You do all the setup the orchestrator
tabs would otherwise each repeat - the gap report, topic finding, id assignment, the server
probe - once, hand each tab a finished packet, and then get out of the way. You draft
nothing yourself and you never touch `eval/bank.jsonl`.

**Arguments:** $ARGUMENTS - the order, in words. "Nine questions, all vector." That is the
whole instruction; the user does not name cells, levels, subtypes, term styles, topics or
ids. If the order is missing or ambiguous (no count, or a route that does not exist), ask
in plain text and wait.

**The cap is three questions per tab.** Measured 2026-07-28: a session holding more peaks
at 275k-335k tokens of context and 18-25M cumulative input tokens, because cost is
questions times turns. Never plan a group of more than three.

## Procedure - in this order, stopping where it says stop

### 1. Warn about unpromoted drafts

Glob `eval/drafts/**/draft-bank-*.jsonl`. If staged drafts exist that were never promoted
(their report still has unticked `Decision:` boxes, or the user tells you so), say which
ones and carry on - they are not counted anywhere, the warning exists so old drafts do not
sit there forgotten.

### 2. Work out the cells

```
./.venv/Scripts/python.exe -m src.cli gap-report
```

It reads the allocation table live from `horizon-scout.md`. From it, pick the cells that
move the bank toward its targets within the user's order: which levels are shortest, which
subtypes are thin, and the term_style balance (reported per route). Legal subtypes per
level are `VECTOR_SUBTYPE_LEVELS`, `HYBRID_SUBTYPE_LEVELS`, `SQL_SUBTYPE_LEVELS` in
`src/eval/bank.py` and are not negotiable - vector L3 is survey and nothing else. Never
exceed a cell's target without the user saying so in words. `ambiguous` and `compositional`
cells are interactive-only and are never planned here. **Adversarial cells (level ADV) ARE
planned here** - their subject comes from step 3b, not from the corpus profile.

### 3. Find topics, and check there are enough

Call `mcp__horizon-draft__get_corpus_profile` ONCE for `frontier` plus each needed route
section. An `{"error": ...}` result means the profile is unbuilt - stop and tell the user
to run `/explore-corpus` first.

Pull **three candidates per slot** - twenty-seven for a nine-question order - preferring
frontier buckets marked `mapped` but not yet `mined`, and skipping anything whose topic or
named entities are already used by a bank question (`get_bank_questions`) or by any other
candidate in this run. **All candidates must be pairwise distinct - backups included, not
just the first choices** - because the collision case is exactly two tabs each abandoning a
primary and reaching for a backup. For each candidate carrying a `bucket:` line, pull that
bucket's `## Corpus map` `good for:` / `thin for:` / `texture:` lines; they go in the
packet.

**If there are not enough distinct topics, STOP and say so.** Say how many were found and
which buckets are exhausted. Do not draft fewer than asked without saying so, and do not
reuse a topic to fill the count. (Running `/explore-corpus` to refill is the user's call,
not yours.)

### 3b. Find parents for the adversarial slots

An ADV question is a perturbation of a question already in the bank, so its subject is a
parent record, not a corpus-profile topic.

```
./.venv/Scripts/python.exe -m src.cli pick-parents --n <3 x ADV slots>
```

Deterministic: it drops questions already used as another ADV question's twin, and orders
the rest as a round robin over route, then subtype, then level. Hand each ADV slot three
parents in the order returned, and put each parent's **complete record** in the packet -
the drafter needs the gold to know what to negate. Parents are pairwise distinct across the
whole run for the same reason topics are: two tabs must not reach for the same one.

**If there are not enough untwinned parents, STOP and say so**, naming how many are left.
`unanswerable` slots take no parent and do not consume one.

### 4. Assign the ids - centrally, here, once

```
./.venv/Scripts/python.exe -m src.cli next-ids --sql N --vector N --hybrid N --adversarial N
```

`next-ids` counts the bank and the staged files it can see, but it does not recurse into
the group subdirectories - which is exactly why ids are handed out here, once, before any
tab exists, and why an orchestrator in packet mode never calls `next-ids` itself. Assign
one id per slot. Failed slots leave id gaps; that is harmless and normal.

### 5. Probe the servers - and start them yourself if they are down

First check what is listening: the embedder is :8080, the reranker :8082
(`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/health`, same for 8082).

**If either is not running, start it yourself - do not ask the user to.** The launch
commands are `SERVER_LAUNCH_CMD` (embedder) and `RERANK_SERVER_LAUNCH_CMD` (reranker) in
`src/config.py` - use them EXACTLY as written; the flags are load-bearing. Run each as a
background process so it outlives your tool call.

Then one `mcp__horizon-draft__search_corpus("probe", k=1, snippet_chars=0)`. The first
call after a server start takes minutes while the models load - that is a cold start, not
an outage; give it a long window rather than killing and retrying. Only if the probe
returns an error, or a server you started dies again, or it hangs long past ~5 minutes:
**say what you tried and what failed, and launch nothing.**

### 6. Show the plan and WAIT

A plain list, grouped by tab: each slot with its id, cell (route/level/subtype/term_style)
and chosen topic, plus the two backups. For an ADV slot the "topic" is its parent - show
`twin_id` and the parent's question text, so the user can see what each adversarial question
is a perturbation of before anything launches. Then ask, in plain text - never the
multiple-choice window. **Nothing launches before the user approves.**

### 7. Write the packets and launch the tabs

Split the slots into groups of at most three. Per group:

- Its own output directory `eval/drafts/<group>/` (e.g. `eval/drafts/batchD/` - continue
  the letter sequence). Two groups must never share a directory: the journal and both
  outputs are named by date and would collide.
- One packet file `eval/drafts/<group>/packet.json` in the format the `/question-orchestrator` skill
  defines under "Packet mode": `kind: "packet"`, `output_dir`, `order`, a one-line
  `siblings` saying what the other groups are working on, the resolved `versions` block
  (asset versions and hashes from `get_corpus_profile`/`get_schema_docs` metadata plus the
  index fingerprint - the same block a batch header carries), and the `slots` with their
  pre-assigned ids, cells, and three candidate blocks each with bucket-map lines. An ADV
  slot carries `parents` instead of `candidates`: three `{twin_id, record}` entries, each
  record complete.
- One launch script `eval/drafts/<group>/run.sh`.

**Launch mechanics - every one of these cost real time on 2026-07-28; follow them exactly:**

- **Point `wt` at the shell script, never at a command string containing double quotes.**
  PowerShell mangles embedded `"` when passing arguments to a native exe; the tab dies
  instantly while `wt` still returns exit 0. The script holds the prelude and the
  invocation:

  ```bash
  #!/usr/bin/env bash
  # eval/drafts/<group>/run.sh
  unset NO_COLOR                      # inherited from the PowerShell tool env; forces b/w
  unset CLAUDE_CODE_CHILD_SESSION     # else the session is a nested child: transcript
                                      # never saved, --resume and agent-trace broken
  export TERM=xterm-256color
  export MSYS2_ARG_CONV_EXCL='*'      # see below - without it the slash command is
  export MSYS_NO_PATHCONV=1           # rewritten into a Windows path and the run dies
  cd /c/horizon-scout
  claude --model claude-opus-5 --effort medium "/question-orchestrator eval/drafts/<group>/packet.json"
  ```

- **Export `MSYS2_ARG_CONV_EXCL='*'` before the `claude` line.** `claude.exe` is a native
  Windows binary, so MSYS rewrites any argument that looks like a POSIX path - and a slash
  command starts with `/`. Measured 2026-08-04: the prompt reached the tab as
  `"C:/Program Files/Git/question-orchestrator eval/drafts/batchK/packet.json"`, so the
  session started on a prompt naming a file that does not exist. The tab is alive and the
  model is right, which is exactly why the process check below must read the WHOLE command
  line and not just confirm something is running.
- **Always pass `--model` and `--effort`; both are launch-time only.** Omit `--model` and
  the tab silently inherits the launcher's model (2026-07-28: two orchestrators meant for
  Opus 5 started as Fable 5). Medium effort is deliberate: the orchestrator routes and
  relays, and high effort amplifies re-reads of held evidence - the pilot's biggest cost
  leak.
- **Build the `wt` argument list as an array and splat it** (`wt @wtArgs` from PowerShell,
  with `';'` as its own element between tabs), or from Git Bash launch one
  `wt new-tab -d C:/horizon-scout <path-to-bash> -i -l <group>/run.sh` per group.
  `~/bin/gwt-fan.ps1` is the working reference for the array/splat shape (it does not pass
  the flags; do not copy that part).
- **Verify by process, not by exit code, and read the whole command line.** Each tab must
  have a live `claude.exe` - NOT `node.exe` - whose command line contains its group's
  packet path AND an intact leading `/` on the slash command:

  ```powershell
  Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'claude.exe' } |
    Select-Object ProcessId, CommandLine
  ```

  Report the launch: how many tabs, which model (state it explicitly, so a wrong one is
  caught in seconds), which packet each got.

### 8. Then get out of the way

After the handoff, hold the ids, the group directories and nothing else. Do not keep the
packets, the candidate blocks or the corpus-profile text in play - the whole point of this
skill is that the tabs carry that, not you. Do not poll the tabs; the user watches them
and steers them, and the user will tell you when they are done.

### 9. After the tabs finish - only when the user says so

- Read each group's `draft-report-<date>.md` and summarise what came back: accepted /
  failed / blocked per group, with the judge's reasons for failures.
- **Report the holes.** A failed slot leaves an id gap; say which slots failed and why,
  and let the user decide whether to refill.
- **Notice a dead tab.** A group whose `claude.exe` is gone but whose report never
  appeared has died - say so, do not wait on it.
- **Do not promote.** The human gate is the user ticking `[x] APPROVE` and running
  `./.venv/Scripts/python.exe -m src.cli promote-drafts <report>`, per group, and it stays
  that way. Print the commands; run nothing.

## Standing rules

- You never draft, attack, judge, or edit a record. Your MCP use is setup only: the corpus
  profile, the bank questions (for topic dedup), and the health probe.
- You write exactly two kinds of file: packets and launch scripts, both under
  `eval/drafts/<group>/`. Never `eval/bank.jsonl`, never skills, never agents, never the
  corpus profile.
- Every stop in the procedure is a real stop: not enough topics, servers that will not
  come up even after you start them, and the approval gate all end the turn with a
  plain-text message to the user.
- The order is the user's. Never plan more questions than asked, and never quietly plan
  fewer.
