"""Project CLI.

  python -m src.cli build-index [--limit N] [--chunk-target 400] [--split-overlap 50]
  python -m src.cli search "query" [-k 10] [--source report|objective]
                                   [--project-ids ID ...] [--dedup-projects]
  python -m src.cli smoke [--eval-file eval/smoke_vector.jsonl]
  python -m src.cli ask-sql "question" [--show-prompt]
  python -m src.cli smoke-sql [--eval-file eval/smoke_sql.jsonl]
  python -m src.cli ask "question" [--mode sql|vector|scoped] [-k 10]
                                   [--explain] [--quiet]
  python -m src.cli explore [--mode sql|vector|scoped] [-k 10]
  python -m src.cli smoke-router [--eval-file eval/smoke_router.jsonl]
  python -m src.cli validate-bank [--bank eval/bank.jsonl]
  python -m src.cli validate-record <record.json | ->
  python -m src.cli gap-report [--bank ...] [--drafts-dir ...] [--plan ...]
  python -m src.cli next-ids [--sql N] [--vector N] [--hybrid N]
  python -m src.cli batch-crosscheck <journal.jsonl | draft-bank.jsonl>
  python -m src.cli journal-append <journal.jsonl> --id hyb-08
                                   --status REVIEWING [--payload - | file]
  python -m src.cli write-batch <journal.jsonl> [--output-dir ...]
                                [--date YYYY-MM-DD] [--suffix -2] [--force]
  python -m src.cli promote-drafts <draft-report.md> [--bank eval/bank.jsonl]
  python -m src.cli archive-questions --ids vec-03 ... --reason "<why>"
                                      [--reasons per-id.json] [--dry-run]
                                      [--bank eval/bank.jsonl] [--archive PATH]
  python -m src.cli judge-file [--eval-file eval/judge_smoke.jsonl]
                               [--model haiku|sonnet]
  python -m src.cli run-bank [--conditions router force-sql force-vector
                                            always-hybrid]
                             [--bank eval/bank.jsonl] [-k 10] [--no-judge]
                             [--ids sql-01 ...] [--routes vector hybrid]
                             [--limit N] [--run-id NAME] [--resume]
  python -m src.cli run-retrieval [--conditions lexical dense hybrid
                                                hybrid_rerank]
                                  [--bank eval/bank.jsonl] [--depth 100]
                                  [--k-gen 10] [--no-judge] [--ids vec-01 ...]
                                  [--routes vector] [--limit N]
                                  [--run-id NAME] [--resume]
"""

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

from src.config import (CHUNK_TARGET, CLAUDE_CONCURRENCY, FUSE_CANDIDATES,
                        JUDGE_DEFAULT, ROOT, SPLIT_OVERLAP)

# Transcription-boundary guard: agent-returned packages have arrived with
# HTML entities (&lt; &gt; &amp;) where the source text had < > &. Unnoticed,
# corrupted text goes into the bank permanently - so the CLI commands that
# accept an agent's raw returned text refuse it loudly. This lives HERE, not
# in bank.py's schema validator: a CORDIS title legitimately carrying an
# entity-looking substring must never make the promoted bank unloadable; the
# hazard is transcription, and this is the transcription boundary.
_HTML_ENTITY_RE = re.compile(r"&(?:lt|gt|amp|quot|apos|nbsp|#\d+"
                             r"|#x[0-9a-fA-F]+);")


def html_entity_hits(raw: str, context: int = 40) -> list[str]:
    """Each entity found in `raw`, with surrounding context - "an entity is
    present" is useless without "where"."""
    hits = []
    for match in _HTML_ENTITY_RE.finditer(raw):
        start, end = match.span()
        snippet = raw[max(0, start - context):min(len(raw), end + context)]
        snippet = " ".join(snippet.split())
        hits.append(f"{match.group(0)} in: ...{snippet}...")
    return hits


def cmd_build_index(args):
    from src.ingest.embed_index import build_index

    build_index(limit=args.limit, chunk_target=args.chunk_target,
                split_overlap=args.split_overlap)


def cmd_search(args):
    from src.config import RUNTIME_RETRIEVER
    from src.retrieval.registry import build_retriever

    # The stack the system actually answers with, not dense alone.
    searcher = build_retriever(RUNTIME_RETRIEVER)
    results = searcher.search(
        args.query, k=args.k,
        project_ids=set(args.project_ids) if args.project_ids else None,
        source=args.source, dedup_projects=args.dedup_projects)
    for i, r in enumerate(results, 1):
        print(f"{i:2d}. [{r.score:.4f}] {r.acronym} - {r.title}")
        print(f"    {r.chunk_id} ({r.source}/{r.section})")
        text = " ".join(r.text.split())
        print(f"    {text[:300]}{'...' if len(text) > 300 else ''}")
    if not results:
        print("no results")


def cmd_smoke(args):
    from src.config import RUNTIME_RETRIEVER
    from src.retrieval.registry import build_retriever

    # Smokes the stack the system answers with. Hit counts moved when this went
    # dense-only -> hybrid_rerank on 2026-08-03; that is a re-baseline, not a
    # regression, and the numbers before/after are in working-plan.md.
    searcher = build_retriever(RUNTIME_RETRIEVER)
    cases = [json.loads(line) for line in
             Path(args.eval_file).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    hits = 0
    for case in cases:
        results = searcher.search(case["query"], k=10)
        got = [r.project_id for r in results]
        hit = case["expect_project_id"] in got
        hits += hit
        print(f"[{'HIT ' if hit else 'MISS'}] {case['query'][:70]!r} "
              f"-> expected {case['expect_project_id']}"
              + ("" if hit else f", got {got[:5]}..."))
    print(f"\n{hits}/{len(cases)} hits@10")
    sys.exit(0 if hits >= 8 else 1)


def _print_table(columns, rows, max_rows=20, max_width=40):
    if not rows:
        print("(0 rows)")
        return
    shown = rows[:max_rows]
    cells = [[str(c) for c in columns]]
    for row in shown:
        cells.append([(s if len(s) <= max_width else s[:max_width - 3] + "...")
                      for s in (str(v) for v in row)])
    widths = [max(len(r[i]) for r in cells) for i in range(len(columns))]
    for j, row in enumerate(cells):
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))
        if j == 0:
            print("  ".join("-" * w for w in widths))
    if len(rows) > max_rows:
        print(f"... ({len(rows)} rows total, showing {max_rows})")
    else:
        print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")


