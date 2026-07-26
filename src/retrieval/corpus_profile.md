# Horizon Scout corpus profile

## Header

- **Version:** cp5
- **Generated:** 2026-07-26
- **Corpus fingerprint:** 35,389 projects (`SELECT COUNT(*) FROM project`). Dense index `data/processed/index_meta.json`: 190,248 vectors, embedder `bge-base-en-v1.5-f16.gguf`, dim 768, built 2026-07-22T08:53:52Z. euroscivoc classification covers 32,236 of 35,389 projects across 111,614 rows (`SELECT COUNT(DISTINCT projectID), COUNT(*) FROM euroscivoc`).
- **Grounded against schema_docs:** version `sd2`, content_hash `e2696e0f80f5`.

**Run log** (scope, cost, frontier movement - one line per run):

- cp1 (2026-07-23) scope `"find 15 vector topics"`: Vector only, 15 candidates. 2 subagents.
- cp2 (2026-07-23) scope `"pilot hybrid 10"`: Hybrid only, 10 candidates (10 found). 2 subagents, 54 `run_sql`, 6 `get_project_text` calls (~15 projects). Frontier not yet in existence.
- cp3 (2026-07-24) scope `"structural: add the frontier"`: no exploration subagents. Introduced `## Frontier`, `## Corpus map` and `## Structural findings`, built the 46-bucket frontier from the data and back-filled `seeds`/`bank` from the existing candidates and `eval/bank.jsonl`. Frontier established at `mapped 0/46 | mined 18/46`.
- cp4 (2026-07-26) scope `"map=6"`: 12m wall (32s in MCP calls), 3 subagents (2 explorers + 1 critic) over 6 slices, 14 `run_sql`, 18 projects read across 6 `get_project_text` calls; +6 map entries, +18 candidates; frontier `mapped 6/46 | mined 19/46 | unexplored 21/46`.
- cp5 (2026-07-26) scope `"vector=15 (three named mined buckets, L1-weighted)"`: 16m wall (143s in MCP calls), 4 subagents over 3 slices, 39 `run_sql`, 21 projects read across 7 `get_project_text` calls; +3 map entries, +15 candidates; frontier `mapped 4/46 | mined 23/46 | unexplored 19/46`.

**Reading order for a run:** `## Frontier` alone is enough to plan one (it says where we have not been). Read a section's candidates only when you are drafting from them. The whole file is never needed at once.

## Frontier

Where exploration has and has not been. **This is the only section needed to plan a run.**

The denominator is `euroscivoc`, which already partitions the corpus - no taxonomy is invented here. **46 buckets:** 40 named second-level categories (`split_part(euroSciVocPath,'/',2)`, each under exactly one branch), 5 top-level-only paths (one per branch that has depth-1 rows; agricultural sciences has none), and 1 `(unclassified)` bucket for projects with no euroSciVoc row. Verified: `SELECT split_part(euroSciVocPath,'/',1), split_part(euroSciVocPath,'/',2), COUNT(DISTINCT projectID) FROM euroscivoc GROUP BY 1,2` -> 45 rows (40 named + 5 blank), plus `SELECT COUNT(*) FROM project p WHERE NOT EXISTS (SELECT 1 FROM euroscivoc e WHERE e.projectID=p.id)` -> 3,153.

**Caveat, stated because it is real:** a project carries 1-5 euroSciVoc rows, so a project can appear in more than one bucket. This is a cover, not a strict partition, and bucket project-counts therefore sum to more than 35,389. For a coverage checklist that is fine.

**Statuses:** `unexplored` (nobody has been there) -> `mapped` (a `## Corpus map` entry exists - we know what is in there and what it can support) -> `mined` (at least one bank question has been drawn from it). `status`, `seeds` and `bank` are recomputed each run; `map` is carried.

The `bank` column is traced through `gold_project_ids` -> `euroscivoc`, so SQL-route questions with no gold project ids do not appear in it.

| bucket | projects | status | map | seeds | bank |
|---|---|---|---|---|---|
| natural sciences / biological sciences | 8,057 | mined | - | vector-01, vector-12 | hyb-06, hyb-09, vec-01, vec-05 |
| natural sciences / computer and information sciences | 7,654 | mined | m07 | vector-07, vector-34, vector-35, vector-36, vector-37, vector-38 | hyb-07, vec-01 |
| natural sciences / physical sciences | 5,788 | mined | - | hybrid-10 | hyb-03, hyb-07, hyb-08, vec-04, vec-05 |
| engineering and technology / electrical engineering, electronic engineering, information engineering | 5,566 | mined | - | vector-05 | hyb-03, hyb-07, hyb-08, vec-01 |
| engineering and technology / environmental engineering | 5,178 | mined | - | vector-11 | hyb-03, hyb-07, vec-05 |
| social sciences / economics and business | 4,711 | mined | m08 | vector-10, vector-39, vector-40, vector-41, vector-42, vector-43 | hyb-09, vec-05 |
| medical and health sciences / clinical medicine | 4,661 | mined | - | vector-06, vector-13 | vec-02 |
| natural sciences / chemical sciences | 4,331 | mined | m09 | vector-44, vector-45, vector-46, vector-47, vector-48 | hyb-03, hyb-07, vec-05 |
| medical and health sciences / basic medicine | 4,252 | mined | m01 | vector-15, vector-16, vector-17, vector-18 | hyb-06 |
| social sciences / sociology | 3,802 | mined | m02 | vector-19, vector-20, vector-21 | hyb-07 |
| engineering and technology / mechanical engineering | 3,158 | mined | - | vector-03 | hyb-03, vec-05 |
| (unclassified - no euroSciVoc row) | 3,153 | mined | - | - | vec-03 |
| natural sciences / earth and related environmental sciences | 2,922 | mined | - | hybrid-01, hybrid-05, vector-02 | hyb-01, hyb-07, vec-05 |
| medical and health sciences / health sciences | 2,679 | mined | - | - | vec-05 |
| engineering and technology / materials engineering | 2,605 | mined | - | hybrid-09 | hyb-03, hyb-06, hyb-08, vec-05 |
| natural sciences / mathematics | 2,097 | mined | - | vector-09 | vec-04, vec-05 |
| agricultural sciences / agriculture, forestry, and fisheries | 1,943 | mined | - | hybrid-04, hybrid-07 | hyb-09, vec-05 |
| social sciences / political sciences | 1,795 | mapped | m03 | vector-22, vector-23, vector-24 | - |
| humanities / history and archaeology | 1,669 | mapped | m04 | vector-08, vector-25, vector-26, vector-27 | - |
| engineering and technology / nanotechnology | 1,478 | mined | - | hybrid-02, hybrid-06 | hyb-03, hyb-06 |
| medical and health sciences / medical biotechnology | 1,394 | mined | - | - | vec-02 |
| social sciences / social geography | 870 | mapped | m06 | vector-31, vector-32, vector-33 | - |
| social sciences / law | 866 | mapped | m05 | vector-28, vector-29, vector-30 | - |
| engineering and technology / civil engineering | 844 | mined | - | hybrid-03, vector-14 | vec-05 |
| social sciences / psychology | 636 | unexplored | - | - | - |
| engineering and technology / other engineering and technologies | 633 | mined | - | - | vec-05 |
| humanities / philosophy, ethics and religion | 627 | unexplored | - | vector-04 | - |
| engineering and technology / industrial biotechnology | 613 | mined | - | - | hyb-09 |
| humanities / arts | 552 | unexplored | - | hybrid-08 | - |
| humanities / languages and literature | 490 | unexplored | - | - | - |
| engineering and technology / medical engineering | 472 | unexplored | - | - | - |
| social sciences / other social sciences | 417 | unexplored | - | - | - |
| agricultural sciences / animal and dairy science | 402 | unexplored | - | - | - |
| social sciences / educational sciences | 308 | unexplored | - | - | - |
| engineering and technology / chemical engineering | 288 | unexplored | - | - | - |
| engineering and technology / environmental biotechnology | 286 | mined | - | - | hyb-09 |
| social sciences / media and communications | 177 | unexplored | - | - | - |
| humanities / other humanities | 164 | unexplored | - | - | - |
| agricultural sciences / agricultural biotechnology | 104 | unexplored | - | - | - |
| social sciences / (top-level only) | 46 | unexplored | - | - | - |
| medical and health sciences / other medical sciences | 32 | unexplored | - | - | - |
| humanities / (top-level only) | 19 | unexplored | - | - | - |
| agricultural sciences / veterinary sciences | 15 | unexplored | - | - | - |
| natural sciences / (top-level only) | 14 | unexplored | - | - | - |
| medical and health sciences / (top-level only) | 13 | unexplored | - | - | - |
| engineering and technology / (top-level only) | 2 | unexplored | - | - | - |

`mapped 4/46 | mined 23/46 | unexplored 19/46`

No bucket is `mapped` yet: the map is new at cp3 and no region has a `## Corpus map` entry. 18 buckets are `mined` (a bank question was drawn from them, traced through `gold_project_ids`), and a further 4 carry cp1/cp2 candidate seeds with no bank question yet - the `seeds` column keeps that history, but seeds are not a map, so those buckets still read `unexplored`.

## Corpus map

What each explored region of the database actually contains, and what it can support. Written from project text that was READ, never from the taxonomy label alone - cp1 established that euroSciVoc leaf labels lie on interdisciplinary and MSCA projects (`ethnomycology` tagged an aquatic-fungi ecology project; `sustainable architecture` tagged a district-heating project; `agroecology` tagged a green-economy ethnography). A map entry that paraphrases its own tag is worthless.

Append-only: entries are added as buckets are explored, never rewritten. Format:

```
- region: m<NN>
  bucket: <top-level> / <second-level>
  slice: <the SQL predicate that DEFINES this bucket>
  size: <N> projects  (<count query> -> <N>)
  about: <2-3 sentences on the work that actually lives here>
  texture: <report_text coverage; tag echoed verbatim vs paraphrased; tag noisiness; anything that changes how a question must be written>
  read: <the project ids the `about:` was written from - at least 2>
  good for: <which question kinds this region supports - route/level/subtype - and why>
  thin for: <what it cannot support, and why>
  mapped: <cpN>
```

`read:` (added cp4) makes "written from text, not from the tag" checkable rather than promised: `python -m src.cli verify-evidence` confirms those ids exist, carry text and sit in the bucket, and flags an `about:` whose wording is mostly the bucket label back.

- region: m01
  bucket: medical and health sciences / basic medicine
  slice: split_part(euroSciVocPath,'/',1)='medical and health sciences' AND split_part(euroSciVocPath,'/',2)='basic medicine'
  size: 4252 projects
  about: Mechanism- and molecule-level biomedicine rather than clinical care: drug discovery and pharmacology dominate (1,817 projects under pharmacology and pharmacy), followed by neurology (964), immunology (959) and physiology (818). Read members range from engineered-cell cancer immunotherapy running phase I/II trials (CARAMBA, SLAMF7 CAR-T cells for multiple myeloma; EURE-CART, centralised EU CAR-T manufacturing) to venom-toxin pharmacology mining cone-snail insulin mimetics as drug leads (ToxMim). Text is heavy on molecular targets, receptors, cell types and trial phases, with an explicit translational framing (orphan designation, market authorisation, SME partners).
  texture: 4,178 of 4,252 members carry a report row (98.3%). Taxonomy labels are rarely echoed verbatim - no member says the tag words; the theme must be reached through technical vocabulary (chimeric antigen receptor, SLAMF7, toxin mimetic). 3,329 members (78%) also carry a euroSciVoc tag under another top-level branch, mostly natural sciences, so bucket membership is not exclusive and a question must be phrased topically, not by tag.
  read: 754658, 949830, 733297
  good for: Vector route at all three levels: technical phrases like 'chimeric antigen receptor' (12) or 'organ-on-chip' (15) give clean L3 survey sets, rarer phrases like 'venom' (4) give L2. Also good for hybrid filters on funding scheme, since translational IA/RIA and ERC projects coexist here.
  thin for: Single-project L1 seeds - the common technical terms all return double digits (CRISPR alone matches 113 members), so uniqueness has to come from an unusual disease/organism pairing rather than a method word. Also thin for lay-vocabulary questions; the text is uniformly specialist.
  mapped: cp4

