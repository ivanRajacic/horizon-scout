# Plan 06 - `/draft-questions`, the skill that sets up and launches the drafting tabs

**Kind:** prompt assets (`.claude/skills/`), plus small additions to `src/eval/batch.py`
and `src/cli.py`.
**Status: APPROVED 2026-07-28 by the user. Not started.**
**Depends on:** nothing. Plans 01-04 are already in; this sits on top of them and changes
none of their code.

## Why

On 2026-07-28 six questions were drafted by hand-launching two `/draft-batch` orchestrators
in terminal tabs and then a third. Everything about the drafting worked. Everything about
getting it started did not, and it failed the same way three times.

Two things came out of that day.

**One session cannot hold more than about three questions.** Measured from the session
transcripts in `~/.claude/projects/C--horizon-scout/`: the heaviest sessions peaked at
**275,451 and 334,730 tokens of context**, and each accumulated **18-25 million tokens of
cumulative input** because every turn re-reads everything the session is holding. The cost
of a session is not the number of questions in it, it is questions times turns. Three per
orchestrator is the cap this plan builds around.

**The setup work is what fills the context, and it gets paid for once per orchestrator.**
Reading the corpus profile, running the gap report, choosing candidate topics, assigning ids
and probing the servers are identical for every batch, but today each orchestrator does all
of it, and then carries it for the rest of its run. Three orchestrators pay three times.

So: do the shared work once, hand each orchestrator a finished packet, and let it go
straight to drafting.

A third thing from the same day is smaller but caused most of the lost time. The launch
mechanics are folklore. They live in `~/.claude/CLAUDE.md` and in whoever ran it last. They
belong in the skill.

## What we are building

Two skills, one renamed and one new.

| now | after | what it is |
|---|---|---|
| `/draft-batch` | `/question-orchestrator` | runs in one tab, drafts up to three questions, unchanged inside |
| - | `/draft-questions` | you type this; it plans the whole job and launches the tabs |

The existing agents (`question-drafter`, `question-reviewer`, `question-judge`) do not
change at all, and neither does anything downstream of them - the journal, the CLI nodes,
`write-batch`, `promote-drafts`.

## The workflow we want

1. Open a tab on any model, type `/draft-questions`, and say what you want: *"nine
   questions, all vector."* That is the whole instruction. You do not name cells, levels,
   subtypes, term styles, topics or ids.
2. It works out the cells from the gap report, finds topics, assigns ids, checks the
   servers, and **stops**.
3. It shows you a plain list - these nine slots, these topics, these ids - and waits. Not
   plan mode, just a list and a question.
4. You approve.
5. It splits the nine into groups of three, launches one terminal tab per group, and hands
   each a finished packet.
6. You watch the three tabs and steer any of them if you want to. That is why they are
   tabs and not background agents: the visibility is the point.
7. When they finish, the big orchestrator collects the three reports, checks them against
   each other, and tells you what you got. **You** tick the boxes and promote.

---

## Item 1 - rename `/draft-batch` to `/question-orchestrator`, and add packet mode

**Files:** `.claude/skills/draft-batch/` → `.claude/skills/question-orchestrator/`, plus a
prose sweep.

The rename is the easy half: "batch" never told anyone what the skill does, and the new name
puts it in the family it belongs to - `question-drafter` authors, `question-reviewer`
attacks, `question-judge` rules, `question-orchestrator` runs the three of them.

`draft-batch` appears in **27 files** outside `eval/drafts/`. All of them are prose
references. **Do not rename the CLI commands** - `gap-report`, `next-ids`,
`journal-append`, `batch-crosscheck`, `write-batch` are accurate about what they do, and
renaming them churns `src/eval/batch.py`, `src/cli.py`, `src/eval/trace.py`,
`src/eval/promote.py` and three test files for no gain. Journal and output filenames stay
as they are too; `promote-drafts` parses them.

The real work is packet mode. Today the skill always does its own setup and always
negotiates cells with a user, which is wrong when it is running in a tab nobody is talking
to. Add a second way in: **given a packet, skip steps 1 to 4 entirely** (gap report, cell
negotiation, candidate picking, id assignment, health probe) and go straight to dispatching
drafters.