def cmd_ask_sql(args):
    from src.llm import check_generator
    from src.retrieval.sql_path import SqlPath

    check_generator()
    path = SqlPath()
    if args.show_prompt:
        print("=== system prompt ===")
        print(path.system_prompt)
        print("=== user ===")
        print(args.question)
        print()
    result = path.ask(args.question)
    print("SQL:")
    print(f"  {result.sql}")
    if result.retried:
        print("(one retry used)")
    print()
    if result.ok:
        _print_table(result.columns, result.rows)
    else:
        print(f"ERROR (no answer): {result.error}")
        sys.exit(1)


def cmd_smoke_sql(args):
    from src.llm import check_generator
    from src.retrieval.sql_path import SqlPath, results_match

    check_generator()
    path = SqlPath()
    cases = [json.loads(line) for line in
             Path(args.eval_file).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    passed = 0
    for case in cases:
        _, want_rows = path.execute_trusted(case["sql"])
        result = path.ask(case["question"])
        ok = result.ok and results_match(result.rows, want_rows)
        passed += ok
        flag = "PASS" if ok else "FAIL"
        retry = " (retried)" if result.retried else ""
        print(f"[{flag}]{retry} {case['question']}")
        print(f"       gen: {result.sql}")
        if not ok:
            print(f"        gt: {case['sql']}")
            if result.error:
                print(f"       err: {result.error}")
            else:
                print(f"       got {len(result.rows)} rows,"
                      f" want {len(want_rows)}")
    print(f"\n{passed}/{len(cases)} execution accuracy")
    sys.exit(0 if passed >= 7 else 1)


def cmd_ask(args):
    from src.ask import Ask
    from src.embed_client import check_server as check_embed
    from src.llm import check_generator

    check_embed()
    check_generator()
    res = Ask().ask(args.question, k=args.k, mode=args.mode,
                    explain=args.explain)
    if not args.quiet:
        tag = f"{res.mode}"
        if res.router_fallback:
            tag += " (ROUTER FALLBACK)"
        print(f"[mode] {tag} - {res.router_reason}")
        if res.sql:
            print(f"[sql] {res.sql}")
        if res.chunks:
            print("[chunks]")
            for c in res.chunks:
                print(f"  [{c.score:.3f}] {c.project_id} {c.acronym} "
                      f"/{c.section}")
        if res.degraded:
            print(f"[degraded] {res.degraded}")
        if res.weak_filter:
            print("[weak_filter] structured filter matched >5000 projects")
        if res.citation_violations:
            print(f"[citation_violation] stripped {res.citation_violations}")
        if res.rows:
            _print_table(res.columns, res.rows)
        print("\n[answer]")
    print(res.answer)


def _fmt_timings(trace: dict) -> str:
    t = trace.get("timings", {})
    order = ["route", "sql", "search", "retrieve", "synth", "total"]
    parts = [f"{k} {t[k]:.2f}s" for k in order if k in t]
    return " | ".join(parts)


def print_ask_verbose(res, k: int):
    """Full internal trace of one ask: routing, generated SQL / evidence,
    per-stage timings, and the final answer. This is the exploration view -
    every 'event' the pipeline produced, so you can see WHY it answered."""
    bar = "=" * 70
    trace = res.trace or {}
    print(bar)
    print(f"Q: {res.question}")
    print(bar)

    tag = res.mode + (" (ROUTER FALLBACK)" if res.router_fallback else "")
    print(f"[route]   {tag}")
    if res.router_reason:
        print(f"          reason: {res.router_reason}")
    if _fmt_timings(trace):
        print(f"[timing]  {_fmt_timings(trace)}")

    # ---- structured path (sql mode, or the id-narrowing SQL of scoped) ----
    if res.sql:
        label = "SQL (id-narrowing filter)" if res.mode == "scoped" else "SQL"
        print(f"\n-- {label} " + "-" * (66 - len(label)))
        print("  " + res.sql.replace("\n", "\n  "))
        if trace.get("sql_retried"):
            print("  (one retry used)")
        if res.mode == "scoped":
            n_ids = trace.get("n_ids")
            notes = []
            if trace.get("subject_corrected"):
                notes.append("subject-filter corrected")
            if res.weak_filter:
                notes.append("WEAK FILTER >5000 ids")
            note = ("  [" + ", ".join(notes) + "]") if notes else ""
            print(f"  -> {n_ids} project id(s) matched{note}")

    # ---- SQL result rows (the structured 'ground truth' for this run) ----
    if res.rows:
        print("\n-- result rows " + "-" * 55)
        _print_table(res.columns, res.rows)

    # ---- retrieved evidence (the semantic 'ground truth': what was read) --
    if res.chunks:
        print(f"\n-- retrieved evidence ({len(res.chunks)} chunk(s) fed to "
              "synthesis) " + "-" * 8)
        for i, c in enumerate(res.chunks, 1):
            print(f"{i:2d}. [{c.score:.4f}] {c.project_id} {c.acronym} "
                  f"({c.source}/{c.section})")
            print(f"    {c.title}")
            body = " ".join(c.text.split())
            for line in textwrap.wrap(body, width=100)[:6]:
                print(f"      {line}")
            print()
    elif res.mode in ("vector", "scoped") and trace.get("status") != "zero_match":
        print("\n-- retrieved evidence --  (none survived; nothing to read)")

    # ---- degradations / faithfulness signals ----
    if res.degraded:
        print(f"[degraded] {res.degraded}")
    if trace.get("dropped_for_budget"):
        print(f"[budget]  dropped {trace['dropped_for_budget']} chunk(s) to fit context")
    if res.citation_violations:
        print(f"[citation] stripped {len(res.citation_violations)} unknown "
              f"citation(s): {res.citation_violations}")

    print("\n-- answer " + "-" * 60)
    print(res.answer)
    print()


def cmd_explore(args):
    """Interactive verbose REPL over the full pipeline. Servers load once."""
    from src.ask import Ask
    from src.embed_client import check_server as check_embed
    from src.llm import check_generator
    from src.config import (GEN_BACKEND, LLM_SERVER_LAUNCH_CMD,
                            SERVER_LAUNCH_CMD)

    try:
        check_embed()
        check_generator()
    except Exception as e:  # noqa: BLE001 - surface the launch commands
        print(f"A required backend is unreachable: {e}\n")
        print(f"  embeddings (bge, port 8080):\n    {SERVER_LAUNCH_CMD}\n")
        if GEN_BACKEND == "local":
            print(f"  LLM (Qwen3-8B, port 8081):\n    {LLM_SERVER_LAUNCH_CMD}")
        else:
            print("  generation: `claude` CLI must be on PATH "
                  "(Claude Code subscription)")
        sys.exit(1)

    mode = args.mode          # None = let the router decide
    k = args.k
    asker = Ask()
    print("Horizon Scout - interactive explore. Backends up.")
    print(f"mode={mode or 'auto (router decides)'}  k={k}")
    print("Commands:  :mode auto|sql|vector|scoped   :k <N>   :help   :q\n")

    while True:
        try:
            line = input("ask> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":q", ":quit", ":exit"):
            break
        if line == ":help":
            print("  :mode auto|sql|vector|scoped  force a route (auto = router)")
            print("  :k <N>                        set number of chunks retrieved")
            print("  :q                            quit")
            continue
        if line.startswith(":mode"):
            val = line.split(maxsplit=1)[1].strip() if len(line.split()) > 1 else ""
            if val == "auto":
                mode = None
                print("mode -> auto (router decides)")
            elif val in ("sql", "vector", "scoped"):
                mode = val
                print(f"mode -> {val}")
            else:
                print("usage: :mode auto|sql|vector|scoped")
            continue
        if line.startswith(":k"):
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                k = int(parts[1])
                print(f"k -> {k}")
            else:
                print("usage: :k <N>")
            continue

        try:
            res = asker.ask(line, k=k, mode=mode)
            print_ask_verbose(res, k)
        except Exception as e:  # noqa: BLE001 - keep the REPL alive
            print(f"[error] {type(e).__name__}: {e}\n")


def cmd_build_fts(args):
    from src.retrieval.lexical import build_fts_index

    n = build_fts_index()
    print(f"FTS (BM25) index built over {n} chunks -> table chunk_fts")


def _load_bench_cases(path):
    """Read a retrieval eval file. Accepts {query, gold_project_ids:[...]} or
    the legacy smoke shape {query, expect_project_id: id}."""
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        gold = obj.get("gold_project_ids")
        if gold is None and "expect_project_id" in obj:
            gold = [obj["expect_project_id"]]
        cases.append({"query": obj["query"], "gold": set(gold or [])})
    return cases


def cmd_bench_retrievers(args):
    """Retriever bake-off: score lexical / dense / hybrid / hybrid_rerank on a
    retrieval eval set (project-level gold) and print a comparison table."""
    from src.eval.metrics import METRICS, dedup_projects, score_ranking
    from src.retrieval.lexical import LexicalRetriever
    from src.retrieval.rerank import RerankClient
    from src.retrieval.registry import RETRIEVERS, build_retriever
    from src.retrieval.vector_search import VectorSearcher

    names = args.retrievers or list(RETRIEVERS)
    bad = [n for n in names if n not in RETRIEVERS]
    if bad:
        print(f"unknown retriever(s): {bad}; choose from {RETRIEVERS}")
        sys.exit(2)

    cases = _load_bench_cases(args.eval_file)
    if not cases:
        print(f"no cases in {args.eval_file}")
        sys.exit(1)

    # Build shared components once (reused across hybrid/hybrid_rerank).
    need_lex = any(n in ("lexical", "hybrid", "hybrid_rerank") for n in names)
    need_dense = any(n in ("dense", "hybrid", "hybrid_rerank") for n in names)
    need_rr = "hybrid_rerank" in names
    lexical = LexicalRetriever() if need_lex else None
    dense = VectorSearcher() if need_dense else None       # checks embed server
    reranker = None
    if need_rr:
        reranker = RerankClient()
        reranker.check_server()                            # checks rerank server

    built = {n: build_retriever(n, lexical=lexical, dense=dense,
                                reranker=reranker) for n in names}

    # metric_name -> retriever_name -> [per-query score]
    agg = {n: {m: [] for m in METRICS} for n in names}
    for case in cases:
        for n, r in built.items():
            chunks = r.search(case["query"], k=args.fetch)
            ranked = dedup_projects(chunks)
            scores = score_ranking(ranked, case["gold"], args.k)
            for m, v in scores.items():
                agg[n][m].append(v)

    metric_cols = [f"{m}@{args.k}" for m in METRICS]
    header = ["retriever"] + metric_cols
    rows = []
    for n in names:
        rows.append([n] + [f"{sum(agg[n][m]) / len(cases):.3f}" for m in METRICS])
    widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(header)]
    print(f"\nRetriever bake-off - {len(cases)} queries, fetch={args.fetch}, "
          f"eval@{args.k}  ({Path(args.eval_file).name})\n")
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    print()


def cmd_smoke_router(args):
    from collections import Counter

    from src.llm import check_generator
    from src.router.router import Router

    check_generator()
    router = Router()
    cases = [json.loads(line) for line in
             Path(args.eval_file).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    correct = 0
    confusion = Counter()
    for case in cases:
        d = router.route(case["question"])
        want = case["expect_mode"]
        alts = set(case.get("accept_also", []))
        ok = d.mode == want or d.mode in alts
        correct += ok
        confusion[(want, d.mode)] += 1
        flag = "OK  " if ok else "MISS"
        note = f"  (also ok: {alts})" if alts and not ok else ""
        print(f"[{flag}] want={want:6s} got={d.mode:6s} {case['question']}{note}")
    print(f"\n{correct}/{len(cases)} router accuracy")
    print("confusion (expected -> got):")
    for (want, got), n in sorted(confusion.items()):
        mark = "" if want == got else "  <-"
        print(f"  {want:6s} -> {got:6s}: {n}{mark}")
    sys.exit(0 if correct >= 11 else 1)


def cmd_validate_bank(args):
    from src.eval.bank import BankValidationError, bank_summary, load_bank

    try:
        questions = load_bank(args.bank)
    except BankValidationError as e:
        print(f"INVALID: {Path(args.bank).name}")
        for err in e.errors:
            print(f"  {err}")
        sys.exit(1)
    print(f"OK: {Path(args.bank).name} - {len(questions)} questions\n")
    print(bank_summary(questions))


def cmd_validate_record(args):
    """Schema-validate ONE drafted record - the /question-orchestrator slot-close gate.

    Reads one JSON object from a file (or stdin with `-`); prints every
    violation and exits 1, or prints OK and exits 0."""
    from src.eval.bank import validate_record

    try:
        raw = (sys.stdin.read() if args.record == "-"
               else Path(args.record).read_text(encoding="utf-8"))
    except OSError as e:
        print(f"INVALID: cannot read the record ({e})")
        sys.exit(1)
    raw = raw.strip()
    if not raw:
        print("INVALID: empty input - expected one JSON record")
        sys.exit(1)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"INVALID: not valid JSON ({e})")
        sys.exit(1)
    # Checked on the RAW string, not the parsed dict, so entities in keys
    # and nested values are both caught.
    entities = html_entity_hits(raw)
    if entities:
        print("INVALID: HTML entities in the record - the source text almost "
              "certainly had the literal character; unescape before "
              "validating:")
        for hit in entities:
            print(f"  {hit}")
        sys.exit(1)
    where = "record" if args.record == "-" else Path(args.record).name
    errors = validate_record(obj, where)
    if errors:
        print(f"INVALID: {where}")
        for err in errors:
            print(f"  {err}")
        sys.exit(1)
    qid = obj.get("question_id")
    print(f"OK: {qid} - {obj.get('expected_route')}/{obj.get('level')}"
          f"/{obj.get('subtype')} passes the bank schema")


def cmd_gap_report(args):
    from src.eval.batch import BatchError, gap_report

    try:
        print(gap_report(Path(args.bank), Path(args.drafts_dir),
                         Path(args.plan)))
    except BatchError as e:
        print(f"GAP REPORT FAILED:\n  {e}")
        sys.exit(1)


def cmd_next_ids(args):
    from src.eval.batch import BatchError, next_ids

    counts = {"sql": args.sql, "vector": args.vector, "hybrid": args.hybrid,
              "adversarial": args.adversarial}
    if not any(counts.values()):
        counts = {cell: 1 for cell in counts}
        preview = True
    else:
        preview = False
    try:
        assigned = next_ids(counts, Path(args.bank), Path(args.drafts_dir))
    except BatchError as e:
        print(f"ID ASSIGNMENT FAILED:\n  {e}")
        sys.exit(1)
    for cell, ids in assigned.items():
        if ids:
            label = "next free" if preview else f"{len(ids)} assigned"
            print(f"{cell:11s} {label}: {', '.join(ids)}")


def cmd_pick_parents(args):
    import json

    from src.eval.batch import BatchError, pick_parents

    exclude = tuple(i.strip() for i in (args.exclude or "").split(",")
                    if i.strip())
    try:
        parents = pick_parents(args.n, Path(args.bank), Path(args.drafts_dir),
                               exclude=exclude)
    except BatchError as e:
        print(f"PARENT SELECTION FAILED:\n  {e}")
        sys.exit(1)
    if len(parents) < args.n:
        print(f"# only {len(parents)} of {args.n} requested parents are "
              "available (untwinned and not excluded)", file=sys.stderr)
    print(json.dumps(parents, indent=2))


def _crosscheck_records(path: Path):
    """Accept either a working journal (accepted slots) or a staged draft
    jsonl (every line a record)."""
    from src.eval.batch import BatchError, load_journal, read_records

    lines = read_records(path)
    if any("kind" in line for line in lines):
        journal = load_journal(path)
        return [slot["record"] for slot in journal.slots.values()
                if slot.get("status") == "ACCEPTED"
                and isinstance(slot.get("record"), dict)]
    stray = [i for i, line in enumerate(lines, 1)
             if not isinstance(line.get("question_id"), str)]
    if stray:
        raise BatchError(
            f"{path.name} line(s) {stray} have no question_id and the file "
            "carries no `kind` field - this is neither a staged draft jsonl "
            "nor a typed working journal (a pre-2026-07-25 journal would look "
            "like this; there is nothing to cross-check in one)")
    return lines


def cmd_batch_crosscheck(args):
    """Collision + spread flags across a batch's accepted records and the
    promoted bank. Flags only - never a gate, never a redraft."""
    from src.eval.batch import BatchError, crosscheck, read_records, render_flags

    try:
        records = _crosscheck_records(Path(args.source))
        flags = crosscheck(records, read_records(Path(args.bank)))
    except BatchError as e:
        print(f"CROSSCHECK FAILED:\n  {e}")
        sys.exit(1)
    print(f"{len(records)} record(s) from {Path(args.source).name} vs "
          f"{Path(args.bank).name}\n")
    print(render_flags(flags))


def cmd_journal_append(args):
    """Append one slot transition to a /question-orchestrator working journal.

    The payload (fields to set, JSON object) comes from a file or stdin with
    `-` - quoted-heredoc it, the same pattern as validate-record, so quotes
    and `$` cannot break the shell. It is merged over the slot's latest line;
    the envelope is enforced; `record` stays opaque."""
    from src.eval.batch import BatchError, journal_append

    payload = None
    if args.payload:
        try:
            raw = (sys.stdin.read() if args.payload == "-"
                   else Path(args.payload).read_text(encoding="utf-8"))
        except OSError as e:
            print(f"APPEND REFUSED: cannot read the payload ({e})")
            sys.exit(1)
        raw = raw.strip()
        if raw:
            # The same transcription boundary as validate-record: entities in
            # an agent-returned package are refused, never silently written.
            entities = html_entity_hits(raw)
            if entities:
                print("APPEND REFUSED: HTML entities in the payload - the "
                      "source text almost certainly had the literal "
                      "character; unescape and re-append:")
                for hit in entities:
                    print(f"  {hit}")
                sys.exit(1)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"APPEND REFUSED: payload is not valid JSON ({e})")
                sys.exit(1)
    try:
        line = journal_append(Path(args.journal), args.question_id,
                              args.status, payload)
    except BatchError as e:
        print("APPEND REFUSED - journal untouched:")
        for msg in str(e).splitlines():
            print(f"  {msg}")
        sys.exit(1)
    merged = sorted(k for k in line if k not in ("kind", "question_id",
                                                 "status"))
    print(f"appended: {args.question_id} -> {args.status} "
          f"(fields: {', '.join(merged)})")


