"""Project CLI.

  python -m src.cli build-index [--limit N] [--chunk-target 400] [--split-overlap 50]
  python -m src.cli search "query" [-k 10] [--source report|objective]
                                   [--project-ids ID ...] [--dedup-projects]
  python -m src.cli smoke [--eval-file eval/smoke_vector.jsonl]
  python -m src.cli ask-sql "question" [--show-prompt]
  python -m src.cli smoke-sql [--eval-file eval/smoke_sql.jsonl]
  python -m src.cli ask "question" [--mode sql|vector|hybrid] [-k 10]
                                   [--explain] [--quiet]
  python -m src.cli smoke-router [--eval-file eval/smoke_router.jsonl]
"""

import argparse
import json
import sys
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

    k = sub.add_parser("ask", help="full router/hybrid/synthesis pipeline")
    k.add_argument("question")
    k.add_argument("--mode", choices=["sql", "vector", "hybrid"], default=None,
                   help="bypass the router")
    k.add_argument("-k", type=int, default=10)
    k.add_argument("--explain", action="store_true",
                   help="add LLM narration to SQL answers")
    k.add_argument("--quiet", action="store_true", help="answer only")
    k.set_defaults(fn=cmd_ask)

    r = sub.add_parser("smoke-router", help="router accuracy over smoke_router.jsonl")
    r.add_argument("--eval-file", default=str(ROOT / "eval" / "smoke_router.jsonl"))
    r.set_defaults(fn=cmd_smoke_router)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
