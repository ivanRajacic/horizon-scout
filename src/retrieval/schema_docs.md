# Horizon 2020 CORDIS database (DuckDB)

Field meanings follow the CORDIS DET codebook (a generic spec with Horizon-Europe examples); all values are verified against this H2020 extraction. All money columns are EUR, DECIMAL(18,2).
Join key everywhere: `<table>.projectID = project.id`. `rcn` and `contentUpdateDate` columns are CORDIS bookkeeping - ignore.

## project - one row per H2020 project (grant). 35,389 rows. PK: id.
- id BIGINT - grant agreement number, unique.
- acronym, title, objective VARCHAR - acronym, title, abstract. Never null.
- status VARCHAR - 'CLOSED' | 'SIGNED' (= ongoing) | 'TERMINATED'.
- startDate, endDate DATE - action start/end. 12 nulls each; startDate 2014-01-01..2023-09-01, endDate up to 2029-06-30.
- ecSignatureDate DATE - EC grant signature date. Never null.
- ecMaxContribution - committed EU funding for the project. USE THIS for "EU funding" questions. Per project it ~equals SUM(organization.ecContribution) (differs >1% in 109 projects). Never SUM it across a join to organization (it repeats per org row).
- totalCost - total project cost = EU funding + participants' own resources. NOT "EU funding".
- fundingScheme VARCHAR - type of action. 56 values; top: MSCA-IF, RIA, SME-1, MSCA-IF-EF-ST, CSA, ERC-STG, ERC-COG, IA, ERC-ADG, SME-2, ERC-POC.
- frameworkProgramme VARCHAR - always 'H2020'.
- legalBasis VARCHAR - main specific programme code (e.g. 'H2020-EU.1.3.'); names in legal_basis table.
- topics VARCHAR - call topic code; equals topics.topic for the same project.
- masterCall, subCall VARCHAR - call names.
- nature VARCHAR - null in 34,814 rows; else crisisPreparedness|crisisRecovery|crisisResponse (values not in codebook).
- grantDoi VARCHAR - project DOI, never null. keywords VARCHAR - 17,400 nulls.
- humanValidated VARCHAR - text 'false'|'NA'|'true' (not in codebook); avoid.

## organization - one row per project-organization participation. 178,932 rows.
- projectID BIGINT -> project.id (N:1, no orphans; every project has >=1 row).
- organisationID VARCHAR - PIC, numeric string, stable across projects. name, shortName VARCHAR (name never null). vatNumber VARCHAR.
- role VARCHAR - participant | coordinator | thirdParty (funded via a main participant) | partner (MSCA, unfunded) | internationalPartner. Codebook also lists associatedPartner (zero rows here). Exactly one coordinator per project (1 project has none).
- ecContribution - EU money to this org in this project. USE for per-org/per-country funding sums. NULL for all 7,658 'partner' rows + 142 others.
- netEcContribution - ecContribution net of transfers to linked third parties (7 nulls; differs in 18,284 rows). Prefer ecContribution unless "net" is asked.
- totalCost - this org's own total cost in the project (not the project total). 178 nulls.
- sme BOOLEAN - self-declared SME. 518 nulls.
- activityType VARCHAR - HES (higher education) | PRC (private for-profit) | REC (research org) | PUB (public body) | OTH. 798 nulls.
- country VARCHAR - ISO 3166-1 alpha-2 (178 values). city, street, postCode, nutsCode, geolocation ("lat,lon" string), organizationURL, contactForm VARCHAR - location/contact.
- participantOrder INTEGER - listing order; coordinator is always 1. 14 nulls.
- endOfParticipation BOOLEAN - participation ended early. active BOOLEAN - null except 1,386 false rows (FPAs only); avoid.

## topics - one row per project (1:1, 35,389 rows). projectID, topic (code, = project.topics), title (topic name).

## legal_basis - programme parts funding a project (N per project, 65,807 rows).
projectID; legalBasis (code); title (programme name); uniqueProgrammePart BOOLEAN - true marks the main programme, exactly one true row per project (1 project has 3); null otherwise (never false).

## euroscivoc - science-vocabulary classification. 111,614 rows; 1-5 rows per project; covers 32,236 of 35,389 projects.
projectID; euroSciVocCode (e.g. '/25/61/383'); euroSciVocPath (hierarchy '/engineering and technology/.../biofuels'); euroSciVocTitle (leaf term); euroSciVocDescription - always null.

## report_summary + report_text - published result summaries. 34,712 rows each; 1:1 with each other (same id set) and per project; ~98% of projects have one.
- report_summary: id VARCHAR (PK), title, projectID, projectAcronym, attachment (CORDIS doc URIs).
- report_text (not in codebook; described from data): id/rcn/title/projectID as above; teaser (19 nulls); summary (never null); workPerformed (1,119 nulls); finalResults (1,128 nulls); periodNumber INTEGER (1-9), periodFrom, periodTo DATE - reporting period (never null).

## web_link - project links. 232,267 rows; mostly type='projectDeliverable' (194,750) or 'relatedWebsite'; also relatedStory, socialMedia, relatedVideo, relatedNews, publicationRepository. physUrl, status (null unless archived: legacy|webArchive|invalid|valid), represents (mostly 'project' or null; else social-network name), availableLanguages, archivedDate, id, source, projectID.

## web_item - 10 rows of project logos/images (language, availableLanguages, uri, title, type, source, represents, projectID).

## chunk - internal text-chunk store for vector search (11,684 rows). Not for SQL answers.

## Examples
-- How many projects are still ongoing?
SELECT COUNT(*) FROM project WHERE status = 'SIGNED';  -- 2964
-- Which German organisations coordinate the most projects?
SELECT o.name, COUNT(*) AS n FROM organization o WHERE o.role = 'coordinator' AND o.country = 'DE' GROUP BY o.name ORDER BY n DESC LIMIT 3;  -- MAX-PLANCK... 316
-- Total EU funding for ERC Starting Grants?
SELECT SUM(ecMaxContribution) FROM project WHERE fundingScheme = 'ERC-STG';  -- 4150115522.61
-- How many projects started in 2020, and their average EU funding?
SELECT COUNT(*), AVG(ecMaxContribution) FROM project WHERE startDate BETWEEN DATE '2020-01-01' AND DATE '2020-12-31';  -- 4506 | 2493409.90
-- How many SME participations come from the Netherlands?
SELECT COUNT(*) FROM organization WHERE sme AND country = 'NL';  -- 2387