def cmd_write_batch(args):
    """Render the two canonical /question-orchestrator outputs from the journal."""
    from src.eval.batch import BatchError, write_batch

    try:
        res = write_batch(Path(args.journal),
                          Path(args.output_dir) if args.output_dir else None,
                          date=args.date, suffix=args.suffix,
                          bank_path=Path(args.bank), force=args.force)
    except BatchError as e:
        print("WRITE REFUSED - nothing written:")
        for line in str(e).splitlines():
            print(f"  {line}")
        sys.exit(1)
    print(f"draft bank: {res.draft_file}")
    print(f"report:     {res.report_file}")
    print(f"accepted ({len(res.accepted)}): "
          f"{', '.join(res.accepted) if res.accepted else '-'}")
    print(f"failed ({len(res.failed)}): "
          f"{', '.join(res.failed) if res.failed else '-'}")
    print(f"blocked ({len(res.blocked)}): "
          f"{', '.join(res.blocked) if res.blocked else '-'}")
    hard = [f for f in res.flags if f.level == "FLAG"]
    print(f"cross-check flags: {len(hard)} (see the report's Cross-check "
          "section)")
    print(f"\nreview the report, tick the boxes, then:\n"
          f"  ./.venv/Scripts/python.exe -m src.cli promote-drafts "
          f"{res.report_file}")


