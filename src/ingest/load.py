"""Load CORDIS H2020 CSV dumps into DuckDB and verify them against the codebook.

Usage:  python -m src.ingest.load  (from the repo root)

Produces data/processed/horizon.duckdb and data/processed/ingest_report.md.
"""

import csv
import sys
from pathlib import Path

import duckdb

from .cordis_csv import read_rows

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = PROCESSED / "horizon.duckdb"
REPORT_PATH = PROCESSED / "ingest_report.md"

# Money columns use comma as the decimal separator ("3499076,25").
MONEY = "TRY_CAST(replace(NULLIF({c}, ''), ',', '.') AS DECIMAL(18,2))"
DATE = "TRY_CAST(NULLIF({c}, '') AS DATE)"
TS = "TRY_CAST(NULLIF({c}, '') AS TIMESTAMP)"
BIGINT = "TRY_CAST(NULLIF({c}, '') AS BIGINT)"
INT = "TRY_CAST(NULLIF({c}, '') AS INTEGER)"
BOOL = "TRY_CAST(NULLIF({c}, '') AS BOOLEAN)"
TEXT = "NULLIF({c}, '')"

# table name -> (source file, {source column -> (target column, cast template)})
# Source column names come from the DET codebook (DET_fields_description.pdf)
# and were verified against the actual file headers.
TABLES = {
    "project": ("project.csv", {
        "id": ("id", BIGINT),
        "acronym": ("acronym", TEXT),
        "status": ("status", TEXT),
        "title": ("title", TEXT),
        "startDate": ("startDate", DATE),
        "endDate": ("endDate", DATE),
        "totalCost": ("totalCost", MONEY),
        "ecMaxContribution": ("ecMaxContribution", MONEY),
        "topics": ("topics", TEXT),
        "ecSignatureDate": ("ecSignatureDate", DATE),
        "frameworkProgramme": ("frameworkProgramme", TEXT),
        "masterCall": ("masterCall", TEXT),
        "subCall": ("subCall", TEXT),
        "fundingScheme": ("fundingScheme", TEXT),
        "nature": ("nature", TEXT),
        "objective": ("objective", TEXT),
        "contentUpdateDate": ("contentUpdateDate", TS),
        "rcn": ("rcn", BIGINT),
        "grantDoi": ("grantDoi", TEXT),
        "keywords": ("keywords", TEXT),
        "Human-validated": ("humanValidated", TEXT),  # not in codebook
        "legalBasis": ("legalBasis", TEXT),
    }),
    "organization": ("organization.csv", {
        "projectID": ("projectID", BIGINT),
        "projectAcronym": ("projectAcronym", TEXT),
        "organisationID": ("organisationID", TEXT),
        "vatNumber": ("vatNumber", TEXT),
        "name": ("name", TEXT),
        "shortName": ("shortName", TEXT),
        "SME": ("sme", BOOL),
        "activityType": ("activityType", TEXT),
        "street": ("street", TEXT),
        "postCode": ("postCode", TEXT),
        "city": ("city", TEXT),
        "country": ("country", TEXT),
        "nutsCode": ("nutsCode", TEXT),
        "geolocation": ("geolocation", TEXT),
        "organizationURL": ("organizationURL", TEXT),
        "contactForm": ("contactForm", TEXT),
        "contentUpdateDate": ("contentUpdateDate", TS),
        "rcn": ("rcn", BIGINT),
        # renamed from "order" (reserved word) to keep generated SQL simple
        "order": ("participantOrder", INT),
        "role": ("role", TEXT),
        "ecContribution": ("ecContribution", MONEY),
        "netEcContribution": ("netEcContribution", MONEY),
        "totalCost": ("totalCost", MONEY),
        "endOfParticipation": ("endOfParticipation", BOOL),
        "active": ("active", BOOL),
    }),
    "euroscivoc": ("euroSciVoc.csv", {
        "projectID": ("projectID", BIGINT),
        "euroSciVocCode": ("euroSciVocCode", TEXT),
        "euroSciVocPath": ("euroSciVocPath", TEXT),
        "euroSciVocTitle": ("euroSciVocTitle", TEXT),
        "euroSciVocDescription": ("euroSciVocDescription", TEXT),
    }),
    "legal_basis": ("legalBasis.csv", {
        "projectID": ("projectID", BIGINT),
        "legalBasis": ("legalBasis", TEXT),
        "title": ("title", TEXT),
        "uniqueProgrammePart": ("uniqueProgrammePart", BOOL),
    }),
    "topics": ("topics.csv", {
        "projectID": ("projectID", BIGINT),
        "topic": ("topic", TEXT),
        "title": ("title", TEXT),
    }),
    "report_summary": ("reportSummaries.csv", {
        "id": ("id", TEXT),
        "title": ("title", TEXT),
        "projectID": ("projectID", BIGINT),
        "projectAcronym": ("projectAcronym", TEXT),
        "attachment": ("attachment", TEXT),
        "contentUpdateDate": ("contentUpdateDate", TS),
        "rcn": ("rcn", BIGINT),
    }),
    "web_item": ("webItem.csv", {
        "language": ("language", TEXT),
        "availableLanguages": ("availableLanguages", TEXT),
        "uri": ("uri", TEXT),
        "title": ("title", TEXT),
        "type": ("type", TEXT),
        "source": ("source", TEXT),
        "represents": ("represents", TEXT),
        "projectID": ("projectID", BIGINT),
    }),
    "web_link": ("webLink.csv", {
        "physUrl": ("physUrl", TEXT),
        "id": ("id", TEXT),
        "availableLanguages": ("availableLanguages", TEXT),
        "status": ("status", TEXT),
        "archivedDate": ("archivedDate", TS),
        "type": ("type", TEXT),
        "source": ("source", TEXT),
        "represents": ("represents", TEXT),
        "projectID": ("projectID", BIGINT),
    }),
}