- region: m02
  bucket: social sciences / sociology
  slice: split_part(euroSciVocPath,'/',1)='social sciences' AND split_part(euroSciVocPath,'/',2)='sociology'
  size: 3802 projects
  about: Empirical social research on how people are governed, counted, housed and employed: the third level splits into governance (1,251), demography (1,094), industrial relations (619), social issues (588) and anthropology (389). Read members are urban and labour ethnographies - Ethno-gentrification maps middle-class Latinx-led gentrification in Barrio Logan, San Diego; NEIGHBOURCHANGE studies area-based revitalisation programmes in hyperdiverse deprived neighbourhoods across Canada and Italy; MAJORdom compares white working-class paid domestic and care workers in Italy and the USA. Method language (qualitative case study, ethnographic fieldwork, participant observation, mixed methods) and intersectional framing of class, ethnicity and gender recur throughout.
  texture: 3,725 of 3,802 members have a report row (98.0%). Labels are paraphrased, not echoed - members write 'neighbourhood revitalisation' or 'socio-spatial inequality' rather than the tag words. Tag noise is high: 2,940 of 3,802 members also carry a tag under a different top-level branch, and MSCA-IF fellowships bring in projects whose tag reflects one work package only. Many objectives are non-EU field sites (US, Canada, Colombia), so a question assuming a European setting will mis-scope.
  read: 101025665, 799195, 707726
  good for: Vector route across all levels: lay-vocabulary themes give small clean sets ('domestic worker'=1, 'loneliness'=3, 'gentrification'=7), exactly the L1/L2/L3 ladder. Good for term_style=lay questions.
  thin for: Numeric or comparative SQL questions - almost nothing here is quantified in a column - and tag-based filters, because the sociology tag is shared with another branch on three quarters of members.
  mapped: cp4

- region: m03
  bucket: social sciences / political sciences
  slice: split_part(euroSciVocPath,'/',1)='social sciences' AND split_part(euroSciVocPath,'/',2)='political sciences'
  size: 1795 projects
  about: Studies of how political authority is contested, exercised and held to account: political policies (818) and political transitions (682) dominate, with government systems (257), public administration (178) and political communication (44) behind them. Read members span EU interest-group politics (LOBFRAM, lobbying and framing by non-state actors in EU foreign policy on the Israeli-Palestinian conflict), fiscal-transparency infrastructure (DIGIWHIST, tender-level public-procurement data across 35 jurisdictions linked to accountability indicators), and direct-democracy contestation in the global south (VOTEF, community referendums halting extractive projects in Colombia). Multi-level governance, framing, accountability and legitimacy are the recurring analytic vocabulary.
  texture: 1,775 of 1,795 members have a report row (98.9%) - best-covered bucket in this slice. Labels are paraphrased; members write 'multi-level governance' or 'public procurement' rather than the tag words. 1,260 of 1,795 also sit under another top-level branch. Substring traps are real: ILIKE '%coup%' matches 35 members, almost all via 'couple'/'coupling', so lexical evidence needs whole-word or phrase patterns.
  read: 657949, 645852, 838371
  good for: Vector L2/L3 topical sets on politically named concepts - 'lobbying' (5), 'referendum' (6), 'corruption' (9) - exactly the words a lay user would type. Consortium projects like DIGIWHIST also support results-oriented questions from workPerformed.
  thin for: L1 uniqueness - the well-known political concepts all return 5+ members, so single-project seeds need an unusual country/case pairing. Thin for the political-communication third level (only 44 projects).
  mapped: cp4

- region: m04
  bucket: humanities / history and archaeology
  slice: split_part(euroSciVocPath,'/',1)='humanities' AND split_part(euroSciVocPath,'/',2)='history and archaeology'
  size: 1669 projects
  about: Almost entirely ERC and MSCA single-investigator scholarship: period history (ancient 143, medieval 95, modern 129, contemporary 32 tagged projects) plus a smaller science-of-the-past wing (archaeometry 36, bioarchaeology 31, ethnoarchaeology 91). Read members show two recognisable styles - laboratory-driven reconstruction of the past (802349 SILVER combines archaeometric, numismatic and archaeological analysis of ninth-century Viking silver to date the start of the Viking Age; 843337 ModernShip Project compares Mediterranean and Atlantic 16th-century shipbuilding traditions from wreck evidence) and digital/heritage engineering (727153 iMARECULTURE builds VR, AR and serious-game access to submerged shipwrecks and underwater cultural heritage). Manuscript, epigraphic and papyrological source work is a large recurring substrate (35 objectives mention manuscripts in a medieval context, 31 epigraphy, 12 papyri).
  texture: Objectives are long, discursive, first-person MSCA/ERC prose and rarely echo the euroSciVoc label verbatim - 'archaeometry' or 'ethnoarchaeology' appear as tags while the text says 'isotope analysis', 'radiocarbon', 'coin identification'. Tags are noisy in the science direction (ancient-DNA and radiocarbon projects are shared with biological sciences). Naive LIKE probes misfire: '%cycling%' matches 'recycling', '%slum%' matches the acronym 'Sislum' - topic filters need distinctive multi-word phrases. report_text coverage is high, so teasers are usable gold evidence.
  read: 802349, 843337, 727153
  good for: vector L2/L3 thematic questions - period- or method-defined clusters of 2-6 projects are easy to find (maritime archaeology 6, Viking Age 5, dendrochronology 2), so single-topic multi-project synthesis and small-set comparison are well supported; objectives state a research question explicitly, giving clean reference answers.
  thin for: SQL-route questions (no numeric/structural distinctiveness beyond funding scheme) and hybrid filters, which mostly reduce to 'MSCA-IF humanities' - a filter no user would state. Also thin for results-oriented questions on 2021-2023 grants with first-period-only reports.
  mapped: cp4

- region: m05
  bucket: social sciences / law
  slice: split_part(euroSciVocPath,'/',1)='social sciences' AND split_part(euroSciVocPath,'/',2)='law'
  size: 866 projects
  about: A rights-and-enforcement bucket rather than a doctrinal one: 455 projects sit at the bare top node, then human rights (217 at level 3), law enforcement (97), criminology (51), international law (43). Read members span critical socio-legal scholarship and security innovation - 756672 HumanTrafficking argues for shifting anti-trafficking work from criminal justice and border control to labour-market regulation; 679362 PRILA documents how accountability, rule of law and rights are experienced inside European prisons; 101038097 P-ADMIRAL analyses whether the contemporary law of the sea can accommodate autonomous and unmanned vessels. Asylum/refugee cases form a distinct sub-cluster (15 projects), as does imprisonment (17).
  texture: Two registers coexist and must not be conflated - ERC/MSCA legal-theory objectives (dense, argumentative, doctrinal) and H2020 security-call projects (CONNEXIONs, CRiTERIA) whose text is about platforms and tools with the tag attached only to the ethics/LEA dimension. A tag-only filter therefore pulls in technology projects; the theme must be confirmed in the objective, and 'law enforcement' in particular is mostly technology. report_text coverage is high and teasers restate the legal problem plainly.
  read: 756672, 679362, 101038097
  good for: vector L2/L3 on well-named legal topics - human trafficking (5), law of the sea (4), the International Criminal Court (4), prisons (17) - each an ordinary phrase a user would type. Also good for ambiguous-route seeds, since enforcement technology and doctrinal scholarship answer different readings of the same question.
  thin for: L1 single-project questions on generic themes - 'human rights' is shared by 117 projects, so no one project is uniquely identified. Also thin for SQL/aggregate questions: nothing in the columns marks a project as legal, the classification lives only in euroscivoc.
  mapped: cp4

- region: m06
  bucket: social sciences / social geography
  slice: split_part(euroSciVocPath,'/',1)='social sciences' AND split_part(euroSciVocPath,'/',2)='social geography'
  size: 870 projects
  about: Despite the label, this bucket is overwhelmingly transport engineering and mobility technology: 805 of 870 projects sit under the level-3 node 'transport', against 42 urban studies and 30 cultural and economic geography. Dominant leaves are electric vehicles (209), public transport (132), air traffic management (129), GNSS (112). Read members bear this out - 682337 NICENAV is an SME building an ITAR-free fibre-optic-gyroscope inertial navigation system for manned and unmanned aircraft (avionics hardware), while 769819 HiReach targets transport poverty and mobility exclusion for vulnerable groups via small-scale shared services. A drone/UAS traffic-management cluster (14 objectives mention 'U-space') is a further distinct region.
  texture: The noisiest bucket in this run - the label is almost never echoed in member text, and most members read as transport, aerospace or energy projects. A question phrased in the taxonomy's own words would retrieve the wrong things; questions must use the transport/mobility vocabulary the text actually uses. Register splits between SME-instrument market-facing prose (NICENAV, Sislum) and RIA/CSA consortium prose (HiReach, INCLUSION). Substring probes are dangerous: a '%cycling%' probe returns 29 projects but many are recycling, and '%slum%' matches the acronym 'Sislum'.
  read: 682337, 769819
  good for: vector L1 seeds, unusually - narrow engineering topics are uniquely instantiated (inertial navigation 1), so single-project lookups are reliable. Also L2/L3 on named technology clusters (U-space 14, rural mobility 3), and adversarial seeds exploiting the label-vs-content mismatch.
  thin for: anything treating this as a humanities-style geography region - urban studies (42) and cultural and economic geography (30) are too small and heterogeneous for a survey, and there is no critical-geography corpus. Thin for hybrid filters stating the taxonomy label as a user filter, since no user describes an avionics INS project that way.
  mapped: cp4

- region: m07
  bucket: natural sciences / computer and information sciences
  slice: EXISTS (SELECT 1 FROM euroSciVoc e WHERE e.projectID=p.id AND split_part(e.euroSciVocPath,'/',1)='natural sciences' AND split_part(e.euroSciVocPath,'/',2)='computer and information sciences')
  size: 7654 projects  (SELECT COUNT(*) FROM (SELECT DISTINCT projectID FROM euroSciVoc WHERE split_part(euroSciVocPath,'/',1)='natural sciences' AND split_part(euroSciVocPath,'/',2)='computer and information sciences') -> 7654)
  about: Reading the extremes first: the two biggest-funded members are infrastructure megaprojects - HBP SGA2 (785907, EUR 88,000,000) building six ICT platforms for neuroinformatics, brain simulation, high-performance analytics and neurorobotics, and EPI SGA1 (826647, EUR 79,991,745) taping out a low-power European HPC/automotive processor - while the oldest member (637529, EUR 60,000) is a small ERC text-mining job extracting funding statements from Europe PubMed Central full texts. So the bucket spans four orders of magnitude of budget and mixes core computing R&D with computing used as a service to another discipline. Probing further found genuine CS-internal research too: higher-order SMT and superposition provers for Isabelle/Coq (713999), serverless data-analytics platforms (825184, 825040), approximate/transprecision computing (732631, 956090) and SME-Instrument security products such as a CAPTCHA-free authentication platform (684168) and an AI image-forgery/deepfake detector (878319).
  texture: 7581 of 7654 members carry a report row (99.0%), above the 98.1% corpus rate, so report teasers are usable evidence here. Member text names its techniques in its own words ("satisfiability modulo theories", "deepfakes", "serverless") rather than echoing euroSciVoc labels, so topic questions should be written from text phrases, not tag names. Tag noise is real: 785907 (Human Brain Project) and 796752 (FLOODARC, a Mediterranean flood-archive project) both sit in this bucket, and corpus-wide singleton leaf tags reachable from here include 'hydrometeorology' and 'other medical sciences' - leaf labels are unreliable as topic proxies.
  read: 785907, 826647, 637529, 878319, 684168, 713999
  read first: 785907, 826647, 637529
  good for: Vector L1 seeds: the bucket is large and heterogeneous enough that sharply-worded technique phrases isolate exactly one project corpus-wide (deepfake, CAPTCHA, satisfiability modulo theories). Also supports L2/L3 technique clusters (program synthesis, differential privacy, serverless computing) whose members are genuinely comparable CS research.
  thin for: Poor for questions keyed on the euroSciVoc leaf label itself, because members are tagged into computing for applying it as much as for researching it. Also thin for absence claims, since almost any computing term returns something across 7,654 members.
  mapped: cp5