def cmd_frontier_report(args):
    """The /explore-corpus startup, in one call: where exploration has and has
    not been, this run's slice partition, the orientation block, next ids."""
    from src.eval.explore import (ExploreError, frontier_report,
                                  render_frontier_report)

    try:
        report = frontier_report(map_count=args.map,
                                 profile_path=Path(args.profile),
                                 bank_path=Path(args.bank))
    except ExploreError as e:
        print(f"FRONTIER REPORT FAILED:\n  {e}")
        sys.exit(1)
    print(render_frontier_report(report, args.map))


def cmd_verify_evidence(args):
    """Re-execute EVERY claim in an exploration journal - not a sample."""
    from src.eval.explore import (ExploreError, connect, load_journal,
                                  render_checks, verify_evidence)

    try:
        journal = load_journal(Path(args.journal))
    except ExploreError as e:
        print(f"VERIFY FAILED - journal unreadable:\n  {e}")
        sys.exit(1)
    con = connect()
    try:
        checks = verify_evidence(journal, con)
    finally:
        con.close()
    print(render_checks(checks))
    if any(c.status == "FAIL" for c in checks):
        sys.exit(1)


def cmd_explore_crosscheck(args):
    """Width, entity spread, near-duplicates and supply across a whole run."""
    from src.eval.explore import (ExploreError, crosscheck, load_journal,
                                  read_profile, render_flags)

    try:
        journal = load_journal(Path(args.journal))
    except ExploreError as e:
        print(f"CROSSCHECK FAILED:\n  {e}")
        sys.exit(1)
    flags = crosscheck(journal, read_profile(Path(args.profile)),
                       journal.header.get("targets"))
    print(f"{len(journal.order)} slice(s) from {Path(args.journal).name} vs "
          f"{Path(args.profile).name}\n")
    print(render_flags(flags))


