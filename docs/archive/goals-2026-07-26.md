# Goals

*Written 2026-07-26, distilled from the conversation that ended the formal-study framing. This is the why of the project; `working-plan.md` stays the how.*

## Why this project exists

Three reasons, in order:

1. **Learn by building.** My next job will very likely involve a combined SQL + vector database system. This project is my intro to that class of system - not a tutorial version, but the real thing: guardrailed text-to-SQL, four retrieval conditions, a router, end-to-end tracing over a real corpus (35k CORDIS projects, 190k vectors).
2. **Have a stamp that it actually works.** Not "the demo ran" but measured evidence: an execution-verified question bank, an LLM judge, and before/after numbers on named improvements. Being able to say exactly why things fail, not just that they pass.
3. **Portfolio.** A write-up someone can read and conclude I can be trusted with a production retrieval/eval pipeline.

## What this project is NOT anymore

The pre-registered study in `horizon-scout.md` (v4) is retired as a framing. The research questions, freeze points, and the ~97-question allocation table were well designed but front-load cost for a payoff that only lands at the very end - the wrong tool for one person learning. Everything the study framing produced (the bank, the judge, the runner, the tracing, the authoring pipelines) carries over unchanged; only the ceremony is dropped.

Consequences:

- The bank does not need to reach ~97 questions. Roughly 40-60 is plenty for a directional eval; fill glaring holes, nothing more.
- Freeze discipline is relaxed. Prompts and thresholds can move if the change is disclosed in the write-up.
- The judge is NOT getting fixed or recalibrated. The pilot showed the metric ranks answers correctly (factual score tracks gold coverage, rho = 0.718); the 0.75 pass threshold is just too strict for answers that are functionally correct but phrased unlike the reference. Resolution: graph continuous scores (means, distributions), not pass rates. The threshold stops mattering.

## The plan: three graphs

The whole remaining arc, in order:

1. **Baseline.** Run the bank end to end on the current system (`run-bank`). Analyze it, graph it. This is graph one and the load-bearing result.
2. **Improve the normal system, re-benchmark.** Take the cheap, concrete fixes the pilot already surfaced (`docs/pilot-router-findings.md`: scoped filter provenance, per-project chunk budgeting, SQL scorer projection to `answer_columns`, judge-call retry) plus whatever the baseline analysis turns up. Same bank, same judge, same axes. Graph two: before/after per named change.
3. **Agentic version, benchmark it.** See whether runtime orchestration buys anything over the static system. Graph three. This is the first thing to cut if the project needs to shrink - the baseline and improvement graphs are the story; this is a chapter.

Then a short write-up. Along the way, the retrieval comparison (is hybrid best, does reranking help) settles which stack the improved system uses - it is programmatic (recall/MRR), so it does not even need the judge.

## Rules that survive the reframing

- **Infrastructure is frozen.** The drafting/explorer pipelines, the runner, the judge plumbing - done. No fifth re-architecture. Remaining effort goes into running measurements and making plots, not improving machinery.
- **Directional claims only.** With this many questions, report "hybrid went from 1/6 to 4/6 and here is why", never small percentage differences. Per-cell differences under ~15 points are noise; that was true under the study framing and is still true.
- **Say how fixes were found.** With 21-ish questions, improvements designed by staring at specific failures are fine - the write-up just says so, so the graphs do not quietly become circular.
- **Deterministic-first, trace everything, execution-verified gold** - unchanged. These are the habits that make the results worth anything.

## What is worth showing at the end

Two things, possibly two separate write-ups:

1. **The system and its measured improvement arc.** The standard story: built it, benchmarked it, improved it, showed the numbers.
2. **The explorer + drafter pipeline.** The genuinely uncommon part: how to get an LLM to author an execution-verified benchmark without trusting it - split authority (drafter/critic/judge), deterministic gates, every claim re-executed. A better post than another RAG walkthrough, and it is already built.

## Perspective, for the days it feels like too much work

The system took a few days; the benchmark part has taken longer. That ratio is not a failure of the project - it is the industry-accurate ratio. In real LLM/RAG work the system is the fast part and evaluation is where the time goes. "The eval is the product" was line one of the original plan, and it survives the reframing. The remaining distance from here to all three graphs is small - a few days of running things that are already built - and it is what converts everything spent so far into the result the project exists for.