- region: m08
  bucket: social sciences / economics and business
  slice: EXISTS (SELECT 1 FROM euroSciVoc e WHERE e.projectID=p.id AND split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='economics and business')
  size: 4711 projects  (WITH b AS (SELECT DISTINCT e.projectID AS id FROM euroSciVoc e WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='economics and business') SELECT COUNT(*) FROM b -> 4711)
  about: Reading the biggest and the oldest and newest members shows this is not a room full of economists. SGA3 (861952, EUR 81.8m) is the COST Association's own coordination grant for running research networks; General Purpose DP (650473, 2014) is an SME-instrument grant from a naval architecture firm to build a cheap dynamic-positioning controller and sell it to shipyards, where the only economics is the business plan and turnover forecast; ExpBoD (101025105, 2022) is an MSCA fellowship measuring the socio-economic and psychological burden of disease in Danish register data. Alongside these sit genuine research-economics grants - INFL (682288) on risk-adjusted inflation and central-bank liabilities, ORIGENDER (841969) on gender norms and the pay gap - so the region mixes ERC/MSCA academic economics with a very large SME commercialisation tail whose business content is market-size and revenue projections.
  texture: 4676 of the 4711 members have a report row (99.3%). Only three third-level nodes exist under the field: business and management 3231, economics 1936, and 32 projects tagged at the field itself with an empty third level; leaf tags are coarse (business models alone holds 1677, employment 787, productivity 712) and no leaf in this bucket is a singleton - the smallest is 'economic impact of epidemics' at 4. So narrow questions must be built from distinctive phrases in objective/title, not from tags. Tag noise runs toward business: an SME hardware project is tagged economics and business because it has a commercialisation plan.
  read: 861952, 650473, 101025105, 682288, 780143, 841969
  read first: 861952, 650473, 101025105
  good for: Single-project vector questions built on distinctive economic phrases (microfinance, inflation expectations, gender pay gap each occur in exactly one project corpus-wide), and small multi-project themes on labour and public finance (tax evasion 4, gig economy 6).
  thin for: Tag-scoped questions of any narrowness - the leaves are huge and generic (business models 1677), so nothing at leaf level lands inside an L1/L2 window.
  mapped: cp5

- region: m09
  bucket: natural sciences / chemical sciences
  slice: EXISTS (SELECT 1 FROM euroSciVoc e WHERE e.projectID=p.id AND split_part(e.euroSciVocPath,'/',1)='natural sciences' AND split_part(e.euroSciVocPath,'/',2)='chemical sciences')
  size: 4331 projects  (WITH b AS (SELECT DISTINCT p.id FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='natural sciences' AND split_part(e.euroSciVocPath,'/',2)='chemical sciences') SELECT COUNT(*) FROM b -> 4331)
  about: Reading the extremes: the biggest-budget member (PYROCO2, 101037009) is an industrial demonstrator turning captured CO2 plus green hydrogen into acetone through a thermophilic microbial bioprocess, then catalytically into fuels and recyclable polymers; the newest (CSE-LBATTS, 101021759) is a single-fellow MSCA project fabricating composite solid electrolytes to replace flammable liquid electrolytes in lithium batteries; the oldest (MACC-III, 633080) is a Copernicus atmospheric-composition service covering air quality, stratospheric ozone, UV and solar-energy resources. So the region is mostly chemistry-as-means - energy conversion and storage, CO2 utilisation, catalysis, materials synthesis - with a real bench/theory core (organocatalysis, relativistic electronic-structure theory) and a tail of atmospheric monitoring that is chemical only in subject matter.
  texture: 4248 of 4331 members (98.1%) have a report row. Member text uses working technical vocabulary, never the taxonomy label: MACC-III never says 'chemical sciences' and CSE-LBATTS never says 'electrochemistry' though it is filed there, so questions must be built from substantive phrases (composite solid electrolyte, photoelectrochemical cell, C-H acid) rather than euroSciVoc wording. Third-level tags concentrate in inorganic chemistry 1740, organic chemistry 1002, catalysis 827, polymer sciences 725, electrochemistry 562, analytical chemistry 336, physical chemistry 174; 54 members carry a bare two-level path with no third level, and nuclear chemistry has only 38. Only ONE euroSciVoc leaf in this whole bucket is unique corpus-wide ('nuclear chemistry' on PCCDX, 702635), so L1 seeds here have to come from distinctive free-text phrases, not from leaf tags.
  read: 101037009, 633080, 101021759, 891647, 694228, 849068
  read first: 101037009, 633080, 101021759
  good for: Narrow single-project vector questions, because distinctive method phrases really are unique here - 'thermophilic microbial', 'composite solid electrolyte' and 'relativistic quantum chemistry' each match exactly 1 project corpus-wide. Also small 2-4 project clusters around named chemistries (chiral phosphoric acid 2, carbon nitride 4) that are enumerable as gold.
  thin for: Broad chemistry questions: head terms like catalysis, battery or CO2 conversion return hundreds of projects spread across engineering and energy buckets and cannot be enumerated. Also weak for quantitative chemistry questions - yields, conversions and temperatures live only in free text, never in a column.
  mapped: cp5

## Structural findings

Corpus facts that are not topical and therefore have no bucket list to check off: trap pairs, verified absences, facts carried in both a structured column and free text, and value inventories. Feeds the SQL, Adversarial and Ambiguous routes. Append-only, no denominator.

- id: sf-01
  kind: value-inventory
  claim: `euroSciVocPath` values do NOT begin with a leading slash, despite the leading-slash example that stood in schema_docs.md until `sd2`. Matching a subtree needs `LIKE 'natural sciences/%'` or `LIKE '%/leaf%'`, never `LIKE '/natural sciences/%'`.
  evidence: `SELECT COUNT(*) FROM euroscivoc WHERE euroSciVocPath LIKE '/%'` -> 0 of 111,614. The sibling column `euroSciVocCode` DOES lead with a slash (e.g. '/25/61/383'), which is why the wrong form looked plausible.
  serves: every route that filters on topic; recorded here because it silently returned empty results for both exploration and the runtime SQL path.

- id: sf-02
  kind: value-inventory
  claim: euroSciVoc has 6 top-level branches, not the 40-ish that a `split_part(...,'/',2)` inventory suggests - index 2 is the second level (40 named fields of science), index 1 is the branch.
  evidence: `SELECT COUNT(DISTINCT split_part(euroSciVocPath,'/',1)) FROM euroscivoc` -> 6; `... ,'/',2))` -> 41 (40 named + the empty string on the 94 depth-1 rows). Branch counts: natural sciences 22,075 projects, engineering and technology 13,696, social sciences 10,099, medical and health sciences 8,580, humanities 2,699, agricultural sciences 2,302.
  serves: slice partitioning and the width rule, both of which are stated in terms of "top-level branches"; cp1/cp2 used second-level categories under that name.

## Distributions

Not yet explored (scoped run "find 15 vector topics", 2026-07-23).

## SQL

Not yet explored (scoped run "find 15 vector topics", 2026-07-23).

## Vector

15 candidate seeds for `/draft-vector-question`, bucketed by the bank's level definition (|satisfying projects| = 1 -> L1, 2-4 -> L2, 5+ -> L3). Clusters were sized by distinct-project count on `euroscivoc.euroSciVocTitle`; each seed's theme was confirmed by reading the project text via `get_project_text`, and report_text coverage was counted by joining to `report_text`. Spread: **13 of the 15 top-level euroscivoc branches** are distinct, no branch carries more than 2 candidates (biological sciences x2, clinical medicine x2); level mix is 6 L1 / 5 L2 / 4 L3.

**Load-bearing finding for drafting: euroscivoc leaf labels are noisy on interdisciplinary / MSCA fellowships.** Reading text repeatedly showed the taxonomy tag diverging from the actual work - `ethnomycology` tagged an aquatic-fungi ecology project (CRYPTRANS), `sustainable architecture` tagged a district-heating project (COOL DH), and `agroecology` tagged a green-economy ethnography (TRANSITION-FRICTION, discarded during this run). Consequence: every seed below is anchored on **observed project text**, not on the tag; and each candidate's `term_style` flag records whether the tag's own words actually appear in the members' text (echoed verbatim -> exact-term material; absent/paraphrased -> paraphrase material). Disease-name and technical leaves (`duchenne muscular dystrophy`, `knot theory`, `rare earths`) were reliably on-theme; method/interdisciplinary leaves were the noisy ones.

- id: vector-01
  topic: A project that discovered many species of fungi, new to science, living in the deep water of boreal lakes, characterised by sequencing DNA and RNA rather than by culturing.
  recommend: route=vector level=L1 subtype=identify term_style=paraphrase
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='ethnomycology'` -> 1 (project 660122 CRYPTRANS). Text read: objective/report describe "early diverging fungal lineages (Chytridiomycota, Cryptomycota)" in ~144 boreal lakes, ~25% of species undescribed, via metabarcoding/metatranscriptomics.
  axes: branch=biological-sciences leaf=ethnomycology satisfying=1 report_coverage=1/1 term_style=paraphrase sample_ids=660122 sample_acr=CRYPTRANS
  why: A vivid, specific single-project discovery whose taxonomy tag ("ethnomycology") is off-theme, forcing the question to describe the work in plain words - clean identify seed.

- id: vector-02
  topic: A project that reconstructed how the frequency and intensity of extreme floods in the Western Mediterranean varied over centuries to millennia, using natural sediment layers as archives instead of instrument records.
  recommend: route=vector level=L1 subtype=detail term_style=paraphrase
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='hydrometeorology'` -> 1 (project 796752 FLOODARC). Text read: paleoflood reconstruction from lacustrine sediment microfacies + geochemistry, Iberian lakes spanning up to 26,000 years, "Paleo Flood Frequency Analyses".
  axes: branch=earth-and-environmental-sciences leaf=hydrometeorology satisfying=1 report_coverage=1/1 term_style=paraphrase sample_ids=796752 sample_acr=FLOODARC
  why: A method-in-free-text detail (how past floods are read from lake sediment) not recoverable from any stored column - textbook L1 detail.

- id: vector-03
  topic: A project commercialising a steering/brake/throttle system with no mechanical linkage so that people with a wide range of physical disabilities can drive a car with joysticks or handlebars.
  recommend: route=vector level=L1 subtype=identify term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='drive by wire'` -> 1 (project 807968 Joysteer 3.0). Text read: "drive-by-wire" system replacing conventional controls for disabled drivers, plus teleoperated/autonomous off-highway use.
  axes: branch=mechanical-engineering leaf=drive-by-wire satisfying=1 report_coverage=1/1 term_style=exact-term sample_ids=807968 sample_acr="Joysteer 3.0"
  why: Distinctive term "drive-by-wire" is echoed verbatim in the text - good exact-term identify probe; assistive-tech domain untouched by the bank.

- id: vector-04
  topic: A project in which philosophers and physicists together re-examined how time is represented at the beginning of the universe, contrasting Plato's and Kant's conceptions with emergent time in quantum gravity and quantum cosmology.
  recommend: route=vector level=L1 subtype=identify term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='history of philosophy'` -> 1 (project 758145 PROTEUS). Text read: history/philosophy of cosmology, Plato & Kant vs quantum gravity, "time and timelessness in fundamental physics".
  axes: branch=philosophy-ethics-and-religion leaf=history-of-philosophy satisfying=1 report_coverage=1/1 term_style=exact-term sample_ids=758145 sample_acr=PROTEUS
  why: Humanities-x-physics seed with strongly distinctive vocabulary (Plato, Kant, quantum gravity) - anchors a branch the bank never touches.

- id: vector-05
  topic: A project building an ultra-high-capacity 5G wireless backhaul layer operating above 100 GHz, powered by newly designed millimetre-wave traveling wave tubes.
  recommend: route=vector level=L1 subtype=identify term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='fixed wireless network'` -> 1 (project 762119 ULTRAWAVE). Text read: D-band/G-band point-to-multipoint wireless backhaul, "traveling wave tubes", vacuum + solid-state electronics + photonics.
  axes: branch=electrical-engineering leaf=fixed-wireless-network satisfying=1 report_coverage=1/1 term_style=exact-term sample_ids=762119 sample_acr=ULTRAWAVE
  why: Highly distinctive engineering vocabulary ("traveling wave tubes", "millimetre wave backhaul") - strong exact-term identify seed.

- id: vector-06
  topic: A project that made 3D-printable dental resins kill bacteria on contact, so that printed restorations and orthodontic appliances resist the biofilms that cause decay and inflammation.
  recommend: route=vector level=L1 subtype=detail term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='orthodontics'` -> 1 (project 665587 APPROAcH). Text read: antimicrobial photo-cured resins with "quaternary ammonium groups" that "kill bacteria on contact" via stereolithography; biocompatibility by limiting leaching.
  axes: branch=clinical-medicine leaf=orthodontics satisfying=1 report_coverage=1/1 term_style=exact-term sample_ids=665587 sample_acr=APPROAcH
  why: The antibacterial mechanism (quaternary ammonium in the resin) is a free-text detail with no stored-column route - clean L1 detail.

