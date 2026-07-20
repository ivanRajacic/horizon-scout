"""Profile the available text fields and derive the chunking decision.

Usage:  python -m src.ingest.profile  (from the repo root, after load.py)

Appends a text-profile section to data/processed/ingest_report.md.

Token counts are estimated as chars/4 - close enough for a chunk-size
decision (English prose runs ~4 chars per token for common tokenizers).
"""

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "processed" / "horizon.duckdb"
REPORT_PATH = ROOT / "data" / "processed" / "ingest_report.md"

PCTS = [0.10, 0.25, 0.50, 0.75, 0.90, 0.99]


def profile_field(con, table: str, col: str):
    row = con.execute(f"""
        SELECT count(*) AS total,
               count({col}) AS non_null,
               avg(length({col})),
               {', '.join(f'quantile_cont(length({col}), {p})' for p in PCTS)},
               max(length({col}))
        FROM {table}
    """).fetchone()
    total, non_null, mean = row[0], row[1], row[2]
    pcts = row[3:3 + len(PCTS)]
    mx = row[-1]
    return total, non_null, mean, pcts, mx


def fmt_row(label, total, non_null, mean, pcts, mx):
    toks = lambda c: f"{c / 4:,.0f}" if c is not None else "-"
    cells = " | ".join(f"{p:,.0f} (~{toks(p)})" for p in pcts)
    return (f"| {label} | {non_null:,}/{total:,} | {mean:,.0f} (~{toks(mean)}) "
            f"| {cells} | {mx:,} (~{toks(mx)}) |")


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    lines = ["", "## Text profile (chars, ~tokens at 4 chars/token)", "",
             "| field | non-null | mean | " +
             " | ".join(f"p{int(p * 100)}" for p in PCTS) + " | max |",
             "|---|---|---|" + "---|" * (len(PCTS) + 1)]

    for label, table, col in [
        ("project.objective", "project", "objective"),
        ("project.title", "project", "title"),
        ("project.keywords", "project", "keywords"),
        ("report_text.teaser", "report_text", "teaser"),
        ("report_text.summary", "report_text", "summary"),
        ("report_text.workPerformed", "report_text", "workPerformed"),
        ("report_text.finalResults", "report_text", "finalResults"),
        ("report_text combined (summary+work+final)", "report_text",
         "concat_ws(chr(10), summary, workPerformed, finalResults)"),
    ]:
        lines.append(fmt_row(label, *profile_field(con, table, col)))
        print(lines[-1])

    p99_chars = con.execute(
        "SELECT quantile_cont(length(objective), 0.99) FROM project"
    ).fetchone()[0]
    over_512 = con.execute(
        "SELECT count(*) FROM project WHERE length(objective) > 512 * 4"
    ).fetchone()[0]

    comb = ("concat_ws(chr(10), summary, workPerformed, finalResults)")
    med_c, p99_c = con.execute(
        f"SELECT quantile_cont(length({comb}), 0.5), "
        f"quantile_cont(length({comb}), 0.99) FROM report_text"
    ).fetchone()
    paras = con.execute(
        "SELECT quantile_cont(len(string_split(summary, chr(10))), 0.5) "
        "FROM report_text").fetchone()[0]
    total_chars = con.execute(
        f"SELECT sum(length({comb})) FROM report_text").fetchone()[0]

    lines += [
        "",
        "## Chunking decision",
        "",
        "Corpus = `report_text` (summary + workPerformed + finalResults, "
        "34,712 reports) plus `project.objective` (35,389 projects).",
        "",
        f"- Combined report text: median ~{med_c / 4:,.0f} tokens, p99 "
        f"~{p99_c / 4:,.0f}, total ~{total_chars / 4 / 1e6:,.0f}M tokens. "
        "Far too long to embed whole - chunking is required.",
        f"- The text has clean newline paragraph structure (median "
        f"{paras:.0f} paragraphs per summary field), so the SPEC's "
        "paragraph-aware splitter applies directly.",
        "- Decision: **split report text on paragraph boundaries into "
        "~300-500 token chunks with ~50 token overlap**, one section at a "
        "time (summary / workPerformed / finalResults) so no chunk spans "
        "section boundaries; carry projectID + section on every chunk. "
        f"Expect roughly {total_chars / 4 / 400 / 1000:,.0f}k chunks.",
        f"- `project.objective` (p99 ~{p99_chars / 4:,.0f} tokens; "
        f"{over_512:,} of 35,389 exceed ~512): **embed whole, one chunk "
        "per project**, prefixed with the project title.",
        "",
    ]
    con.close()

    text = REPORT_PATH.read_text(encoding="utf-8")
    marker = "\n## Text profile"
    if marker in text:  # rerun: replace the old profile section
        text = text[:text.index(marker)]
    REPORT_PATH.write_text(text.rstrip() + "\n" + "\n".join(lines),
                           encoding="utf-8")
    print(f"\nAppended profile to {REPORT_PATH}")


if __name__ == "__main__":
    main()
