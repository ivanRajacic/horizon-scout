"""Re-verify every data claim in src/retrieval/schema_docs.md against the DuckDB.

Run after any data reload:  python scripts/verify_schema_docs.py
Exit code 0 = docs still match reality; nonzero = a claim drifted and the
docs (and the expected values below) must be updated.
"""

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "horizon.duckdb"
DOCS_PATH = ROOT / "src" / "retrieval" / "schema_docs.md"
TOKEN_BUDGET = 1500  # heuristic: chars / 4

failures = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {actual!r}" + ("" if ok else f" (expected {expected!r})"))
    if not ok:
        failures.append(name)


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    q1 = lambda sql: con.execute(sql).fetchone()[0]
    col = lambda sql: sorted(r[0] for r in con.execute(sql).fetchall())

    # --- tables and row counts ---
    check("tables", col("SHOW TABLES"),
          sorted(["chunk", "euroscivoc", "legal_basis", "organization", "project",
                  "report_summary", "report_text", "topics", "web_item", "web_link"]))
    for table, rows in [("project", 35389), ("organization", 178932), ("topics", 35389),
                        ("legal_basis", 65807), ("euroscivoc", 111614), ("report_summary", 34712),
                        ("report_text", 34712), ("web_link", 232267), ("web_item", 10),
                        ("chunk", 11684)]:
        check(f"rowcount {table}", q1(f"SELECT COUNT(*) FROM {table}"), rows)

    # --- every documented column exists, nothing undocumented (names only) ---
    expected_cols = {
        "project": {"id", "acronym", "status", "title", "startDate", "endDate", "totalCost",
                    "ecMaxContribution", "topics", "ecSignatureDate", "frameworkProgramme",
                    "masterCall", "subCall", "fundingScheme", "nature", "objective",
                    "contentUpdateDate", "rcn", "grantDoi", "keywords", "humanValidated",
                    "legalBasis"},
        "organization": {"projectID", "projectAcronym", "organisationID", "vatNumber", "name",
                         "shortName", "sme", "activityType", "street", "postCode", "city",
                         "country", "nutsCode", "geolocation", "organizationURL", "contactForm",
                         "contentUpdateDate", "rcn", "participantOrder", "role", "ecContribution",
                         "netEcContribution", "totalCost", "endOfParticipation", "active"},
        "topics": {"projectID", "topic", "title"},
        "legal_basis": {"projectID", "legalBasis", "title", "uniqueProgrammePart"},
        "euroscivoc": {"projectID", "euroSciVocCode", "euroSciVocPath", "euroSciVocTitle",
                       "euroSciVocDescription"},
        "report_summary": {"id", "title", "projectID", "projectAcronym", "attachment",
                           "contentUpdateDate", "rcn"},
        "report_text": {"rcn", "id", "title", "teaser", "summary", "workPerformed",
                        "finalResults", "periodNumber", "periodFrom", "periodTo",
                        "lastUpdateDate", "projectID"},
        "web_link": {"physUrl", "id", "availableLanguages", "status", "archivedDate", "type",
                     "source", "represents", "projectID"},
        "web_item": {"language", "availableLanguages", "uri", "title", "type", "source",
                     "represents", "projectID"},
        "chunk": {"chunk_id", "project_id", "source", "section", "n_tokens", "text"},
    }
    for table, cols in expected_cols.items():
        check(f"columns {table}", set(col(f"SELECT column_name FROM (DESCRIBE {table})")), cols)

    # --- enumerated values (must match SELECT DISTINCT exactly) ---
    check("project.status", col("SELECT DISTINCT status FROM project"),
          ["CLOSED", "SIGNED", "TERMINATED"])
    check("project.frameworkProgramme", col("SELECT DISTINCT frameworkProgramme FROM project"),
          ["H2020"])
    check("project.nature (non-null)",
          col("SELECT DISTINCT nature FROM project WHERE nature IS NOT NULL"),
          ["crisisPreparedness", "crisisRecovery", "crisisResponse"])
    check("project.humanValidated", col("SELECT DISTINCT humanValidated FROM project"),
          ["NA", "false", "true"])
    check("project.fundingScheme count", q1("SELECT COUNT(DISTINCT fundingScheme) FROM project"), 56)
    check("organization.role", col("SELECT DISTINCT role FROM organization"),
          ["coordinator", "internationalPartner", "participant", "partner", "thirdParty"])
    check("organization.activityType (non-null)",
          col("SELECT DISTINCT activityType FROM organization WHERE activityType IS NOT NULL"),
          ["HES", "OTH", "PRC", "PUB", "REC"])
    check("organization.country count", q1("SELECT COUNT(DISTINCT country) FROM organization"), 178)
    check("web_link.type", col("SELECT DISTINCT type FROM web_link"),
          ["projectDeliverable", "publicationRepository", "relatedNews", "relatedStory",
           "relatedVideo", "relatedWebsite", "socialMedia"])
    check("web_link.status (non-null)",
          col("SELECT DISTINCT status FROM web_link WHERE status IS NOT NULL"),
          ["invalid", "legacy", "valid", "webArchive"])
    check("legal_basis.uniqueProgrammePart values (never false)",
          q1("SELECT COUNT(*) FROM legal_basis WHERE uniqueProgrammePart = false"), 0)

    # --- keys, cardinality, 1:1 claims ---
    check("project.id unique", q1("SELECT COUNT(*) - COUNT(DISTINCT id) FROM project"), 0)
    check("organization orphans",
          q1("SELECT COUNT(*) FROM organization o LEFT JOIN project p ON o.projectID = p.id"
             " WHERE p.id IS NULL"), 0)
    check("every project has >=1 organization",
          q1("SELECT COUNT(DISTINCT projectID) FROM organization"), 35389)
    check("projects with exactly one coordinator",
          q1("SELECT COUNT(*) FROM (SELECT projectID FROM organization"
             " WHERE role = 'coordinator' GROUP BY 1 HAVING COUNT(*) = 1)"), 35388)
    check("projects with no coordinator",
          q1("SELECT COUNT(*) FROM project p WHERE NOT EXISTS (SELECT 1 FROM organization o"
             " WHERE o.projectID = p.id AND o.role = 'coordinator')"), 1)
    check("coordinator participantOrder is 1",
          q1("SELECT COUNT(*) FROM organization WHERE role = 'coordinator'"
             " AND (participantOrder IS NULL OR participantOrder <> 1)"), 0)
    check("topics 1:1 with project",
          q1("SELECT COUNT(*) - COUNT(DISTINCT projectID) FROM topics"), 0)
    check("project.topics equals topics.topic",
          q1("SELECT COUNT(*) FROM project p JOIN topics t ON t.projectID = p.id"
             " WHERE p.topics IS DISTINCT FROM t.topic"), 0)
    check("report_text 1 row per project",
          q1("SELECT COUNT(*) - COUNT(DISTINCT projectID) FROM report_text"), 0)
    check("report_summary/report_text same id set",
          q1("SELECT COUNT(*) FROM report_summary s FULL JOIN report_text t ON s.id = t.id"
             " WHERE s.id IS NULL OR t.id IS NULL"), 0)
    check("projects with >1 uniqueProgrammePart=true rows",
          q1("SELECT COUNT(*) FROM (SELECT projectID FROM legal_basis WHERE uniqueProgrammePart"
             " GROUP BY 1 HAVING COUNT(*) > 1)"), 1)
    check("euroscivoc rows per project within 1..5",
          q1("SELECT COUNT(*) FROM (SELECT projectID, COUNT(*) n FROM euroscivoc GROUP BY 1)"
             " WHERE n < 1 OR n > 5"), 0)
    check("euroscivoc project coverage",
          q1("SELECT COUNT(DISTINCT projectID) FROM euroscivoc"), 32236)
    check("chunk.source values", col("SELECT DISTINCT source FROM chunk"),
          ["objective", "report"])

    # --- money relationships ---
    check("projects where SUM(org.ecContribution) differs >1% from ecMaxContribution",
          q1("SELECT COUNT(*) FROM (SELECT p.id, p.ecMaxContribution m, SUM(o.ecContribution) s"
             " FROM project p JOIN organization o ON o.projectID = p.id GROUP BY 1, 2)"
             " WHERE s IS NOT NULL AND m > 0 AND ABS(s - m) / m > 0.01"), 109)
    check("partner rows all have NULL ecContribution",
          q1("SELECT COUNT(*) FROM organization WHERE role = 'partner'"
             " AND ecContribution IS NOT NULL"), 0)
    check("rows where netEcContribution differs from ecContribution",
          q1("SELECT COUNT(*) FROM organization WHERE ecContribution IS NOT NULL"
             " AND netEcContribution IS NOT NULL AND ecContribution <> netEcContribution"), 18284)

    # --- null counts ---
    for name, sql, expected in [
        ("project.totalCost nulls", "SELECT COUNT(*) - COUNT(totalCost) FROM project", 0),
        ("project.ecMaxContribution nulls", "SELECT COUNT(*) - COUNT(ecMaxContribution) FROM project", 0),
        ("project.startDate nulls", "SELECT COUNT(*) - COUNT(startDate) FROM project", 12),
        ("project.endDate nulls", "SELECT COUNT(*) - COUNT(endDate) FROM project", 12),
        ("project.ecSignatureDate nulls", "SELECT COUNT(*) - COUNT(ecSignatureDate) FROM project", 0),
        ("project.objective nulls", "SELECT COUNT(*) - COUNT(objective) FROM project", 0),
        ("project.grantDoi nulls", "SELECT COUNT(*) - COUNT(grantDoi) FROM project", 0),
        ("project.keywords nulls", "SELECT COUNT(*) - COUNT(keywords) FROM project", 17400),
        ("project.nature nulls", "SELECT COUNT(*) - COUNT(nature) FROM project", 34814),
        ("organization.name nulls", "SELECT COUNT(*) - COUNT(name) FROM organization", 0),
        ("organization.ecContribution nulls", "SELECT COUNT(*) - COUNT(ecContribution) FROM organization", 7800),
        ("organization.netEcContribution nulls", "SELECT COUNT(*) - COUNT(netEcContribution) FROM organization", 7),
        ("organization.totalCost nulls", "SELECT COUNT(*) - COUNT(totalCost) FROM organization", 178),
        ("organization.sme nulls", "SELECT COUNT(*) - COUNT(sme) FROM organization", 518),
        ("organization.activityType nulls", "SELECT COUNT(*) - COUNT(activityType) FROM organization", 798),
        ("organization.participantOrder nulls", "SELECT COUNT(*) - COUNT(participantOrder) FROM organization", 14),
        ("organization.active non-null (all false)", "SELECT COUNT(active) FROM organization", 1386),
        ("euroscivoc.euroSciVocDescription non-null", "SELECT COUNT(euroSciVocDescription) FROM euroscivoc", 0),
        ("report_text.teaser nulls", "SELECT COUNT(*) - COUNT(teaser) FROM report_text", 19),
        ("report_text.summary nulls", "SELECT COUNT(*) - COUNT(summary) FROM report_text", 0),
        ("report_text.workPerformed nulls", "SELECT COUNT(*) - COUNT(workPerformed) FROM report_text", 1119),
        ("report_text.finalResults nulls", "SELECT COUNT(*) - COUNT(finalResults) FROM report_text", 1128),
        ("report_text.periodFrom nulls", "SELECT COUNT(*) - COUNT(periodFrom) FROM report_text", 0),
        ("report_text.periodTo nulls", "SELECT COUNT(*) - COUNT(periodTo) FROM report_text", 0),
    ]:
        check(name, q1(sql), expected)

    # --- date ranges and misc ---
    check("startDate min/max",
          con.execute("SELECT CAST(MIN(startDate) AS VARCHAR), CAST(MAX(startDate) AS VARCHAR)"
                      " FROM project").fetchone(), ("2014-01-01", "2023-09-01"))
    check("endDate max",
          q1("SELECT CAST(MAX(endDate) AS VARCHAR) FROM project"), "2029-06-30")
    check("periodNumber range",
          con.execute("SELECT MIN(periodNumber), MAX(periodNumber) FROM report_text").fetchone(),
          (1, 9))
    check("organisationID all-numeric strings",
          q1("SELECT COUNT(*) FROM organization WHERE organisationID IS NOT NULL"
             " AND NOT regexp_matches(organisationID, '^[0-9]+$')"), 0)

    # --- few-shot examples from schema_docs.md ---
    check("example 1: ongoing projects",
          q1("SELECT COUNT(*) FROM project WHERE status = 'SIGNED'"), 2964)
    check("example 2: top DE coordinators",
          con.execute("SELECT o.name, COUNT(*) AS n FROM organization o"
                      " WHERE o.role = 'coordinator' AND o.country = 'DE'"
                      " GROUP BY o.name ORDER BY n DESC LIMIT 1").fetchone(),
          ("MAX-PLANCK-GESELLSCHAFT ZUR FORDERUNG DER WISSENSCHAFTEN EV", 316))
    check("example 3: ERC-STG total EU funding",
          str(q1("SELECT SUM(ecMaxContribution) FROM project WHERE fundingScheme = 'ERC-STG'")),
          "4150115522.61")
    row = con.execute("SELECT COUNT(*), ROUND(AVG(ecMaxContribution), 2) FROM project"
                      " WHERE startDate BETWEEN DATE '2020-01-01' AND DATE '2020-12-31'").fetchone()
    check("example 4: 2020 starts count/avg", (row[0], str(row[1])), (4506, "2493409.9"))
    check("example 5: NL SME participations",
          q1("SELECT COUNT(*) FROM organization WHERE sme AND country = 'NL'"), 2387)

    # --- size budget ---
    text = DOCS_PATH.read_text(encoding="utf-8")
    est_tokens = len(text) // 4
    print(f"INFO  schema_docs.md size: {len(text)} chars, ~{est_tokens} tokens"
          f" (budget {TOKEN_BUDGET}, heuristic chars/4)")
    check("token budget", est_tokens <= TOKEN_BUDGET, True)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