- id: vector-07
  topic: Projects applying computational creativity / AI to the creative arts - e.g. a mood-indexed multimodal database linking music, lyrics and dance motion capture for interactive music generation.
  recommend: route=vector level=L2 subtype=comparison term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='computational creativity'` -> 3 (659434 MUSICAL-MOODS, 754401 eTryOn, 951908 I2C8). Text read (MUSICAL-MOODS): "computational creativity", music emotion recognition, motion-capture dataset, interactive music systems.
  axes: branch=computer-and-information-sciences leaf=computational-creativity satisfying=3 report_coverage=3/3 term_style=exact-term sample_ids=659434,754401,951908 sample_acr=MUSICAL-MOODS,eTryOn,I2C8
  why: A small AI-x-arts cluster; drafting must verify the three members share the creative-generation theme (eTryOn is fashion try-on) before fixing gold - advisory L2 comparison.

- id: vector-08
  topic: Projects using underwater archaeology of shipwrecks to reconstruct early-modern Mediterranean shipbuilding technique and its exchange with the Atlantic tradition.
  recommend: route=vector level=L2 subtype=synthesis term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='underwater archaeology'` -> 3 (843337 ModernShip, 777998 CONCHA, 705225 MEDICINE). Text read (ModernShip): excavation of Mortella/Santiago de Galicia wrecks, "Mediterranean vs Atlantic technical cultures", shipbuilding model.
  axes: branch=history-and-archaeology leaf=underwater-archaeology satisfying=3 report_coverage=3/3 term_style=exact-term sample_ids=843337,777998,705225 sample_acr=ModernShip,CONCHA,MEDICINE
  why: Coherent maritime-archaeology cluster with vocabulary echoed verbatim - good L2 synthesis seed in a humanities branch.

- id: vector-09
  topic: Projects in mathematical knot theory studying how knots appear and evolve in physical systems - knotted electromagnetic field lines, quantum/optical vortex knots, and links between knot topology and induced magnetic fields.
  recommend: route=vector level=L2 subtype=comparison term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='knot theory'` -> 2 (101023017 KNOTDYNAPP, 682537 BOPNIE). Text read (KNOTDYNAPP): "knot theory", electromagnetic knots satisfying Maxwell's equations, quantum vortex knots, fibered knots vs magnetic fields.
  axes: branch=mathematics leaf=knot-theory satisfying=2 report_coverage=2/2 term_style=exact-term sample_ids=101023017,682537 sample_acr=KNOTDYNAPP,BOPNIE
  why: Distinctive, self-consistent mathematical vocabulary; a clean two-project comparison anchoring the mathematics branch.

- id: vector-10
  topic: Projects evaluating the economic burden and cost-effectiveness of responses to infectious-disease epidemics - e.g. the health-economics of community active case finding for tuberculosis in low- and middle-income countries.
  recommend: route=vector level=L2 subtype=synthesis term_style=paraphrase
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='economic impact of epidemics'` -> 4 (733174 IMPACT TB, 826722 TLA-Gut, 812021 SIGNIA). Text read (IMPACT TB): "economic and social impacts of the TB pandemic", cost-per-case, epidemic transmission modelling in Vietnam/Nepal.
  axes: branch=economics-and-business leaf=economic-impact-of-epidemics satisfying=4 report_coverage=4/4 term_style=paraphrase sample_ids=733174,826722,812021 sample_acr="IMPACT TB",TLA-Gut,SIGNIA
  why: The tag's phrase is not echoed verbatim (paraphrase material); drafting must confirm the four members share the epidemic-economics theme - advisory L2 synthesis.

- id: vector-11
  topic: Projects developing permanent magnets that avoid or minimise rare-earth elements, to cut Europe's dependence on critical raw materials for electric vehicles and wind turbines.
  recommend: route=vector level=L2 subtype=comparison term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='rare earths'` -> 2 (686056 NOVAMAG, 636876 REDMUD). Text read (NOVAMAG): "rare earths", "critical raw materials", RE-free/lean permanent magnets by computational screening, reduce dependence on China.
  axes: branch=environmental-engineering leaf=rare-earths satisfying=2 report_coverage=2/2 term_style=exact-term sample_ids=686056,636876 sample_acr=NOVAMAG,REDMUD
  why: Clean two-project materials cluster (NOVAMAG designs RE-free magnets; REDMUD recovers rare earths) with vocabulary echoed verbatim - solid L2 comparison.

- id: vector-12
  topic: A landscape of projects studying freshwater lake and river ecosystems - dissolved organic matter, microbial diversity, carbon cycling and drinking-water quality.
  recommend: route=vector level=L3 subtype=survey term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='freshwater ecosystems'` -> 12 (804673 sEEIngDOM, 730141 IMPRESS, 656647 MICROPATH, 731065 CHROME). Text read (sEEIngDOM): "freshwater ecosystems", "dissolved organic matter", chemodiversity, lake carbon burial, drinking-water treatment.
  axes: branch=biological-sciences leaf=freshwater-ecosystems satisfying=12 report_coverage=12/12 term_style=exact-term sample_ids=804673,730141,656647 sample_acr=sEEIngDOM,IMPRESS,MICROPATH
  why: Large, coherent, verbatim-vocabulary cluster - a strong L3 survey; characterize the set with named examples rather than dumping all 12.

- id: vector-13
  topic: A landscape of projects automating flexible/robotic surgery - autonomous catheters, endoscopes and intraluminal instruments that navigate the body's natural lumens.
  recommend: route=vector level=L3 subtype=survey term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='robotic surgery'` -> 9 (813782 ATLAS, 878204 AUTO NERVE, 875523 SMARTsurg). Text read (ATLAS): "surgical robotics", autonomous intraluminal navigation of flexible instruments (ureteroscopy, colonoscopy, endovascular).
  axes: branch=clinical-medicine leaf=robotic-surgery satisfying=9 report_coverage=9/9 term_style=exact-term sample_ids=813782,878204,875523 sample_acr=ATLAS,"AUTO NERVE",SMARTsurg
  why: Coherent surgical-robotics cluster of 9 with distinctive shared vocabulary - a robust L3 survey seed.

- id: vector-14
  topic: A landscape of projects making the heating, cooling and energy performance of buildings more sustainable - e.g. low-temperature district heating that recovers low-grade surplus heat for energy-efficient buildings.
  recommend: route=vector level=L3 subtype=survey term_style=paraphrase
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='sustainable architecture'` -> 10 (767799 COOL DH, 673874 ScalinGreen, 825464 AMANDA). Text read (COOL DH): low-temperature district heating, heat recovery, energy-efficient buildings, heat pumps - the tag "sustainable architecture" does not appear in the text.
  axes: branch=civil-engineering leaf=sustainable-architecture satisfying=10 report_coverage=10/10 term_style=paraphrase sample_ids=767799,673874,825464 sample_acr="COOL DH",ScalinGreen,AMANDA
  why: Large cluster but the tag is loose; frame the survey around the real shared theme (energy-efficient/low-carbon buildings) and treat as paraphrase material - drafting should re-verify member coherence.

- id: vector-15
  topic: A landscape of projects on Duchenne muscular dystrophy - the disease's fibrosis and inflammation biology and emerging gene, cell and small-molecule therapies.
  recommend: route=vector level=L3 subtype=survey term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocTitle='duchenne muscular dystrophy'` -> 9 (658560 DYS_FUNCTION, 667078 e-walk, 739736 DMD2CURE; seed 659338 Subpopulations). report coverage `LEFT JOIN report_text` -> 8/9. Text read (659338): fibroblast subpopulations driving fibrosis vs regeneration in DMD muscle, mouse lineage tracing.
  axes: branch=basic-medicine leaf=duchenne-muscular-dystrophy satisfying=9 report_coverage=8/9 term_style=exact-term sample_ids=659338,658560,667078 sample_acr=Subpopulations,DYS_FUNCTION,e-walk
  why: A disease-name leaf is reliably on-theme; 9 projects give a rich, coherent L3 survey in the untouched basic-medicine branch.

- id: vector-16
  topic: Projects developing animal-venom toxins as drug leads or studying venom immunotherapy
  recommend: route=vector level=L2 subtype=topical-multi
  bucket: medical and health sciences / basic medicine
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='medical and health sciences' AND split_part(e.euroSciVocPath,'/',2)='basic medicine' AND (p.objective ILIKE '%venom%' OR p.title ILIKE '%venom%')` -> n=4 ; `SELECT DISTINCT p.id, p.acronym FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='medical and health sciences' AND split_part(e.euroSciVocPath,'/',2)='basic medicine' AND (p.objective ILIKE '%venom%' OR p.title ILIKE '%venom%') ORDER BY p.id` -> 4 rows: 655153 IgEPath, 714366 GUTPEPTIDES, 891733 MITafterVIT, 949830 ToxMim
  axes: domain=basic-medicine term_style=technical theme=venom-toxins satisfying=4
  why: Exactly four basic-medicine projects mention venom, spanning toxin-derived drug discovery (ToxMim) and venom immunotherapy - a clean L2 set.

- id: vector-17
  topic: CAR-T / chimeric antigen receptor engineered cell immunotherapy projects
  recommend: route=vector level=L3 subtype=topical-survey
  bucket: medical and health sciences / basic medicine
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='medical and health sciences' AND split_part(e.euroSciVocPath,'/',2)='basic medicine' AND (p.objective ILIKE '%chimeric antigen receptor%' OR p.title ILIKE '%chimeric antigen receptor%')` -> n=12 ; `SELECT DISTINCT p.id, p.acronym FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='medical and health sciences' AND split_part(e.euroSciVocPath,'/',2)='basic medicine' AND (p.objective ILIKE '%chimeric antigen receptor%' OR p.title ILIKE '%chimeric antigen receptor%') ORDER BY p.id` -> 12 rows including CARAMBA, EURE-CART, CARsen, SweetCAR, GENESHUTTLE ; `SELECT id, acronym FROM project WHERE id IN (754658,733297)` -> 754658 CARAMBA, 733297 EURE-CART - both objectives describe CAR-T clinical trials
  axes: domain=basic-medicine term_style=technical-acronym theme=CAR-T satisfying=12
  why: Twelve members whose objectives literally describe CAR-T cell therapy, enough for an L3 topical survey with unambiguous gold.

- id: vector-18
  topic: Organ-on-chip / microphysiological human tissue models
  recommend: route=vector level=L3 subtype=topical-survey
  bucket: medical and health sciences / basic medicine
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='medical and health sciences' AND split_part(e.euroSciVocPath,'/',2)='basic medicine' AND (p.objective ILIKE '%organ-on-chip%' OR p.title ILIKE '%organ-on-chip%')` -> n=15
  axes: domain=basic-medicine term_style=technical theme=organ-on-chip satisfying=15
  why: Fifteen basic-medicine projects use the literal phrase organ-on-chip in title or objective, a well-bounded L3 topical set.

- id: vector-19
  topic: Paid domestic and care workers belonging to the ethnic majority (non-migrant) workforce
  recommend: route=vector level=L1 subtype=topical-single
  bucket: social sciences / sociology
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='sociology' AND (p.objective ILIKE '%domestic worker%' OR p.title ILIKE '%domestic worker%')` -> n=1 ; `SELECT DISTINCT p.id, p.acronym FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='sociology' AND (p.objective ILIKE '%domestic worker%' OR p.title ILIKE '%domestic worker%') ORDER BY p.id` -> 1 row: 799195 MAJORdom
  axes: domain=sociology term_style=lay theme=paid-domestic-care-work satisfying=1
  why: MAJORdom is the single sociology project on ethnic-majority paid domestic/care workers, comparing Italy and the USA - a clean L1 target.

- id: vector-20
  topic: Loneliness and social isolation of older adults addressed with companion technology
  recommend: route=vector level=L2 subtype=topical-multi
  bucket: social sciences / sociology
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='sociology' AND (p.objective ILIKE '%loneliness%' OR p.title ILIKE '%loneliness%')` -> n=3 ; `SELECT DISTINCT p.id, p.acronym FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='sociology' AND (p.objective ILIKE '%loneliness%' OR p.title ILIKE '%loneliness%') ORDER BY p.id` -> 3 rows: 643808 MARIO, 769872 EMPATHIC, 868008 ADOPT GRANDPARENTS
  axes: domain=sociology term_style=lay theme=loneliness-ageing satisfying=3
  why: Three sociology-tagged projects name loneliness, all on older-adult isolation and assistive/companion technology - a tight L2 set.

