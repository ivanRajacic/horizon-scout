"""Load report-summary full text from the CORDIS XML bundle into DuckDB.

The DET reportSummaries.csv ships metadata only; the XML bundle
(cordis-h2020reports-xml.zip, extracted to data/raw/reports/) carries the
actual text: teaser, summary, workPerformed, finalResults.

Usage:  python -m src.ingest.load_reports  (from the repo root, after load.py)

Creates table report_text in data/processed/horizon.duckdb and updates the
"Report text (XML)" section of data/processed/ingest_report.md.
Rerun src.ingest.profile afterwards to refresh the text profile.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "data" / "raw" / "reports"
DB_PATH = ROOT / "data" / "processed" / "horizon.duckdb"
REPORT_PATH = ROOT / "data" / "processed" / "ingest_report.md"

# direct children of <result>; nested <rcn>/<id> live under <relations>
FIELDS = ["rcn", "id", "title", "teaser", "summary", "workPerformed",
          "finalResults", "periodNumber", "periodFrom", "periodTo",
          "lastUpdateDate"]


def parse_report(path: Path) -> dict:
    root = ET.parse(path).getroot()
    row = {f: root.findtext(f) for f in FIELDS}
    proj = root.find("relations/associations/project")
    row["projectID"] = proj.findtext("id") if proj is not None else None
    return row


def main():
    files = sorted(REPORTS_DIR.glob("result-rcn-*.xml"))
    if not files:
        sys.exit(f"no result-rcn-*.xml files under {REPORTS_DIR}")
    print(f"parsing {len(files)} XML files ...")

    rows, errors = [], []
    for p in files:
        try:
            r = parse_report(p)
            rows.append([r[f] for f in FIELDS] + [r["projectID"]])
        except ET.ParseError as e:
            errors.append(f"{p.name}: {e}")
    if errors:
        print(f"{len(errors)} files failed to parse:", *errors[:5], sep="\n  ")

    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE OR REPLACE TABLE report_text (
            rcn BIGINT, id VARCHAR, title VARCHAR, teaser VARCHAR,
            summary VARCHAR, workPerformed VARCHAR, finalResults VARCHAR,
            periodNumber INTEGER, periodFrom DATE, periodTo DATE,
            lastUpdateDate TIMESTAMP, projectID BIGINT)
    """)
    con.executemany(
        f"INSERT INTO report_text VALUES ({', '.join('?' * 12)})", rows)

    q = lambda sql: con.execute(sql).fetchone()[0]
    checks = [
        ("all XML files parsed",
         f"{len(rows)} of {len(files)}", not errors),
        ("one report per CSV report_summary row (join on rcn)",
         f"{q('SELECT count(*) FROM report_text t JOIN report_summary s USING (rcn)')} "
         f"of {q('SELECT count(*) FROM report_summary')}",
         q("SELECT count(*) FROM report_summary WHERE rcn NOT IN "
           "(SELECT rcn FROM report_text)") == 0),
        ("projectID all resolve to project.id",
         f"{q('SELECT count(*) FROM report_text WHERE projectID NOT IN (SELECT id FROM project)')} orphans",
         q("SELECT count(*) FROM report_text WHERE projectID NOT IN "
           "(SELECT id FROM project)") == 0),
        ("projectID consistent with id prefix (e.g. 851890_PS)",
         "mismatches: " + str(q(
             "SELECT count(*) FROM report_text "
             "WHERE CAST(projectID AS VARCHAR) <> split_part(id, '_', 1)")),
         q("SELECT count(*) FROM report_text "
           "WHERE CAST(projectID AS VARCHAR) <> split_part(id, '_', 1)") == 0),
        ("summary text present",
         f"{q('SELECT count(*) FROM report_text WHERE length(summary) > 100')} "
         "reports with >100 chars of summary",
         q("SELECT count(*) FROM report_text WHERE summary IS NULL "
           "OR length(summary) <= 100") < len(rows) * 0.02),
    ]

    lines = ["## Report text (XML)", "",
             f"Parsed from `data/raw/reports/` ({len(files)} files) into "
             "table `report_text` (rcn, id, title, teaser, summary, "
             "workPerformed, finalResults, period, projectID).", "",
             "| check | observed | result |", "|---|---|---|"]
    failures = 0
    for name, observed, ok in checks:
        lines.append(f"| {name} | {observed} | {'PASS' if ok else 'FAIL'} |")
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {observed}")
    con.close()

    text = REPORT_PATH.read_text(encoding="utf-8")
    for marker in ("\n## Report text (XML)", "\n## Text profile"):
        if marker in text:  # drop stale section(s); profile.py re-adds its own
            text = text[:text.index(marker)]
    REPORT_PATH.write_text(text.rstrip() + "\n\n" + "\n".join(lines) + "\n",
                           encoding="utf-8")
    print(f"\nUpdated {REPORT_PATH} - now rerun: python -m src.ingest.profile")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