def stage_csv(src: Path, dst: Path) -> int:
    """Re-emit a CORDIS dump as a standard RFC-4180 CSV; return record count."""
    n = -1  # do not count the header
    with open(dst, "w", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        for row in read_rows(src):
            w.writerow(row)
            n += 1
    return n


def load_table(con, name: str, src_file: str, colmap: dict, staging_dir: Path):
    src = RAW / src_file
    staged = staging_dir / f"{name}.csv"
    n_raw = stage_csv(src, staged)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE stg AS
        SELECT * FROM read_csv('{staged.as_posix()}', header=true,
                               all_varchar=true, max_line_size=20000000)
    """)
    staged_cols = [c[0] for c in con.execute("DESCRIBE stg").fetchall()]
    expected = list(colmap.keys())
    if sorted(staged_cols) != sorted(expected):
        raise SystemExit(
            f"{src_file}: columns differ from codebook mapping.\n"
            f"  file:     {staged_cols}\n  expected: {expected}"
        )

    selects = ", ".join(
        tmpl.format(c=f'"{src_col}"') + f' AS "{tgt}"'
        for src_col, (tgt, tmpl) in colmap.items()
    )
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT {selects} FROM stg")
    n_loaded = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]

    # Audit lossy casts: raw value present but typed value NULL.
    cast_failures = []
    for src_col, (tgt, tmpl) in colmap.items():
        if tmpl == TEXT:
            continue
        n_bad = con.execute(
            f"SELECT count(*) FROM stg WHERE NULLIF(\"{src_col}\", '') IS NOT NULL "
            f"AND {tmpl.format(c=chr(34) + src_col + chr(34))} IS NULL"
        ).fetchone()[0]
        if n_bad:
            cast_failures.append((tgt, n_bad))
    staged.unlink()
    return n_raw, n_loaded, cast_failures


def run_checks(con) -> list[tuple[str, str, bool]]:
    """Return (check name, observed value, ok) tuples."""
    q = lambda sql: con.execute(sql).fetchone()[0]
    checks = []

    dup = q("SELECT count(*) - count(DISTINCT id) FROM project")
    checks.append(("project.id is unique", str(dup) + " duplicates", dup == 0))

    fp = con.execute(
        "SELECT DISTINCT frameworkProgramme FROM project").fetchall()
    fps = sorted(v[0] for v in fp)
    checks.append(("frameworkProgramme is H2020 only", str(fps), fps == ["H2020"]))

    status = con.execute(
        "SELECT status, count(*) FROM project GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    known = {"SIGNED", "CLOSED", "TERMINATED"}
    checks.append((
        "status codes match codebook (ongoing/closed/terminated)",
        str(status), {s for s, _ in status} <= known,
    ))

    lo, hi = con.execute(
        "SELECT min(startDate), max(startDate) FROM project").fetchone()
    checks.append((
        "startDate range plausible for H2020 (2014-2020 calls)",
        f"{lo} .. {hi}", str(lo) >= "2013-01-01" and str(hi) <= "2024-12-31",
    ))

    for child, fk in [("organization", "projectID"), ("euroscivoc", "projectID"),
                      ("legal_basis", "projectID"), ("topics", "projectID"),
                      ("report_summary", "projectID")]:
        orphans = q(f"SELECT count(*) FROM {child} WHERE {fk} NOT IN "
                    f"(SELECT id FROM project)")
        checks.append((f"{child}.{fk} all resolve to project.id",
                       f"{orphans} orphans", orphans == 0))

    multi = q("""
        SELECT count(*) FROM (
          SELECT projectID FROM organization WHERE role = 'coordinator'
          GROUP BY projectID HAVING count(*) <> 1)
    """)
    no_coord = q("""
        SELECT count(*) FROM project WHERE id NOT IN
          (SELECT projectID FROM organization WHERE role = 'coordinator')
    """)
    checks.append((
        "exactly one coordinator per project (known gap: 101036871 OLGA "
        "ships only thirdParty rows)",
        f"{multi} projects with !=1 coordinator, {no_coord} projects with "
        "none", multi == 0 and no_coord <= 1))

    zero_cost = q("""
        SELECT count(*) FROM project
        WHERE totalCost = 0 AND ecMaxContribution > 0
    """)
    over = q("""
        SELECT count(*) FROM project
        WHERE totalCost > 0 AND ecMaxContribution > totalCost + 0.01
    """)
    checks.append((
        "ecMaxContribution <= totalCost where totalCost is populated "
        "(totalCost=0 means 'not declared', mostly lump-sum ERC-POC/CSA)",
        f"{over} violations, {zero_cost} projects with totalCost=0",
        over == 0))

    med = q("""
        SELECT median(abs(s - p.ecMaxContribution) / nullif(p.ecMaxContribution, 0))
        FROM (SELECT projectID, sum(ecContribution) AS s FROM organization
              GROUP BY projectID) o
        JOIN project p ON p.id = o.projectID
    """)
    checks.append(("sum(org.ecContribution) ~= project.ecMaxContribution "
                   "(codebook: grant amount)",
                   f"median relative diff = {med:.4f}", med is not None and med < 0.05))

    cov = q("""
        SELECT count(DISTINCT projectID) FROM report_summary
    """)
    total = q("SELECT count(*) FROM project")
    checks.append(("report summaries cover most projects",
                   f"{cov} of {total} projects", cov / total > 0.8))
    return checks


def main():
    PROCESSED.mkdir(parents=True, exist_ok=True)
    staging = PROCESSED / "_staging"
    staging.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = duckdb.connect(str(DB_PATH))

    lines = ["# Ingest report", "", "## Tables loaded", "",
             "| table | source file | rows (file) | rows (loaded) | cast failures |",
             "|---|---|---:|---:|---|"]
    for name, (src_file, colmap) in TABLES.items():
        n_raw, n_loaded, cast_failures = load_table(con, name, src_file,
                                                    colmap, staging)
        cf = ", ".join(f"{c}: {n}" for c, n in cast_failures) or "none"
        ok = "OK" if n_raw == n_loaded else "MISMATCH"
        lines.append(f"| {name} | {src_file} | {n_raw} | {n_loaded} ({ok}) | {cf} |")
        print(f"{name}: {n_loaded} rows ({ok}), cast failures: {cf}")

    lines += ["", "## Verification against codebook", "",
              "| check | observed | result |", "|---|---|---|"]
    failures = 0
    for check_name, observed, ok in run_checks(con):
        lines.append(f"| {check_name} | {observed} | "
                     f"{'PASS' if ok else 'FAIL'} |")
        if not ok:
            failures += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {check_name}: {observed}")

    lines += ["", "## Schema notes", "",
              "- All money columns are DECIMAL(18,2) in EUR; source used comma "
              "as decimal separator.",
              "- `organization.order` renamed to `participantOrder` "
              "(reserved word).",
              "- `project.\"Human-validated\"` (undocumented in codebook) "
              "loaded as `humanValidated`.",
              "- **`report_summary` contains NO summary text** - the current "
              "DET export only ships metadata (title, attachment URIs). The "
              "chunking corpus for the vector index must come from another "
              "source; `project.objective` is the interim text field.", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    staging.rmdir()
    con.close()
    print(f"\nDB: {DB_PATH}\nReport: {REPORT_PATH}\nFailed checks: {failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