def cmd_write_profile(args):
    """Grow corpus_profile.md from an exploration journal, by insertion."""
    from src.eval.explore import (ExploreError, render_write_result,
                                  write_profile)

    try:
        result = write_profile(Path(args.journal), args.version,
                               profile_path=Path(args.profile),
                               bank_path=Path(args.bank),
                               date=args.date, dry_run=args.dry_run)
    except ExploreError as e:
        print("WRITE REFUSED - nothing written:")
        for line in str(e).splitlines():
            print(f"  {line}")
        sys.exit(1)
    print(render_write_result(result))
    if args.dry_run:
        print("\n(dry run - the profile and src/config.py are untouched)")
    elif result.version_bumped:
        print(f"\nCORPUS_PROFILE_VERSION bumped to {result.version}. "
              "Review the profile, then present the summary to the user.")
    else:
        print("\n(wrote a non-canonical profile copy - "
              "CORPUS_PROFILE_VERSION left alone)")


def cmd_agent_trace(args):
    """What each agent in a run cost - time and tokens, per agent."""
    from src.eval.trace import (orchestrator_trace, render_traces,
                                session_dirs, trace_session)

    sessions = session_dirs()
    if args.session:
        sessions = [s for s in sessions if s.name.startswith(args.session)]
    if not sessions:
        print("No session with subagent transcripts found under "
              "~/.claude/projects/. Nothing to trace.")
        return
    session = sessions[0]
    print(f"Session {session.name}\n")
    print(render_traces(trace_session(session, since=args.since),
                        orchestrator_trace(session) if args.orchestrator
                        else None,
                        steps=args.steps))


def cmd_promote_drafts(args):
    """Append the APPROVE-ticked questions of a /question-orchestrator report to the
    bank. Deterministic: parses the report's decision boxes, validates the
    combined bank before writing, refuses loudly on any problem."""
    from src.eval.bank import bank_summary, load_bank
    from src.eval.promote import PromoteError, promote

    try:
        res = promote(Path(args.report), Path(args.bank))
    except PromoteError as e:
        print("PROMOTE REFUSED - bank untouched:")
        for line in str(e).splitlines():
            print(f"  {line}")
        sys.exit(1)
    print(f"draft file: {res.draft_file}")
    print(f"promoted ({len(res.promoted)}): "
          f"{', '.join(res.promoted) if res.promoted else '-'}")
    print(f"rejected ({len(res.rejected)}): "
          f"{', '.join(res.rejected) if res.rejected else '-'}\n")
    print(bank_summary(load_bank(args.bank)))


def cmd_archive_questions(args):
    """Move questions out of the bank into an archive file. Deterministic:
    validates the bank that would remain BEFORE writing either file, and
    refuses loudly on any problem. Archived ids stay permanently taken."""
    from src.eval.archive import ArchiveError, archive_questions
    from src.eval.bank import bank_summary, load_bank

    per_id: dict[str, str] = {}
    if args.reasons:
        per_id = json.loads(Path(args.reasons).read_text(encoding="utf-8"))
        if not isinstance(per_id, dict):
            print("--reasons must be a JSON object of {question_id: reason}")
            sys.exit(1)

    if args.dry_run:
        bank = {q.question_id: q for q in load_bank(args.bank)}
        missing = [q for q in args.ids if q not in bank]
        print(f"DRY RUN - nothing written. {len(args.ids)} question(s) would "
              f"move to {args.archive}\n")
        for qid in args.ids:
            q = bank.get(qid)
            where = (f"{q.expected_route}/{q.level}/{q.subtype}"
                     if q else "NOT IN BANK")
            print(f"  {qid:8} {where:28} {per_id.get(qid, args.reason)}")
        print(f"\nbank would go {len(bank)} -> {len(bank) - len(args.ids)}")
        if missing:
            print(f"REFUSAL AHEAD - not in bank: {', '.join(missing)}")
            sys.exit(1)
        return

    try:
        res = archive_questions(args.ids, args.reason, Path(args.bank),
                                Path(args.archive), per_id_reasons=per_id)
    except ArchiveError as e:
        print("ARCHIVE REFUSED - bank untouched:")
        for line in str(e).splitlines():
            print(f"  {line}")
        sys.exit(1)
    print(f"archive file: {res.archive_file}")
    print(f"archived ({len(res.archived)}): {', '.join(res.archived)}")
    print(f"bank now {res.remaining} question(s)\n")
    print(bank_summary(load_bank(args.bank)))