A packet is one file the big orchestrator writes, holding:

- the three slots: route, level, subtype, term_style, and the pre-assigned question id
- three candidate topics per slot, in the order to try them, each with its corpus-map notes
  already pulled (`good for:` / `thin for:` / `texture:`)
- the output directory for this group
- the asset versions and index fingerprint for the batch header
- one line saying what the other groups are working on, so a drafter is not sent at a topic
  a sibling tab already has

Everything after that is the skill exactly as it stands: three candidates per slot, one fix
round each, the drafter/critic/judge split authority, the journal, `write-batch` at the end.

Keep the old path working. A `/question-orchestrator` with no packet still does its own
setup and still talks to a user - that is the right thing when you want one batch by hand.

Two things the orchestrator must still do for itself, because they are about its own three
questions and cannot be done in advance: choosing which of its three candidates to try when
one is abandoned, and relaying the lesson from an abandoned candidate to the next drafter.

---

## Item 2 - the new `/draft-questions` skill

**Files:** `.claude/skills/draft-questions/SKILL.md`, and whatever small CLI support it
needs (see below).

In order, what it does:

**Work out the cells.** You said "nine, vector". It runs `gap-report`, which reads the
allocation table live from `horizon-scout.md`, and picks nine cells that move the bank
toward its targets: which levels are shortest, which subtypes are thin, and the term_style
balance, which the gap report already reports per route. Legal subtypes per level are in
`src/eval/bank.py` (`VECTOR_SUBTYPE_LEVELS`, `HYBRID_SUBTYPE_LEVELS`, `SQL_SUBTYPE_LEVELS`)
and are not negotiable - vector L3 is survey and nothing else. It never exceeds a cell's
target without you saying so in words.

**Warn about unpromoted drafts.** Before planning, look inside the `eval/drafts/`
subfolders. If staged drafts are sitting there that were never promoted, say so and carry
on - they are not counted (the gap report counts the bank); the warning exists so old
drafts do not sit there forgotten.

**Find topics, and check there are enough.** It reads the corpus profile once and pulls
three candidates per slot - twenty-seven for a nine-question order - preferring frontier
buckets marked mapped but not yet mined, skipping anything whose topic or named entities are
already used by a bank question or by another slot in this run. **All twenty-seven
candidates must be distinct** - backups included, not just the nine first choices - because
the collision case is exactly two tabs each abandoning a primary and reaching for a backup. **If there are not enough
distinct topics, stop and say so.** Say how many were found and which buckets are exhausted.
Do not draft fewer than asked without saying so, and do not reuse a topic to fill the count.
(Later this is where it will offer to run `/explore-corpus` first. Not in this plan.)

**Assign the ids.** `next-ids` counts the bank and every staged draft file, but it globs
`eval/drafts/draft-bank-*.jsonl` and does not recurse, so it cannot see a batch that has not
staged its file yet. That is exactly why ids must be handed out centrally, once, here.

**Probe the servers - and start them if they are down.** If the embedder (:8080) or
reranker (:8082) is not running, the launcher starts it itself with the exact pinned
commands from `src/config.py` (decided 2026-07-28: the user should never have to be told
"turn on the server"). Then one `search_corpus("probe", k=1, snippet_chars=0)`. Expect the
first call after a server start to take minutes while models load; that is not an outage,
and the probe should be given a long window rather than killed and retried. Only a server
that will not come up even after being started stops the run.

**Show the plan and wait.** A plain list: each slot with its cell, its chosen topic and its
id, grouped by tab. Then ask. Nothing launches before you approve.

**Launch.** One tab per group of three, each with its own output directory
(`eval/drafts/<group>/`), because the journal and both output files are named by date and
two groups sharing a directory would collide.

**Then get out of the way.** The big orchestrator must not keep the packets in its context
after handing them over - that is the whole point. It should hold nine ids, three output
directories and nothing else while the tabs run.

---

## Item 3 - launch mechanics, written down so they stop being folklore

Every one of these cost real time on 2026-07-28. They go in the skill as instructions, not
in a person's head.