- id: vector-21
  topic: Gentrification and neighbourhood change in diverse urban neighbourhoods
  recommend: route=vector level=L3 subtype=topical-survey
  bucket: social sciences / sociology
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='sociology' AND (p.objective ILIKE '%gentrification%' OR p.title ILIKE '%gentrification%')` -> n=7 ; `SELECT DISTINCT p.id, p.acronym FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='sociology' AND (p.objective ILIKE '%gentrification%' OR p.title ILIKE '%gentrification%') ORDER BY p.id` -> 7 rows: 658875 GGG, 678034 GREENLULUS, 707726 NEIGHBOURCHANGE, 752547 SDD, 837749 SUSTEUS, 950641 HIPPO, 101025665 Ethno-gentrification
  axes: domain=sociology term_style=lay theme=gentrification satisfying=7
  why: Seven sociology projects discuss gentrification in their objectives, from green gentrification to ethnic-led gentrification, supporting an L3 survey.

- id: vector-22
  topic: Lobbying by non-state actors and interest groups in EU policymaking
  recommend: route=vector level=L3 subtype=topical-survey
  bucket: social sciences / political sciences
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='political sciences' AND (p.objective ILIKE '%lobbying%' OR p.title ILIKE '%lobbying%')` -> n=5 ; `SELECT DISTINCT p.id, p.acronym FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='political sciences' AND (p.objective ILIKE '%lobbying%' OR p.title ILIKE '%lobbying%') ORDER BY p.id` -> 5 rows: 637662 PEMP, 657949 LOBFRAM, 702134 DemocInChange, 740447 FEDCIT, 842868 PROSPER
  axes: domain=political-sciences term_style=lay theme=lobbying satisfying=5
  why: Five political-science projects name lobbying, e.g. LOBFRAM on non-state-actor lobbying in EU foreign policy, exactly at the L3 threshold.

- id: vector-23
  topic: Corruption, public-procurement transparency and accountability of public spending
  recommend: route=vector level=L3 subtype=topical-survey
  bucket: social sciences / political sciences
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='political sciences' AND (p.objective ILIKE '%corruption%' OR p.title ILIKE '%corruption%')` -> n=9 ; `SELECT DISTINCT p.id, p.acronym FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='political sciences' AND (p.objective ILIKE '%corruption%' OR p.title ILIKE '%corruption%') ORDER BY p.id` -> 9 rows: 645833 OpenBudgets.eu, 645852 DIGIWHIST, 645886 YDS, 693537 INFORM, 694632 PROTEGO, 823815 EventRights, 838371 VOTEF, 840978 BIZPOL, 945501 DEPART
  axes: domain=political-sciences term_style=lay theme=corruption-transparency satisfying=9
  why: Nine political-science projects address corruption, several (DIGIWHIST, OpenBudgets.eu) building open procurement data tools - a solid L3 survey set.

- id: vector-24
  topic: Referendums and direct-democracy votes as instruments of political contestation
  recommend: route=vector level=L3 subtype=topical-survey
  bucket: social sciences / political sciences
  evidence: `SELECT count(DISTINCT p.id) AS n FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='political sciences' AND (p.objective ILIKE '%referendum%' OR p.title ILIKE '%referendum%')` -> n=6 ; `SELECT DISTINCT p.id, p.acronym FROM project p JOIN euroSciVoc e ON e.projectID=p.id WHERE split_part(e.euroSciVocPath,'/',1)='social sciences' AND split_part(e.euroSciVocPath,'/',2)='political sciences' AND (p.objective ILIKE '%referendum%' OR p.title ILIKE '%referendum%') ORDER BY p.id` -> 6 rows: 638115, 788304, 817582, 838371 VOTEF, 838418, 894303
  axes: domain=political-sciences term_style=lay theme=referendums satisfying=6
  why: Six political-science projects study referendums, including VOTEF on Colombian anti-extractivism referendums, giving an L3 set with distinct national cases.

- id: vector-25
  topic: Projects studying shipwrecks and underwater/maritime archaeology in European waters
  recommend: route=vector level=L3 subtype=thematic-survey
  bucket: humanities / history and archaeology
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'humanities/history and archaeology%' AND (lower(p.objective) LIKE '%shipwreck%' OR lower(p.objective) LIKE '%underwater archae%' OR lower(p.objective) LIKE '%maritime archae%')` -> n = 6 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'humanities/history and archaeology%' AND (lower(p.objective) LIKE '%shipwreck%' OR lower(p.objective) LIKE '%underwater archae%' OR lower(p.objective) LIKE '%maritime archae%') GROUP BY 1,2 ORDER BY 1` -> 727153 iMARECULTURE, 833143 TRANSPACIFIC, 863393 AISLES, 892446 STAMPEDE, 101022386 WATERISKULT, 101025204 NDTD
  axes: domain=history-archaeology term_style=domain-phrase theme=underwater-archaeology satisfying=6
  why: Six projects, all confirmed in objective text, form a natural survey of EU-funded underwater archaeology with distinct angles (VR access, wreck-based shipbuilding history, heritage risk).

- id: vector-26
  topic: Research on the Viking Age - its origins, names, poetry and silver economy
  recommend: route=vector level=L3 subtype=thematic-survey
  bucket: humanities / history and archaeology
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'humanities/history and archaeology%' AND lower(p.objective) LIKE '%viking%'` -> n = 5 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'humanities/history and archaeology%' AND lower(p.objective) LIKE '%viking%' GROUP BY 1,2 ORDER BY 1` -> 657128 LEXICON POETICUM, 792006 GENTES, 797386 ArcNames, 802349 SILVER, 949886 BODY-POLITICS
  axes: domain=history-archaeology term_style=named-period theme=viking-age satisfying=5
  why: Five text-confirmed projects on one named historical period, spanning philology, onomastics and materials analysis - a clean L3 synthesis with genuinely different sub-answers.

- id: vector-27
  topic: Use of dendrochronology (tree-ring dating) in historical and archaeological projects
  recommend: route=vector level=L2 subtype=small-set-synthesis
  bucket: humanities / history and archaeology
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'humanities/history and archaeology%' AND lower(p.objective) LIKE '%dendrochronolog%'` -> n = 2 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'humanities/history and archaeology%' AND lower(p.objective) LIKE '%dendrochronolog%' GROUP BY 1,2 ORDER BY 1` -> 800204 VEILA, 101029581 WoodTiMe
  axes: domain=history-archaeology term_style=method-term theme=dendrochronology satisfying=2
  why: A precisely named dating method with exactly two carriers in the bucket, giving an unambiguous two-project answer set.

- id: vector-28
  topic: Projects addressing human trafficking - from labour-market regulation to detection tools for law enforcement
  recommend: route=vector level=L3 subtype=thematic-survey
  bucket: social sciences / law
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/law%' AND lower(p.objective) LIKE '%human trafficking%'` -> n = 5 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/law%' AND lower(p.objective) LIKE '%human trafficking%' GROUP BY 1,2 ORDER BY 1` -> 756672 HumanTrafficking, 786731 CONNEXIONs, 790798 PMT4NIIS, 101021866 CRiTERIA, 101027924 SIGNAL-LANDSCAPE
  axes: domain=law term_style=policy-phrase theme=human-trafficking satisfying=5
  why: Five text-confirmed projects on a phrase any user would type, and the set deliberately mixes socio-legal critique with LEA technology, which makes the reference answer non-trivial.

- id: vector-29
  topic: Law of the sea research - ocean governance, marine biodiversity treaties and autonomous vessels
  recommend: route=vector level=L2 subtype=small-set-synthesis
  bucket: social sciences / law
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/law%' AND lower(p.objective) LIKE '%law of the sea%'` -> n = 4 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/law%' AND lower(p.objective) LIKE '%law of the sea%' GROUP BY 1,2 ORDER BY 1` -> 639070 SUSTAINABLEOCEAN, 804599 MARIPOLDATA, 101018998 LOSFARE, 101038097 P-ADMIRAL
  axes: domain=law term_style=doctrinal-phrase theme=law-of-the-sea satisfying=4
  why: Exactly four projects share one named legal regime, spanning ocean governance and the unmanned-vessel gap that P-ADMIRAL's objective states explicitly.

- id: vector-30
  topic: Research on the International Criminal Court and international criminal justice
  recommend: route=vector level=L2 subtype=small-set-synthesis
  bucket: social sciences / law
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/law%' AND lower(p.objective) LIKE '%international criminal court%'` -> n = 4 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/law%' AND lower(p.objective) LIKE '%international criminal court%' GROUP BY 1,2 ORDER BY 1` -> 654261 ToEfDeCo, 746768 INCRICO, 748114 EaRL, 802053 JustSites
  axes: domain=law term_style=named-institution theme=international-criminal-justice satisfying=4
  why: A named institution stated in four objectives gives a crisply bounded L2 set with no tag-only members.

- id: vector-31
  topic: An SME project building an ITAR-free fibre-optic-gyroscope inertial navigation system for manned and unmanned aircraft
  recommend: route=vector level=L1 subtype=single-project-lookup
  bucket: social sciences / social geography
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/social geography%' AND lower(p.objective) LIKE '%inertial navigation%'` -> n = 1 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/social geography%' AND lower(p.objective) LIKE '%inertial navigation%' GROUP BY 1,2 ORDER BY 1` -> 682337 NICENAV
  axes: domain=social-geography term_style=technical-term theme=inertial-navigation satisfying=1
  why: One project only, and its objective names the distinguishing features (FOG technology, ITAR-free, DO-178C certification) that a reference answer can be checked against.

- id: vector-32
  topic: Mobility solutions for rural and low-density areas
  recommend: route=vector level=L2 subtype=small-set-synthesis
  bucket: social sciences / social geography
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/social geography%' AND lower(p.objective) LIKE '%rural%' AND lower(p.objective) LIKE '%mobility%'` -> n = 3 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/social geography%' AND lower(p.objective) LIKE '%rural%' AND lower(p.objective) LIKE '%mobility%' GROUP BY 1,2 ORDER BY 1` -> 770115 INCLUSION, 881825 RIDE2RAIL, 101034449 CLOE
  axes: domain=social-geography term_style=plain-language theme=rural-mobility satisfying=3
  why: Three projects whose objectives state rural/low-density mobility explicitly, an ordinary user phrasing in a bucket otherwise dominated by urban and aviation work.

- id: vector-33
  topic: U-space: traffic management for drones and unmanned aircraft in low-level airspace
  recommend: route=vector level=L3 subtype=thematic-survey
  bucket: social sciences / social geography
  evidence: `SELECT count(DISTINCT p.id) n FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/social geography%' AND lower(p.objective) LIKE '%u-space%'` -> n = 14 ; `SELECT p.id, p.acronym FROM project p JOIN euroscivoc v ON v.projectID=p.id WHERE v.euroSciVocPath LIKE 'social sciences/social geography%' AND lower(p.objective) LIKE '%u-space%' GROUP BY 1,2 ORDER BY 1` -> 14 rows incl. 783211 SAFEDRONE, 783230 PODIUM, 861696 LABYRINTH, 890378 USEPE, 893864 DACUS, 101017682 CORUS-XUAM, 101017702 AMU-LED
  axes: domain=social-geography term_style=programme-term theme=u-space satisfying=14
  why: A named European airspace concept with 14 text-confirmed carriers - large enough for a real synthesis question and tightly bounded by a term only this cluster uses.

