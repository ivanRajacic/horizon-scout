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
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

from src.config import CHUNK_TARGET, ROOT, SPLIT_OVERLAP


def cmd_build_index(args):
    from src.ingest.embed_index import build_index

    build_index(limit=args.limit, chunk_target=args.chunk_target,
                split_overlap=args.split_overlap)


def cmd_search(args):
    from src.retrieval.vector_search import VectorSearcher

    searcher = VectorSearcher()
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
    from src.retrieval.vector_search import VectorSearcher

    searcher = VectorSearcher()
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
    from src.llm import check_server
    from src.retrieval.sql_path import SqlPath

    check_server()
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
    from src.llm import check_server
    from src.retrieval.sql_path import SqlPath, results_match

    check_server()
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
    from src.llm import check_server as check_llm

    check_embed()
    check_llm()
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
    from src.llm import check_server as check_llm
    from src.config import LLM_SERVER_LAUNCH_CMD, SERVER_LAUNCH_CMD

    try:
        check_embed()
        check_llm()
    except Exception as e:  # noqa: BLE001 - surface the launch commands
        print(f"A required llama-server is unreachable: {e}\n")
        print("Start both servers in separate terminals, then rerun:\n")
        print(f"  embeddings (bge, port 8080):\n    {SERVER_LAUNCH_CMD}\n")
        print(f"  LLM (Qwen3-8B, port 8081):\n    {LLM_SERVER_LAUNCH_CMD}")
        sys.exit(1)

    mode = args.mode          # None = let the router decide
    k = args.k
    asker = Ask()
    print("Horizon Scout - interactive explore. Both servers up.")
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

    from src.llm import check_server
    from src.router.router import Router

    check_server()
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
                        help="bake-off: lexical/dense/hybrid/hybrid_rerank")
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

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
