"""Live demonstration of the three scoped edge policies against both servers.
Exercises ScopedRetriever.retrieve (SQL narrowing + filtered vector search);
synthesis is validated separately (unit tests) and is the only throttle-heavy
step. Run with bge on 8080 and Qwen on 8081."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.retrieval.scoped import ScopedRetriever          # noqa: E402
from src.retrieval.sql_path import SqlPath                # noqa: E402
from src.retrieval.vector_search import VectorSearcher    # noqa: E402


class BadLlm:
    """Induces a SQL failure: returns invalid SQL on both attempts."""
    def chat(self, messages, **kw):
        return "SELECT DISTINCT p.id FROM project p WHERE p.nonexistent_col = 1"


def show(tag, r):
    n_ids = None if r.project_ids is None else len(r.project_ids)
    print(f"\n=== {tag} ===")
    print(f"  status={r.status} degraded={r.degraded} weak_filter={r.weak_filter}")
    print(f"  n_ids={n_ids} n_chunks={len(r.chunks)}")
    print(f"  sql={r.sql}")
    if r.chunks:
        c = r.chunks[0]
        print(f"  top chunk: {c.project_id} {c.acronym} /{c.section} [{c.score:.3f}]")


def main():
    searcher = VectorSearcher()
    hybrid = ScopedRetriever(searcher)

    # 1. zero-match: KP-coordinated projects (0 in the DB) -> status zero_match
    show("ZERO-MATCH (North-Korea-coordinated fusion projects)",
         hybrid.retrieve("fusion energy projects coordinated in North Korea"))

    # 2. weak-filter: CLOSED status matches 30k > 5000 -> weak_filter=true
    show("WEAK-FILTER (closed projects about energy)",
         hybrid.retrieve("closed projects about energy storage"))

    # 3. sql-failed (induced): narrowing SQL always invalid -> degrade to vector
    bad_hybrid = ScopedRetriever(
        searcher, narrow_sql=SqlPath(llm=BadLlm(), row_limit=50000))
    show("SQL-FAILED (induced bad narrowing SQL)",
         bad_hybrid.retrieve("projects developing solar hydrogen production"))

    # 4. ok: real topic + country constraint that has matches in the dev index
    show("OK (German-coordinated ocean/marine projects)",
         hybrid.retrieve("German-coordinated projects on ocean and marine observation"))


if __name__ == "__main__":
    main()