- id: vector-34
  topic: AI-generated deepfake / digital image forgery detection for business documents and insurance claims
  recommend: route=vector level=L1 subtype=topical-multi
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / computer and information sciences
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%deepfake%' OR p.title ILIKE '%deepfake%'` -> 1 ; `SELECT p.id, p.acronym, p.title FROM project p WHERE p.objective ILIKE '%deepfake%' OR p.title ILIKE '%deepfake%'` -> 878319 | QI | AI-powered image forgery detection ; `SELECT COUNT(*) FROM project p JOIN (SELECT DISTINCT projectID id FROM euroSciVoc WHERE split_part(euroSciVocPath,'/',1)='natural sciences' AND split_part(euroSciVocPath,'/',2)='computer and information sciences') b ON b.id=p.id WHERE p.objective ILIKE '%deepfake%' OR p.title ILIKE '%deepfake%'` -> 1
  axes: topic=media-forensics; technique=AI image forgery detection; application=insurance/banking fraud
  why: Read 878319: its objective explicitly frames AI-automated image manipulation as 'deepfakes' and sells detection to banking/insurance, and it is the only project in the corpus using the word.

- id: vector-35
  topic: Eliminating CAPTCHAs and passwords via a phone-as-security-token distributed cryptographic authentication platform
  recommend: route=vector level=L1 subtype=topical-multi
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / computer and information sciences
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%CAPTCHA%'` -> 1 ; `SELECT p.id, p.acronym, p.title FROM project p WHERE p.objective ILIKE '%CAPTCHA%'` -> 684168 | Excalibur 2.0 | Revolutionary trustworthy platform for seamless authentication of Internet users ; `SELECT COUNT(*) FROM project p JOIN (SELECT DISTINCT projectID id FROM euroSciVoc WHERE split_part(euroSciVocPath,'/',1)='natural sciences' AND split_part(euroSciVocPath,'/',2)='computer and information sciences') b ON b.id=p.id WHERE p.objective ILIKE '%CAPTCHA%'` -> 1
  axes: topic=usable-security; technique=distributed cryptographic scheme, behaviometrics, proximity detection; artefact=cloud password manager
  why: Read 684168: the objective names CAPTCHAs, logins and verification emails as the friction it removes, using the phone as a universal security token - the only corpus objective mentioning CAPTCHA.

- id: vector-36
  topic: Higher-order automation for interactive proof assistants: superposition provers and satisfiability-modulo-theories solvers
  recommend: route=vector level=L1 subtype=topical-multi
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / computer and information sciences
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%satisfiability modulo%'` -> 1 ; `SELECT p.id, p.acronym, p.title FROM project p WHERE p.objective ILIKE '%satisfiability modulo%'` -> 713999 | Matryoshka | Fast Interactive Verification through Strong Higher-Order Automation ; `SELECT COUNT(*) FROM project p JOIN (SELECT DISTINCT projectID id FROM euroSciVoc WHERE split_part(euroSciVocPath,'/',1)='natural sciences' AND split_part(euroSciVocPath,'/',2)='computer and information sciences') b ON b.id=p.id WHERE p.objective ILIKE '%satisfiability modulo%'` -> 1
  axes: topic=automated-theorem-proving; tools=Isabelle, Coq, TLA+ Proof System, Sledgehammer; goal=higher-order superposition and SMT
  why: Read 713999: the objective sets out to enrich superposition and SMT with higher-order reasoning and integrate the provers into Coq, Isabelle and TLA+, and it is the corpus's only 'satisfiability modulo' project.

- id: vector-37
  topic: Program synthesis - automatically producing programs or network configurations from high-level intent
  recommend: route=vector level=L2 subtype=topical-multi
  counts: 2 corpus-wide, 2 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / computer and information sciences
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%program synthesis%'` -> 2 ; `SELECT p.id, p.acronym, p.title FROM project p WHERE p.objective ILIKE '%program synthesis%' ORDER BY p.id` -> 680358 BIGCODE 'Learning from Big Code: Probabilistic Models, Analysis and Synthesis'; 851809 SyNET 'From Network Verification to Synthesis: Breaking New Ground in Network Automation' ; `SELECT COUNT(*) FROM project p JOIN (SELECT DISTINCT projectID id FROM euroSciVoc WHERE split_part(euroSciVocPath,'/',1)='natural sciences' AND split_part(euroSciVocPath,'/',2)='computer and information sciences') b ON b.id=p.id WHERE p.objective ILIKE '%program synthesis%'` -> 2
  axes: topic=program-synthesis; 680358=statistical synthesis from Big Code probabilistic models; 851809=synthesizing router configurations from operator intent
  why: Read both objectives: 680358 proposes statistical program synthesis over probabilistic models of massive codebases, and 851809 explicitly frames automatic network-configuration generation as 'akin to program synthesis'.

- id: vector-38
  topic: Differential privacy as a leakage guarantee in data analytics, genomic privacy and dynamic data structures
  recommend: route=vector level=L3 subtype=topical-multi
  counts: 5 corpus-wide, 3 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / computer and information sciences
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%differential privacy%' OR p.title ILIKE '%differential privacy%'` -> 5 ; `SELECT p.id, p.acronym, p.title FROM project p WHERE p.objective ILIKE '%differential privacy%' OR p.title ILIKE '%differential privacy%' ORDER BY p.id` -> 707135 GenoPri; 726361 IMPROVE; 731583 SODA; 101002277 TypeFoundry; 101019564 MoDynStruct ; `SELECT COUNT(*) FROM project p JOIN (SELECT DISTINCT projectID id FROM euroSciVoc WHERE split_part(euroSciVocPath,'/',1)='natural sciences' AND split_part(euroSciVocPath,'/',2)='computer and information sciences') b ON b.id=p.id WHERE p.objective ILIKE '%differential privacy%' OR p.title ILIKE '%differential privacy%'` -> 3
  axes: topic=differential-privacy; settings=multi-party computation (731583), genomic data (707135), dynamic data structures (101019564); spill=2 members outside the bucket (726361, 101002277)
  why: Read 731583: its objective combines multi-party computation with differential privacy so aggregated results do not leak individual data, and the phrase recurs across five corpus projects in clearly different settings.

- id: vector-39
  topic: The single project studying microfinance / digital lending to unbanked borrowers
  recommend: route=vector level=L1 subtype=topical-single
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: social sciences / economics and business
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%microfinance%' OR p.title ILIKE '%microfinance%'` -> 1 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%microfinance%' OR p.title ILIKE '%microfinance%'` -> 780143 | VillageInvest
  axes: topic=microfinance; region=India; theme=financial inclusion
  why: VillageInvest (780143) is the only project whose text mentions microfinance, and its objective is explicitly about replacing the failing microfinance model with a digital lending platform for unbanked borrowers.

- id: vector-40
  topic: The single project on the anchoring of inflation expectations and risk-adjusted inflation pricing
  recommend: route=vector level=L1 subtype=topical-single
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: social sciences / economics and business
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%inflation expectation%' OR p.title ILIKE '%inflation expectation%'` -> 1 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%inflation expectation%' OR p.title ILIKE '%inflation expectation%'` -> 682288 | INFL
  axes: topic=monetary economics; instrument=inflation-indexed bonds; actor=central bank
  why: INFL (682288) is the only project mentioning inflation expectations, and its objective builds risk-neutral inflation densities and inflation-indexed central-bank liabilities.

- id: vector-41
  topic: The single project on the gender pay gap and gender norms in the labour market
  recommend: route=vector level=L1 subtype=topical-single
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: social sciences / economics and business
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%gender pay gap%' OR p.title ILIKE '%gender pay gap%'` -> 1 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%gender pay gap%' OR p.title ILIKE '%gender pay gap%'` -> 841969 | ORIGENDER
  axes: topic=gender pay gap; region=Scandinavia/Denmark; method=administrative data
  why: ORIGENDER (841969) is the only project whose text mentions the gender pay gap, and its objective is entirely about quantifying how gender norms sustain it.

- id: vector-42
  topic: Projects studying tax evasion (firm networks, tax morale, predatory economies)
  recommend: route=vector level=L2 subtype=topical-multi
  counts: 4 corpus-wide, 2 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: social sciences / economics and business
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%tax evasion%' OR p.title ILIKE '%tax evasion%'` -> 4 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%tax evasion%' OR p.title ILIKE '%tax evasion%' ORDER BY id` -> 693402 ECOSOCPOL; 748062 ChEATAX; 758984 DEVTAXNET; 101026736 AnthroTax ; `WITH b AS (SELECT DISTINCT projectID id FROM euroSciVoc WHERE split_part(euroSciVocPath,'/',1)='social sciences' AND split_part(euroSciVocPath,'/',2)='economics and business') SELECT COUNT(*) FROM project p JOIN b ON b.id=p.id WHERE p.objective ILIKE '%tax evasion%' OR p.title ILIKE '%tax evasion%'` -> 2
  axes: topic=tax evasion; scope=developing countries + cross-cultural experiments
  why: A small, cleanly enumerable set of four projects whose titles name tax evasion or tax morale directly.

- id: vector-43
  topic: Projects on the gig economy - platform work, precariousness and algorithmic management
  recommend: route=vector level=L3 subtype=topical-multi
  counts: 6 corpus-wide, 5 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: social sciences / economics and business
  evidence: `SELECT COUNT(*) FROM project p WHERE p.objective ILIKE '%gig economy%' OR p.title ILIKE '%gig economy%'` -> 6 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%gig economy%' OR p.title ILIKE '%gig economy%' ORDER BY id` -> 833577 REsPecTMe; 837539 Mercurius Connect; 838081 FAIRWORK; 875255 GIGSTATS; 890434 SOJUFOW; 947806 iManage ; `WITH b AS (SELECT DISTINCT projectID id FROM euroSciVoc WHERE split_part(euroSciVocPath,'/',1)='social sciences' AND split_part(euroSciVocPath,'/',2)='economics and business') SELECT COUNT(*) FROM project p JOIN b ON b.id=p.id WHERE p.objective ILIKE '%gig economy%' OR p.title ILIKE '%gig economy%'` -> 5
  axes: topic=gig/platform work; angles=measurement, fair work, employment law
  why: Six projects share the gig-economy theme from distinct angles (statistics tooling, fair-work standards, employment law), giving a genuine multi-project synthesis target.

- id: vector-44
  topic: Thermophilic microbial conversion of captured industrial CO2 into acetone
  recommend: route=vector level=L1 subtype=topical-multi
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / chemical sciences
  evidence: `SELECT COUNT(*) c FROM project p WHERE p.objective ILIKE '%thermophilic microbial%' OR p.title ILIKE '%thermophilic microbial%'` -> c=1 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%thermophilic microbial%' OR p.title ILIKE '%thermophilic microbial%'` -> 101037009 PYROCO2
  axes: subfield=catalysis/CO2-utilisation; scale=industrial demonstrator; funding=largest ecMaxContribution in bucket
  why: Read the objective: PYROCO2 demonstrates 4000 t/yr acetone from 9100 t industrial CO2 via an energy-efficient thermophilic microbial bioprocess, and no other project in the corpus uses that phrase.

- id: vector-45
  topic: Composite solid electrolytes for all-solid-state lithium batteries
  recommend: route=vector level=L1 subtype=topical-multi
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / chemical sciences
  evidence: `SELECT COUNT(*) c FROM project p WHERE p.objective ILIKE '%composite solid electrolyte%' OR p.title ILIKE '%composite solid electrolyte%'` -> c=1 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%composite solid electrolyte%' OR p.title ILIKE '%composite solid electrolyte%'` -> 101021759 CSE-LBATTS
  axes: subfield=electrochemistry; motivation=battery safety and energy density
  why: Read the objective and teaser: CSE-LBATTS designs nanostructured composite solid electrolytes to remove flammable liquid electrolytes from lithium batteries; unique corpus-wide.

- id: vector-46
  topic: Reduced density matrix functionals for relativistic quantum chemistry of heavy elements
  recommend: route=vector level=L1 subtype=topical-multi
  counts: 1 corpus-wide, 1 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / chemical sciences
  evidence: `SELECT COUNT(*) c FROM project p WHERE p.objective ILIKE '%relativistic quantum chemistry%' OR p.title ILIKE '%relativistic quantum chemistry%'` -> c=1 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%relativistic quantum chemistry%' OR p.title ILIKE '%relativistic quantum chemistry%'` -> 891647 ReReDMFT
  axes: subfield=theoretical/computational chemistry; method=RDMFT on four-component Dirac Hamiltonians
  why: Read the objective: ReReDMFT transfers reduced density matrix functional theory to the Dirac equation for near-degenerate heavy-element compounds and implements it in the DIRAC code.