def cmd_judge_file(args):
    """Judge a jsonl of {question_id, question, reference_answer, answer,
    contexts?, adversarial?, expect_pass?}. Ordinary cases go through the
    RAGAS metrics; adversarial ones through the rubric overlay. Cases run
    concurrently (up to --concurrency claude -p processes)."""
    # ragas_judge first: it installs the vertexai import shim ragas needs.
    from src.judge.ragas_judge import JudgePool

    import ragas

    pool = JudgePool(model_key=args.model, concurrency=args.concurrency)
    cases = [json.loads(line) for line in
             Path(args.eval_file).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    print(f"judge={pool.model}  ragas={ragas.__version__}  "
          f"concurrency={pool.concurrency}  {len(cases)} case(s)\n")
    results = pool.judge_all(cases)

    def fmt(x):
        return "-" if x is None else f"{x:.2f}"

    mismatches, errors = 0, 0
    for case, r in zip(cases, results):
        if isinstance(r, Exception):
            errors += 1
            print(f"[ERROR] {case.get('question_id')}: {r}")
            continue
        expect = case.get("expect_pass")
        if expect is None:
            flag = "PASS" if r.passed else "FAIL"
        elif r.passed == expect:
            flag = "OK  "
        else:
            flag = "MISM"
            mismatches += 1
        scores = ("(overlay)" if r.path == "overlay" else
                  f"faith={fmt(r.faithfulness)} "
                  f"factual={fmt(r.factual_correctness)}")
        print(f"[{flag}] {r.question_id} [{r.path}] {scores} "
              f"passed={r.passed}"
              + (f" (expected {expect})" if expect is not None else ""))
        if r.detail:
            print(f"       {r.detail}")
    print(f"\n{len(cases) - mismatches - errors}/{len(cases)} as expected"
          f" ({errors} error(s))")
    sys.exit(0 if mismatches == 0 and errors == 0 else 1)


def cmd_run_bank(args):
    """Bank -> ask -> judge -> a traced record per question plus a report."""
    from src.embed_client import check_server as check_embed
    from src.eval.run import CONDITIONS, ConsoleProgress, run_bank
    from src.llm import check_generator

    bad = [c for c in args.conditions if c not in CONDITIONS]
    if bad:
        print(f"unknown condition(s) {bad}; choose from {sorted(CONDITIONS)}")
        sys.exit(2)
    if args.resume and not args.run_id:
        print("--resume needs --run-id: it resumes a specific run directory.")
        sys.exit(2)

    check_generator()
    # Only the topical paths need the embedder; a SQL-only selection does not.
    if args.routes != ["sql"]:
        check_embed()

    try:
        meta = run_bank(Path(args.bank), args.conditions, k=args.k,
                        judge=not args.no_judge, ids=args.ids,
                        routes=args.routes, limit=args.limit,
                        run_id=args.run_id, judge_model=args.model,
                        resume=args.resume, progress=ConsoleProgress())
    except ValueError as e:
        print(f"\n{e}")
        sys.exit(2)
    if meta["n_errors"]:
        sys.exit(1)


def cmd_run_retrieval(args):
    """Study 1: the four-condition retrieval ladder over the bank's vector
    questions - fetch once per question, assemble four conditions from it,
    generate and judge each, then write the ladder report."""
    from src.embed_client import check_server as check_embed
    from src.eval.retrieval_run import (CONDITIONS, ConsoleProgress,
                                        build_components, run_retrieval)
    from src.llm import check_generator

    bad = [c for c in args.conditions if c not in CONDITIONS]
    if bad:
        print(f"unknown condition(s) {bad}; choose from {list(CONDITIONS)}")
        sys.exit(2)
    if args.resume and not args.run_id:
        print("--resume needs --run-id: it resumes a specific run directory.")
        sys.exit(2)

    # Every server this run depends on is proven up before the first paid
    # generation. Four conditions x forty questions is 160 answers; finding out
    # at answer 41 that the reranker is down is the expensive way to learn it.
    check_generator()                    # generation goes through `claude -p`
    reranker = None
    if any(c in ("dense", "hybrid", "hybrid_rerank") for c in args.conditions):
        check_embed()
    if "hybrid_rerank" in args.conditions:
        from src.retrieval.rerank import RerankClient
        reranker = RerankClient()
        reranker.check_server()          # raises with the relaunch command

    try:
        # Built here rather than inside the run so the lexical retriever's own
        # "FTS index not found - run build-fts" failure also lands before
        # anything is spent. That check is not re-implemented here: it is
        # LexicalRetriever.__init__'s, surfaced.
        components = build_components(args.conditions, reranker=reranker)
    except (RuntimeError, ValueError) as e:
        print(f"\n{e}")
        sys.exit(2)

    try:
        meta = run_retrieval(Path(args.bank), args.conditions,
                             depth=args.depth, k_gen=args.k_gen,
                             judge=not args.no_judge, ids=args.ids,
                             routes=args.routes, limit=args.limit,
                             run_id=args.run_id, judge_model=args.model,
                             resume=args.resume, components=components,
                             progress=ConsoleProgress())
    except ValueError as e:
        print(f"\n{e}")
        sys.exit(2)
    if meta["n_errors"]:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(prog="python -m src.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build-index", help="chunk, embed, and index")
    b.add_argument("--limit", type=int, default=None,
                   help="dev subset: first N projects by id")
    b.add_argument("--chunk-target", type=int, default=CHUNK_TARGET)
    b.add_argument("--split-overlap", type=int, default=SPLIT_OVERLAP)
    b.set_defaults(fn=cmd_build_index)

    s = sub.add_parser("search", help="vector search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=10)
    s.add_argument("--source", choices=["report", "objective"], default=None)
    s.add_argument("--project-ids", type=int, nargs="+", default=None)
    s.add_argument("--dedup-projects", action="store_true",
                   help="keep only the best hit per project")
    s.set_defaults(fn=cmd_search)

    m = sub.add_parser("smoke", help="hit/miss@10 over the smoke query file")
    m.add_argument("--eval-file", default=str(ROOT / "eval" / "smoke_vector.jsonl"))
    m.set_defaults(fn=cmd_smoke)

    a = sub.add_parser("ask-sql", help="question -> SQL -> results")
    a.add_argument("question")
    a.add_argument("--show-prompt", action="store_true")
    a.set_defaults(fn=cmd_ask_sql)

    q = sub.add_parser("smoke-sql", help="execution accuracy over smoke_sql.jsonl")
    q.add_argument("--eval-file", default=str(ROOT / "eval" / "smoke_sql.jsonl"))
    q.set_defaults(fn=cmd_smoke_sql)

    k = sub.add_parser("ask", help="full router/scoped/synthesis pipeline")
    k.add_argument("question")
    k.add_argument("--mode", choices=["sql", "vector", "scoped"], default=None,
                   help="bypass the router")
    k.add_argument("-k", type=int, default=10)
    k.add_argument("--explain", action="store_true",
                   help="add LLM narration to SQL answers")
    k.add_argument("--quiet", action="store_true", help="answer only")
    k.set_defaults(fn=cmd_ask)

    e = sub.add_parser("explore", help="interactive verbose REPL over the pipeline")
    e.add_argument("--mode", choices=["sql", "vector", "scoped"], default=None,
                   help="force a route for the whole session (default: router)")
    e.add_argument("-k", type=int, default=10)
    e.set_defaults(fn=cmd_explore)

    bf = sub.add_parser("build-fts", help="build the lexical BM25 (DuckDB FTS) index")
    bf.set_defaults(fn=cmd_build_fts)

    br = sub.add_parser("bench-retrievers",
                        help="bake-off: lexical/dense/hybrid/hybrid_rerank "
                             "(diagnostic - RQ2 was dropped 2026-08-03, so "
                             "this is no longer part of the study)")
    br.add_argument("--eval-file", default=str(ROOT / "eval" / "smoke_vector.jsonl"))
    br.add_argument("--retrievers", nargs="+", default=None,
                    help="subset to run (default: all four)")
    br.add_argument("-k", type=int, default=10, help="project-level eval cutoff")
    br.add_argument("--fetch", type=int, default=100,
                    help="chunks retrieved per query before dedup to projects")
    br.set_defaults(fn=cmd_bench_retrievers)

    r = sub.add_parser("smoke-router", help="router accuracy over smoke_router.jsonl")
    r.add_argument("--eval-file", default=str(ROOT / "eval" / "smoke_router.jsonl"))
    r.set_defaults(fn=cmd_smoke_router)

    vb = sub.add_parser("validate-bank",
                        help="validate a question-bank jsonl (M5 schema)")
    vb.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    vb.set_defaults(fn=cmd_validate_bank)

    vr = sub.add_parser("validate-record",
                        help="schema-validate ONE drafted record (JSON file "
                             "or '-' for stdin)")
    vr.add_argument("record")
    vr.set_defaults(fn=cmd_validate_record)

    gr = sub.add_parser("gap-report",
                        help="filled / staged / target per bank cell against "
                             "the plan doc's allocation table")
    gr.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    gr.add_argument("--drafts-dir", default=str(ROOT / "eval" / "drafts"))
    gr.add_argument("--plan", default=str(ROOT / "horizon-scout.md"))
    gr.set_defaults(fn=cmd_gap_report)

    ni = sub.add_parser("next-ids",
                        help="next free question ids, counting the bank AND "
                             "every staged draft file")
    ni.add_argument("--sql", type=int, default=0)
    ni.add_argument("--vector", type=int, default=0)
    ni.add_argument("--hybrid", type=int, default=0)
    ni.add_argument("--adversarial", type=int, default=0,
                    help="level ADV, any costume route (adv-NN)")
    ni.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    ni.add_argument("--drafts-dir", default=str(ROOT / "eval" / "drafts"))
    ni.set_defaults(fn=cmd_next_ids)

    pp = sub.add_parser("pick-parents",
                        help="the answerable bank questions an adversarial "
                             "batch derives from - untwinned, spread across "
                             "route and subtype, as JSON records")
    pp.add_argument("--n", type=int, default=3)
    pp.add_argument("--exclude", default="",
                    help="comma-separated ids another tab has claimed")
    pp.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    pp.add_argument("--drafts-dir", default=str(ROOT / "eval" / "drafts"))
    pp.set_defaults(fn=cmd_pick_parents)

    bc = sub.add_parser("batch-crosscheck",
                        help="near-duplicate / entity / axis collisions "
                             "across a batch and the bank")
    bc.add_argument("source", help="working journal or staged draft jsonl")
    bc.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    bc.set_defaults(fn=cmd_batch_crosscheck)

    ja = sub.add_parser("journal-append",
                        help="append one slot transition to a /question-orchestrator "
                             "working journal; payload merged over the "
                             "slot's latest line, envelope enforced")
    ja.add_argument("journal")
    ja.add_argument("--id", required=True, dest="question_id",
                    help="slot question_id, e.g. hyb-08")
    ja.add_argument("--status", required=True,
                    help="new slot status (DRAFTING, REVIEWING, JUDGING, "
                         "FIXING, ACCEPTED, FAILED, BLOCKED)")
    ja.add_argument("--payload", default=None,
                    help="JSON object of fields to set: a file path, or '-' "
                         "for stdin (quoted heredoc). Omit for a pure "
                         "status change.")
    ja.set_defaults(fn=cmd_journal_append)

    wb = sub.add_parser("write-batch",
                        help="render the staged draft jsonl + review report "
                             "from a /question-orchestrator working journal")
    wb.add_argument("journal")
    wb.add_argument("--output-dir", default=None,
                    help="default: the journal's own directory")
    wb.add_argument("--date", default=None,
                    help="default: the journal's batch header, else its "
                         "filename")
    wb.add_argument("--suffix", default="",
                    help="paired name suffix, e.g. -2, when today's files "
                         "already exist")
    wb.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    wb.add_argument("--force", action="store_true",
                    help="overwrite existing output files")
    wb.set_defaults(fn=cmd_write_batch)

    fr = sub.add_parser("frontier-report",
                        help="/explore-corpus startup: frontier, slice "
                             "partition, orientation block, next ids")
    fr.add_argument("--map", type=int, default=0,
                    help="buckets to map this run; also emits the slice "
                         "partition and the seed standard")
    fr.add_argument("--profile",
                    default=str(ROOT / "src" / "retrieval" /
                                "corpus_profile.md"))
    fr.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    fr.set_defaults(fn=cmd_frontier_report)

    ve = sub.add_parser("verify-evidence",
                        help="re-execute every claim in an exploration "
                             "journal (exhaustive, not sampled)")
    ve.add_argument("journal")
    ve.set_defaults(fn=cmd_verify_evidence)

    ec = sub.add_parser("explore-crosscheck",
                        help="width / entity / near-duplicate / supply flags "
                             "across an exploration run")
    ec.add_argument("journal")
    ec.add_argument("--profile",
                    default=str(ROOT / "src" / "retrieval" /
                                "corpus_profile.md"))
    ec.set_defaults(fn=cmd_explore_crosscheck)

    wp = sub.add_parser("write-profile",
                        help="grow corpus_profile.md from an exploration "
                             "journal, by insertion")
    wp.add_argument("journal")
    wp.add_argument("version", help="the new profile version, e.g. cp4")
    wp.add_argument("--profile",
                    default=str(ROOT / "src" / "retrieval" /
                                "corpus_profile.md"))
    wp.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    wp.add_argument("--date", default=None,
                    help="default: the journal's run header, else today")
    wp.add_argument("--dry-run", action="store_true",
                    help="render and report, write nothing")
    wp.set_defaults(fn=cmd_write_profile)

    at = sub.add_parser("agent-trace",
                        help="per-agent time and token spend for a run, from "
                             "the subagent transcripts")
    at.add_argument("--session", default=None,
                    help="session id prefix; default: the most recent session "
                         "that spawned subagents")
    at.add_argument("--since", default=None,
                    help="ISO timestamp; keep only agents that ended after "
                         "it, to separate one run from an earlier one")
    at.add_argument("--orchestrator", action="store_true",
                    help="include the parent session's own spend as a row")
    at.add_argument("--steps", action="store_true",
                    help="break each agent down per instruction: what the "
                         "first draft cost vs its fix round, vs each round "
                         "the warm judge ruled on")
    at.set_defaults(fn=cmd_agent_trace)

    pd = sub.add_parser("promote-drafts",
                        help="append APPROVE-ticked /question-orchestrator drafts "
                             "to the bank")
    pd.add_argument("report", help="draft-report .md with ticked decision boxes")
    pd.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    pd.set_defaults(fn=cmd_promote_drafts)

    aq = sub.add_parser("archive-questions",
                        help="move questions out of the bank into an archive "
                             "file (the bank is never hand-edited)")
    aq.add_argument("--ids", nargs="+", required=True,
                    help="question ids to archive")
    aq.add_argument("--reason", required=True,
                    help="why - recorded on every archived envelope")
    aq.add_argument("--reasons",
                    help="optional JSON {question_id: reason} overriding "
                         "--reason per question")
    aq.add_argument("--dry-run", action="store_true",
                    help="print what would move and write nothing")
    aq.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    aq.add_argument("--archive",
                    default=str(ROOT / "eval" / "archive" /
                                "bank-trimmed-2026-08-03.jsonl"))
    aq.set_defaults(fn=cmd_archive_questions)

    jf = sub.add_parser("judge-file",
                        help="LLM judge over {question, reference, answer} jsonl")
    jf.add_argument("--eval-file", default=str(ROOT / "eval" / "judge_smoke.jsonl"))
    jf.add_argument("--model", choices=["haiku", "sonnet"],
                    default=JUDGE_DEFAULT)
    jf.add_argument("--concurrency", type=int, default=CLAUDE_CONCURRENCY,
                    help=f"parallel judge processes "
                         f"(default {CLAUDE_CONCURRENCY}, max 16)")
    jf.set_defaults(fn=cmd_judge_file)

    rb = sub.add_parser("run-bank",
                        help="run the question bank end to end: execute, "
                             "judge, and write a traced run report")
    rb.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    rb.add_argument("--conditions", nargs="+", default=["router"],
                    help="router | force-sql | force-vector | always-hybrid "
                         "(default: router)")
    rb.add_argument("-k", type=int, default=10,
                    help="chunks retrieved per topical question")
    rb.add_argument("--no-judge", action="store_true",
                    help="phase A only - execute and trace, spend nothing on "
                         "judging. The judge cases are still saved, so "
                         "--resume can judge them later without re-running "
                         "generation")
    rb.add_argument("--ids", nargs="+", default=None,
                    help="run only these question ids, in this order")
    rb.add_argument("--routes", nargs="+", default=None,
                    help="run only these expected_routes")
    rb.add_argument("--limit", type=int, default=None)
    rb.add_argument("--run-id", default=None,
                    help="name the run directory (default: a timestamp)")
    rb.add_argument("--resume", action="store_true",
                    help="continue the run named by --run-id: skip questions "
                         "already executed, and judge any that are still owed "
                         "a verdict without paying for generation again")
    rb.add_argument("--model", choices=["haiku", "sonnet"],
                    default=JUDGE_DEFAULT, help="judge model")
    rb.set_defaults(fn=cmd_run_bank)

    rr = sub.add_parser("run-retrieval",
                        help="the four-condition retrieval ladder - one fetch "
                             "per question, four conditions assembled from it, "
                             "judged and reported. DIAGNOSTIC ONLY: RQ2 was "
                             "dropped as a study 2026-08-03; the 07-29 ladder "
                             "run is the recorded evidence for picking "
                             "config.RUNTIME_RETRIEVER, and the study now runs "
                             "that one stack via run-bank")
    rr.add_argument("--bank", default=str(ROOT / "eval" / "bank.jsonl"))
    rr.add_argument("--conditions", nargs="+",
                    default=["lexical", "dense", "hybrid", "hybrid_rerank"],
                    help="lexical | dense | hybrid | hybrid_rerank "
                         "(default: all four)")
    rr.add_argument("--depth", type=int, default=FUSE_CANDIDATES,
                    help=f"how deep each of lexical and dense is fetched ONCE "
                         f"per question; every condition is assembled from "
                         f"those two lists and the ranking metrics are "
                         f"computed off the full list, not off the chunks the "
                         f"generator saw. Default {FUSE_CANDIDATES} because it "
                         f"equals FUSE_CANDIDATES, which makes the hybrid "
                         f"condition identical to the shipped HybridRetriever")
    rr.add_argument("--k-gen", type=int, default=10,
                    help="chunks handed to the generator per condition")
    rr.add_argument("--no-judge", action="store_true",
                    help="phase A only - execute and trace, spend nothing on "
                         "judging. The judge cases are still saved, so "
                         "--resume can judge them later without re-running "
                         "generation")
    rr.add_argument("--ids", nargs="+", default=None,
                    help="run only these question ids, in this order")
    rr.add_argument("--routes", nargs="+", default=["vector"],
                    help="run only these expected_routes (default: vector - "
                         "Study 1 is the vector cell)")
    rr.add_argument("--limit", type=int, default=None)
    rr.add_argument("--run-id", default=None,
                    help="name the run directory (default: "
                         "retrieval_<timestamp>)")
    rr.add_argument("--resume", action="store_true",
                    help="continue the run named by --run-id: skip "
                         "(condition, question) pairs already executed, and "
                         "judge any still owed a verdict without paying for "
                         "generation again")
    rr.add_argument("--model", choices=["haiku", "sonnet"],
                    default=JUDGE_DEFAULT, help="judge model")
    rr.set_defaults(fn=cmd_run_retrieval)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