- **Point `wt` at a shell script, never at a command string containing double quotes.**
  PowerShell mangles embedded `"` when passing an argument to a native exe, so
  `bash -i -l -c 'claude --effort medium "$(cat prompt.txt)"'` reaches bash malformed and the
  tab dies instantly. `wt` still returns exit 0, so it looks like it worked. Write the
  prelude and the invocation into a small `.sh` per tab and launch
  `wt new-tab -d <repo> <bash> -i -l <path>/run.sh`.
- **Always pass `--model` and `--effort`.** Both are set at launch and a session cannot
  change its own. Omit `--model` and the tab silently inherits the launcher's model - on
  2026-07-28 two orchestrators meant for Opus 5 started as Fable 5. Use
  `--model claude-opus-5 --effort medium`. Medium is deliberate: the orchestrator routes and
  relays, and a high session effort amplifies re-reads of held evidence, which was the
  pilot's biggest cost leak.
- **`unset NO_COLOR`** (plus `export TERM=xterm-256color`) in the prelude, or the tab
  inherits `NO_COLOR=1` from the PowerShell tool environment and comes up black and white.
  Also `unset CLAUDE_CODE_CHILD_SESSION`, or the new sessions are treated as nested children
  and their transcripts are never saved, which breaks `--resume` and `agent-trace`.
- **Build the `wt` argument list as an array and splat it** (`wt @wtArgs`), with `';'` as
  its own element between tabs. `~/bin/gwt-fan.ps1` is the working reference for all of the
  above except the flags, which it does not pass.
- **Verify by process, not by exit code.** Check that each tab has a live `claude.exe` whose
  command line contains its output directory. The CLI runs as `claude.exe`, **not** as
  `node.exe` - looking for node finds nothing and reads as a failure when the launch was
  fine.
- **Notice a dead tab.** A tab whose process is gone but whose report never appeared has
  died. Say so; do not wait forever.

---

## Item 4 - after the tabs finish

**Check the groups against each other.** `batch-crosscheck` reads the *promoted* bank, so
two tabs running at the same time can converge on near-identical questions and nothing
notices. This is the one failure mode the existing checks cannot catch. Handing out topics
centrally mostly prevents it; a comparison across the three finished reports catches the
rest. Worth adding as a CLI node that takes several draft-bank files and flags shared gold
projects, shared named entities and high text overlap - `batch-crosscheck` already does this
within one batch, so it is an extension, not new logic.

**Report the holes.** A failed slot leaves a gap in the ids - `vec-16` is one from an
earlier run. Say which slots failed and why, and let the user decide whether to refill.

**Do not promote.** Collect the three reports, summarise what is in them, and stop. The
human gate is ticking `[x] APPROVE` and running `promote-drafts`, and it stays that way.

---

## Not in this plan

- **Auto-launching the explorer** when topics run short. Say it and stop; the user decides.
- **Background agents instead of tabs.** Nesting was tested on 2026-07-28 and works - a
  subagent can spawn subagents and has `SendMessage` - so an all-agent version with no
  terminals is possible. It was rejected on purpose: the user wants to watch the
  orchestrators work and steer them mid-run, and a background agent cannot be watched.
- **Renaming the CLI nodes or the journal/output filenames.**
- **Any change to the drafter, critic or judge.**

## How to check it worked

1. **Packet mode alone first.** Hand `/question-orchestrator` a packet by hand for one
   group of three and confirm it never calls `gap-report`, `get_corpus_profile` or
   `next-ids`, and that its first MCP call belongs to a drafter. That is the whole claim of
   item 1.
2. **Then one full nine-question run.** It should stop for approval before launching, launch
   three tabs that come up in color on Opus 5, and produce three staged pairs plus one
   combined summary.
3. **The number that says whether this was worth doing:** total tokens per accepted
   question, from `agent-trace`, against the 2026-07-25 baseline of ~490,000 in
   `optimization/README.md` and against 2026-07-28's three hand-launched batches. The saving
   should show up as setup work that happens once instead of three times, and as a big
   orchestrator whose own context stays small all run.
4. **The failure paths, deliberately:** kill a tab mid-run and confirm it is reported as
   dead rather than waited on; run it with the embedder stopped and confirm it starts the
   server itself and only stops if the server will not come up; ask for more questions than
   there are topics and confirm it stops and says so.