- id: vector-47
  topic: Chiral phosphoric acid organocatalysis and the push past its substrate limits in asymmetric synthesis
  recommend: route=vector level=L2 subtype=topical-multi
  counts: 2 corpus-wide, 2 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / chemical sciences
  evidence: `SELECT COUNT(*) c FROM project p WHERE p.objective ILIKE '%chiral phosphoric acid%' OR p.title ILIKE '%chiral phosphoric acid%'` -> c=2 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%chiral phosphoric acid%' OR p.title ILIKE '%chiral phosphoric acid%'` -> 694228 CHAOS, 752405 DUAL-PHOSCAT
  axes: subfield=organic chemistry/catalysis; property=enantioselectivity
  why: Read CHAOS: it introduces C-H acids precisely because chiral phosphoric acid catalysts are limited to reactive substrates like imines; DUAL-PHOSCAT is the phosphorus-organocatalyst sibling for stereoselective ring-opening polymerisation.

- id: vector-48
  topic: Carbon nitride as a metal-free semiconductor for photoelectrochemical and solar-fuel devices
  recommend: route=vector level=L2 subtype=topical-multi
  counts: 4 corpus-wide, 4 inside the bucket (the level is the corpus-wide count; the question carries no bucket filter)
  bucket: natural sciences / chemical sciences
  evidence: `SELECT COUNT(*) c FROM project p WHERE p.objective ILIKE '%carbon nitride%' OR p.title ILIKE '%carbon nitride%'` -> c=4 ; `SELECT id, acronym FROM project p WHERE p.objective ILIKE '%carbon nitride%' OR p.title ILIKE '%carbon nitride%'` -> 658327 2D Hetero-architecture, 849068 MFreePEC, 101031365 SolTIME, 101022649 METHASOL
  axes: subfield=physical chemistry/photocatalysis; material=metal-free CNX semiconductor
  why: Read MFreePEC: it grows carbon nitride and CNX (X=P,B,S) layers as metal-free PEC semiconductors for solar hydrogen; the other three sit in solar-fuel (SolTIME, METHASOL) and 2D-electronics settings, a tight enumerable cluster.

## Hybrid

10 candidate seeds for `/draft-hybrid-question` (cp2 run), each a **topic x filter** combo whose TRUE survivor count - a real `COUNT(DISTINCT project.id)` re-executed in the merge pass - lands in a drafting window. Subtypes follow the bank's bounds: `filter-read` (L1, |gold|=1, 2-10 survivors), `filter-synthesize` (L2, ~5-20), `filter-compare` (L3, 2-4 contrastable), `filter-survey` (L3, >=5). Spread: all four filter dimensions used (country x3, date-range x2, fundingScheme x4, funding-percentile x1); level mix 3 L1 / 3 L2 / 4 L3; all four subtypes present. Every `evidence` line shows BOTH the topic's unfiltered total and the filtered survivor count, so the filter's pruning power (the structural bar for a hybrid question - projects must exist that satisfy the text but fail the filter) is visible; the textual answer (what the survivors do / found / how) lives only in free text, never in a stored column.

**Load-bearing data note:** `euroSciVocPath` values do NOT begin with a leading slash (verified: `SELECT COUNT(*) FROM euroscivoc WHERE euroSciVocPath LIKE '/%'` -> 0 of 111,614), despite the leading-slash example in schema_docs. Match a subtree with `euroSciVocPath LIKE '%/leaf%'` or an exact `'branch/.../leaf%'` prefix, never `'/branch%'`. All sample ids below were re-queried in the merge pass - the exploration sub-batches mis-paired several acronyms to ids, so the ids here (not the sub-batches') are authoritative.

- id: hybrid-01
  topic: Across Italy-based volcanology projects, what eruption-forecasting and monitoring approaches are developed (monitoring networks, geophysical inversion, unrest/hazard modelling)?
  recommend: route=hybrid level=L3 subtype=filter-survey term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id JOIN organization o ON o.projectID=p.id WHERE e.euroSciVocPath LIKE '%/volcanology%' AND o.country='IT'` -> 15 survivors, vs `... WHERE euroSciVocPath LIKE '%/volcanology%'` -> 62 total. Samples: PICVOLC 793811, EUROVOLC 731070, NEWTON-g 801221, ChEESE 823844, IMPROVE 858092.
  axes: filter=country=IT(any-participant) topic=natural sciences/earth and related environmental sciences/geology/volcanology survivors=15 total=62
  why: The IT filter keeps 15 of the topic's 62 projects (47 satisfy the theme but fail the filter), so the filter is load-bearing, and the forecasting/monitoring methods are described only in each project's free text.

- id: hybrid-02
  topic: Among Sweden-linked graphene projects, what production routes, devices and applications are targeted (flagship cores, nanoplatelet inks, printed electronics)?
  recommend: route=hybrid level=L3 subtype=filter-survey term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id JOIN organization o ON o.projectID=p.id WHERE e.euroSciVocPath LIKE '%graphene%' AND o.country='SE'` -> 18 survivors, vs 290 total for the graphene subtree. Samples: GrapheneCore1 696656, GrapheneCore2 785219, GrapheneCore3 881603, INSPIRED 646155, 1D-Engine 758935.
  axes: filter=country=SE(any-participant) topic=engineering and technology/nanotechnology/nano-materials/two-dimensional nanostructures/graphene survivors=18 total=290
  why: Graphene concentrates in a few flagship hubs (18 of 290 have an SE participant), so the country filter prunes hard, and each survivor's specific graphene application is a free-text answer.

- id: hybrid-03
  topic: What structural-health-monitoring sensing techniques do Spain-based projects use for damage detection (acoustic emission, fibre-optic strain, digital image correlation)?
  recommend: route=hybrid level=L2 subtype=filter-synthesize term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id JOIN organization o ON o.projectID=p.id WHERE e.euroSciVocPath LIKE '%structural health monitoring%' AND o.country='ES'` -> 11 survivors, vs 39 total. Samples: TEST-inn 785393, PANOPTIS 769129, MoniTank 760528, PT-SMS 837131, DOMMINIO 101007022.
  axes: filter=country=ES(any-participant) topic=engineering and technology/civil engineering/structural engineering/structural health monitoring survivors=11 total=39
  why: SHM spans many countries (11 of 39 have an ES participant); TEST-inn confirms the sensor stack is described only in report text, so both the country filter and the text matter.

- id: hybrid-04
  topic: Among viticulture/wine projects that started in 2021 or later, what does a given one tackle (climate-resilient grape growing, canopy robotics, winery-effluent valorisation)?
  recommend: route=hybrid level=L1 subtype=filter-read term_style=paraphrase
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id WHERE e.euroSciVocPath LIKE '%/viticulture%' AND p.startDate>=DATE '2021-01-01'` -> 7 survivors, vs 46 total for viticulture. Samples: CANOPIES 101016906, REDWine 101023567, ORGEVINE 963954, VITALY 101019563, TRACEWINDU 101007979.
  axes: filter=dates=startDate>=2021-01-01 topic=agricultural sciences/agriculture, forestry, and fisheries/agriculture/horticulture/viticulture survivors=7 total=46
  why: Only 7 of 46 viticulture projects start in 2021+, so the date window prunes strongly; survivors say "vineyard/wine/grape" rather than "viticulture" (paraphrase), and the specific aim is a single-project free-text read.

- id: hybrid-05
  topic: Among glaciology projects that started in 2021 or later, what does a given one investigate (deep Antarctic ice cores, ice-core proxies, glacier/ice-sheet dynamics)?
  recommend: route=hybrid level=L1 subtype=filter-read term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id WHERE e.euroSciVocPath LIKE '%/glaciology%' AND p.startDate>=DATE '2021-01-01'` -> 7 survivors, vs 43 total. Samples: DEEPICE 955750, ICEglobe 101023960, HAIL 101024540, IceAq 885891.
  axes: filter=dates=startDate>=2021-01-01 topic=natural sciences/earth and related environmental sciences/physical geography/glaciology survivors=7 total=43
  why: 7 of 43 glaciology projects start in 2021+, so the date filter prunes; DEEPICE echoes "glaciology"/"ice core" verbatim and its scientific goals live only in the report text.

- id: hybrid-06
  topic: What nanophotonic light-control approaches do ERC Starting Grant projects pursue (photonic-mode engineering, metasurfaces, LED/colour conversion, quantum photonics)?
  recommend: route=hybrid level=L3 subtype=filter-survey term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id WHERE e.euroSciVocPath LIKE '%/nanophotonics%' AND p.fundingScheme='ERC-STG'` -> 10 survivors, vs 62 total for nanophotonics. Samples: FLATLIGHT 639109, NANOPHOM 715832, PSINFONI 714151, ENLIGHTMENT 637116, aQUARiUM 802986.
  axes: filter=scheme=ERC-STG topic=engineering and technology/nanotechnology/nanophotonics survivors=10 total=62
  why: The nanophotonics subtree spans MSCA/RIA/ERC (10 of 62 are ERC-STG), so the scheme filter cuts, and what each grant does with nanophotonics lives only in free text.

- id: hybrid-07
  topic: Among SME-instrument phase-1 projects on vine-growing/winemaking, what commercial innovations are pursued (vinification sensing, precision viticulture, wine-quality control)?
  recommend: route=hybrid level=L2 subtype=filter-synthesize term_style=paraphrase
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id WHERE e.euroSciVocPath LIKE '%/viticulture%' AND p.fundingScheme='SME-1'` -> 14 survivors, vs 46 total. Samples: VitiPrecision 2020 674786, Winegrid 832214, ANTOFERINE 815904, Eternum 826866, Eco-Closure 855467.
  axes: filter=scheme=SME-1 topic=agricultural sciences/agriculture, forestry, and fisheries/agriculture/horticulture/viticulture survivors=14 total=46
  why: Viticulture spans SME-1/SME-2/RIA/MSCA (14 of 46 are SME-1), so the scheme filter matters; the interesting content (each SME's wine innovation) is textual, and "viticulture" is paraphrased ("vinification/wine") in the members' text. Pairs with hybrid-04 (same topic, different filter/level) to demonstrate the filter is load-bearing.

- id: hybrid-08
  topic: What research questions do MSCA individual fellowships in musicology investigate (choral traditions, ethnomusicology, cultural policy/sociology of music)?
  recommend: route=hybrid level=L2 subtype=filter-synthesize term_style=paraphrase
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id WHERE e.euroSciVocPath LIKE '%/musicology%' AND p.fundingScheme='MSCA-IF-EF-ST'` -> 13 survivors, vs 86 total for musicology. Samples: OXFORDCHOIRS 707827, Aural Paris 750086, MEMORISING 750706, GRIDAMUS 745631.
  axes: filter=scheme=MSCA-IF-EF-ST topic=humanities/arts/musicology survivors=13 total=86
  why: The musicology umbrella (86) spreads across MSCA-IF/ERC/other schemes, so the ST-fellowship filter cuts it to 13, and each fellowship's subject is a free-text objective; "musicology" is not literal in the members' text (paraphrase).

- id: hybrid-09
  topic: How does a given ERC Consolidator project approach textiles as a subject (weaving history/epistemology, textile patents, fashion, wearable/textile labs)?
  recommend: route=hybrid level=L1 subtype=filter-read term_style=exact-term
  evidence: `SELECT COUNT(DISTINCT p.id) FROM project p JOIN euroscivoc e ON e.projectID=p.id WHERE e.euroSciVocPath LIKE '%/textiles%' AND p.fundingScheme='ERC-COG'` -> 7 survivors, vs 295 total for the textiles subtree. Samples: PENELOPE 682711, INTERACT 648763, RE-FASHIONING 726195, TextileLab 771288, PoliticsOfPatents 819458.
  axes: filter=scheme=ERC-COG topic=engineering and technology/materials engineering/textiles survivors=7 total=295
  why: The textiles subtree sprawls across SME/RIA/IA/MSCA (7 of 295 are ERC-COG), so ERC-COG isolates a distinct 7, and what each does with textiles is purely textual.

- id: hybrid-10
  topic: Among the very-highest-budget superconductivity projects, contrast the application domains and superconductor technologies targeted (wind generators, accelerators, topological/Majorana quantum devices, correlated-electron physics).
  recommend: route=hybrid level=L3 subtype=filter-compare term_style=exact-term
  evidence: `SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY ecMaxContribution) FROM project` -> 6,926,372.79; then `SELECT p.id, p.acronym, ROUND(p.ecMaxContribution) FROM project p JOIN euroscivoc e ON e.projectID=p.id WHERE e.euroSciVocPath LIKE '%/superconductivity%' AND p.ecMaxContribution > 6926372.79 GROUP BY 1,2,3 ORDER BY 3 DESC` -> 4 survivors: HERO 810451 (13.9M, hidden-order/correlated-electron physics), EcoSwing 656024 (10.6M, superconducting wind generator), ARIES 730871 (10.0M, accelerator R&D), NONLOCAL 856526 (10.0M, topological/Majorana superconductivity). Superconductivity subtree = 219 total.
  axes: filter=funding=ecMaxContribution>P95(6.93M) topic=natural sciences/physical sciences/electromagnetism and electronics/superconductivity survivors=4 total=219
  why: The P95 money threshold isolates exactly 4 of 219 superconductivity projects, sharply contrastable application-by-application; the money is only the filter and the answer (what each does with superconductivity) is textual.

## Adversarial

Not yet explored (scoped run "find 15 vector topics", 2026-07-23).

## Ambiguous

Not yet explored (scoped run "find 15 vector topics", 2026-07-23).

## Coverage notes

Regenerated at cp2. Covers the Vector candidates (cp1, vector-01..15), the Hybrid candidates (cp2, hybrid-01..10), and current bank usage (all routes, from `get_bank_questions`).

**Bank state (all routes):** sql = 10 (funding / organisation / country / scheme metadata; sql-08 = machine-learning topic); vector = vec-01 (swarm/collective robotics), vec-02 (bioartificial liver / medical biotechnology), vec-03 (plant anthelmintics / livestock); **hybrid = 0 (empty)**; ambiguous = 0. All 10 hybrid candidates are therefore fresh - no hybrid bank question exists to collide with.

**Hybrid filter-dimension axis (cp2):**
- country x topic - hybrid-01 (IT), hybrid-02 (SE), hybrid-03 (ES)
- date-range x topic - hybrid-04 (startDate>=2021), hybrid-05 (startDate>=2021)
- fundingScheme x topic - hybrid-06 (ERC-STG), hybrid-07 (SME-1), hybrid-08 (MSCA-IF-EF-ST), hybrid-09 (ERC-COG)
- funding-percentile x topic - hybrid-10 (ecMaxContribution > P95)

**euroscivoc branch axis (topic side; second-level FOS field; both routes + bank):**
- natural sciences / earth and related environmental sciences - hybrid-01 (volcanology), hybrid-05 (glaciology); vector-02 (hydrometeorology)
- natural sciences / physical sciences - hybrid-10 (superconductivity)
- engineering and technology / nanotechnology - hybrid-02 (graphene), hybrid-06 (nanophotonics)  [flagged least-covered at cp1 - now covered]
- engineering and technology / civil engineering - hybrid-03 (structural health monitoring); vector-14 (sustainable buildings)
- engineering and technology / materials engineering - hybrid-09 (textiles)  [flagged least-covered at cp1 - now covered]
- engineering and technology / mechanical engineering - vector-03
- engineering and technology / electrical engineering - vector-05
- engineering and technology / environmental engineering - vector-11
- agricultural sciences / agriculture, forestry, and fisheries - hybrid-04, hybrid-07 (viticulture); bank vec-03 (anthelmintics)  [flagged least-covered at cp1 - now covered]
- humanities / arts - hybrid-08 (musicology)  [flagged least-covered at cp1 - now covered]
- humanities / history and archaeology - vector-08
- philosophy, ethics and religion - vector-04
- biological sciences - vector-01, vector-12
- clinical medicine - vector-06, vector-13
- basic medicine - vector-15
- mathematics - vector-09
- computer and information sciences - vector-07; bank vec-01 (swarm robotics) adjacent; sql-08 (machine learning)
- economics and business - vector-10

**Bank topic axes already used (avoid re-seeding):** medical biotechnology / bioartificial organs (vec-02); collective/swarm robotics (vec-01); agricultural anthelmintics + livestock parasitology (vec-03); machine learning (sql-08). No cp1 or cp2 candidate reuses these.

**Entity families:** no project id or acronym appears in more than one candidate across the whole profile (checked in the merge). Project 810451 HERO is classified under both graphene and superconductivity; it was kept only in hybrid-10 and dropped from hybrid-02's sample list to preserve this.

**Width observations (cp2 Hybrid section):**
- Top-level euroscivoc branch "engineering and technology" carries 4 of 10 hybrid candidates (hybrid-02, -03, -06, -09) - above the one-third guide - but they sit in three distinct sub-branches (nanotechnology x2, civil engineering, materials engineering) across three filter dimensions, so this is diversified, not the entity/column clustering the width rule targets. Flagged for review rather than auto-topped-up on a 10-candidate pilot.
- Viticulture appears in two candidates (hybrid-04, hybrid-07) by design - same topic, different filter dimension and level - to exercise "the filter is load-bearing"; no other topic repeats.

**Least-covered axes (drafting's next stops):**
- Hybrid filter dimensions: **funding-percentile x topic** (only hybrid-10) and **date-range x topic** (only 2, both `startDate>=2021` - no end-date or mid-window bands yet); no scheme-family contrasts, no NUTS/region country groupings. These are the first top-up targets for the next hybrid run.
- Topic branches with NO candidate and NO bank question (carried from cp1, still open for both vector and hybrid): chemical sciences (4,331 projects), sociology (3,802), political sciences (1,795), psychology (636), languages and literature (490), law (866). cp1's flagged materials engineering, nanotechnology, arts, and agriculture are now touched by cp2 hybrid candidates.
- SQL, Adversarial, Ambiguous, and Distributions sections remain unexplored (stubs) - the SQL trap inventory and the adversarial absence set are entirely open.

### cp4 (2026-07-26, scope `map=6`)

**What this run covered.** Six euroSciVoc second-level buckets were mapped topically in a single `map=6` pass: medical and health sciences / basic medicine (4,252), social sciences / sociology (3,802), political sciences (1,795), humanities / history and archaeology (1,669), social geography (870), law (866). All six slices returned VERIFIED, `verify-evidence` re-executed 98/98 claims PASS, and `explore-crosscheck` raised no width, entity, near-duplicate or supply flag. The frontier moves from mapped 0/46 to mapped 6/46; 21 buckets remain unexplored.

**Route and level shape of what was produced.** All 18 candidates (vector-16 .. vector-33) are `route=vector`. Level split is L1 2 / L2 6 / L3 10. There are **zero** sql, hybrid, adversarial and ambiguous seeds in this run - by scope, not by finding - so the bank cannot be drawn from this run for any non-vector cell. Two map entries explicitly flag material for kinds nobody was sent to mine: social geography for adversarial seeds (label-vs-content mismatch, 805/870 of a "social geography" bucket sitting under `transport`) and law for ambiguous seeds (doctrinal scholarship vs LEA technology answering the same question differently). Those are the highest-value follow-ups and they need no new region.

**The scoping caveat a drafting session must not skip.** Every `satisfying_count` here is computed *inside its bucket*. Corpus-wide the same string matches more: chimeric antigen receptor 12 -> 20, organ-on-chip 15 -> 24, U-space 14 -> 20, gentrification 7 -> 9, human trafficking 5 -> 6, dendrochronology 2 -> 7, and both L1 seeds lose their uniqueness (`domestic worker` 1 -> 2, `inertial navigation` 1 -> 2) [`SELECT count(DISTINCT id) FROM project WHERE objective ILIKE '%<term>%' OR title ILIKE '%<term>%'`]. The recorded numbers are correct and reproduced; they are simply bucket-scoped, while a vector question carries no tag filter. A drafter must re-derive `|gold|` corpus-wide before fixing a level - vector-19 and vector-31 are the two seeds most likely to change cell, and vector-27 would move L2 -> L3.

**Axis coverage.** The `axes` fields span domain (6 values), theme (18 distinct, no repeats) and a loose `term_style` vocabulary. They touch **no** country, coordinator, funding scheme, funding band, date range or activity type - every seed is a pure topical string match on `title`/`objective`. Nothing here supports a scheme- or date-conditioned question, and nothing draws on `report_text` even though the run measured 98-99% report coverage in five of six buckets.

**Genuinely well covered:** vector L3 thematic-survey seeds in the social-science and humanities buckets. Ten of them, spread over six themes and six buckets, all text-confirmed. That cell does not need another run.

**Gaps this run leaves open:**

- *Cell unserved (route).* 0 of 18 candidates are sql, hybrid, adversarial or ambiguous - `map=6` produced vector-only supply, so every non-vector cell of the allocation is unfed and `## Distributions` remains a stub.
- *Bucket-scoped counts.* All 18 `satisfying_count` values are tag-filtered, but a vector question has no tag filter; corpus-wide the same terms match up to 60% more projects. Downstream cost is incomplete gold - the defect class that punishes the systems that did best. Drafters must re-derive `|gold|` unscoped.
- *Cell unserved (level).* Only 2 L1 seeds, both fragile under unscoped re-derivation, so this run may in practice supply zero usable vector-L1 cells.
- *Material found but not mined.* Social geography's label-vs-content mismatch and law's two-register split were both identified as adversarial/ambiguous seed material inside map entries and neither was mined. Cheapest next yield in the run - no new bucket needed.
- *Axis thin (non-topical).* No candidate uses country, scheme, funding band, date range or activity type; drafting hybrid `filter-*` cells from this run's material is impossible.
- *Evidence source.* No seed uses `report_text` or retrieval pooling; all satisfying sets are lexical ILIKE proxies, so no seed carries evidence bearing on `term_style=paraphrase` - supply is biased toward `exact-term`.
- *Frontier shape.* 21/46 buckets still unexplored, and largest-first remains defensible for width, but the marginal value of a seventh mapped bucket is now below an `adversarial`/`ambiguous` pass over the six already mapped, and below a hybrid pass anywhere - the bottleneck has moved from region coverage to route coverage.

**cp5 (2026-07-26)**

cp5 was a scoped run: `vector=15` only. sql, hybrid, adversarial, ambiguous, distributions and the map quota were skipped by instruction, so nothing below should be read as a finding about those sections - they were not attempted. Three slices returned, all VERIFIED, 84 PASS/NA and 0 FAIL from `verify-evidence`, 15/15 candidates delivered, no width, entity, near-duplicate or supply flags from `explore-crosscheck`.

The run deliberately left the frontier's largest-unexplored order and went after the biggest **mined-but-unmapped** regions instead: computer and information sciences (7,654), economics and business (4,711), chemical sciences (4,331). All three now carry a map entry, so the map moves 6/46 -> 9/46 and three of the corpus's eight largest buckets stop being blank. This was the right call and it should continue: every remaining `unexplored` bucket is small (largest is social sciences / psychology at 636), while five large buckets are still mined-with-no-map - biological sciences 8,057, physical sciences 5,788, electrical/electronic engineering 5,566, environmental engineering 5,178, clinical medicine 4,661 (`SELECT split_part(euroSciVocPath,'/',1)||' / '||split_part(euroSciVocPath,'/',2), COUNT(DISTINCT projectID) FROM euroSciVoc GROUP BY 1 ORDER BY 2 DESC`). Largest-first is still correct, but on the mined-unmapped list, not the unexplored list.

What the 15 seeds can be drafted into: the corpus-wide counts give 9 L1 (satisfying=1), 4 L2 (vector-37=2, vector-47=2, vector-42=4, vector-48=4) and 2 L3 (vector-38=5, vector-43=6). That was the intended L1 weighting and it more than covers the 4 open L1 slots; it means cp5 alone cannot feed an L3 quota - two seeds is the whole supply, and one of them (vector-38, differential privacy) sits right on the L2/L3 boundary at 5.

The `read_first` mechanism worked as designed - each explorer fixed its pre-probe reads by budget/date extremes before any topic probe. It bit hardest on s02, where none of the three pre-probe projects became a candidate. On s03 (chemical sciences) only 1 of 3 pre-probe reads stayed outside the candidate set: PYROCO2 (101037009) and CSE-LBATTS (101021759) both became vector-44 and vector-45. Judged on the ordering, this is not a repeat of cp4 - the reads preceded the probes, so the `about:` was not written from search results. Judged on breadth, chemical sciences is still the thinnest of the three descriptions: its 4,331-project `about:` and two of its L1 seeds rest on the same pair of projects, so a later run should treat that entry's coverage claims as the weakest and re-read it from a different angle before drafting broadly from it.

