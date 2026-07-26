# Horizon Scout corpus profile

## Header

- **Version:** cp3
- **Generated:** 2026-07-24
- **Corpus fingerprint:** 35,389 projects (`SELECT COUNT(*) FROM project`). Dense index `data/processed/index_meta.json`: 190,248 vectors, embedder `bge-base-en-v1.5-f16.gguf`, dim 768, built 2026-07-22T08:53:52Z. euroscivoc classification covers 32,236 of 35,389 projects across 111,614 rows (`SELECT COUNT(DISTINCT projectID), COUNT(*) FROM euroscivoc`).
- **Grounded against schema_docs:** version `sd2`, content_hash `e2696e0f80f5`.

**Run log** (scope, cost, frontier movement - one line per run):

- cp1 (2026-07-23) scope `"find 15 vector topics"`: Vector only, 15 candidates. 2 subagents.
- cp2 (2026-07-23) scope `"pilot hybrid 10"`: Hybrid only, 10 candidates (10 found). 2 subagents, 54 `run_sql`, 6 `get_project_text` calls (~15 projects). Frontier not yet in existence.
- cp3 (2026-07-24) scope `"structural: add the frontier"`: no exploration subagents. Introduced `## Frontier`, `## Corpus map` and `## Structural findings`, built the 46-bucket frontier from the data and back-filled `seeds`/`bank` from the existing candidates and `eval/bank.jsonl`. Frontier established at `mapped 0/46 | mined 18/46`.

**Reading order for a run:** `## Frontier` alone is enough to plan one (it says where we have not been). Read a section's candidates only when you are drafting from them. The whole file is never needed at once.

## Frontier

Where exploration has and has not been. **This is the only section needed to plan a run.**

The denominator is `euroscivoc`, which already partitions the corpus - no taxonomy is invented here. **46 buckets:** 40 named second-level categories (`split_part(euroSciVocPath,'/',2)`, each under exactly one branch), 5 top-level-only paths (one per branch that has depth-1 rows; agricultural sciences has none), and 1 `(unclassified)` bucket for projects with no euroSciVoc row. Verified: `SELECT split_part(euroSciVocPath,'/',1), split_part(euroSciVocPath,'/',2), COUNT(DISTINCT projectID) FROM euroscivoc GROUP BY 1,2` -> 45 rows (40 named + 5 blank), plus `SELECT COUNT(*) FROM project p WHERE NOT EXISTS (SELECT 1 FROM euroscivoc e WHERE e.projectID=p.id)` -> 3,153.

**Caveat, stated because it is real:** a project carries 1-5 euroSciVoc rows, so a project can appear in more than one bucket. This is a cover, not a strict partition, and bucket project-counts therefore sum to more than 35,389. For a coverage checklist that is fine.

**Statuses:** `unexplored` (nobody has been there) -> `mapped` (a `## Corpus map` entry exists - we know what is in there and what it can support) -> `mined` (at least one bank question has been drawn from it). `status`, `seeds` and `bank` are recomputed each run; `map` is carried.

The `bank` column is traced through `gold_project_ids` -> `euroscivoc`, so SQL-route questions with no gold project ids do not appear in it.

| bucket | projects | status | map | seeds | bank |
|---|---|---|---|---|---|
| natural sciences / biological sciences | 8,057 | mined | - | vector-01, vector-12 | vec-01, vec-05 |
| natural sciences / computer and information sciences | 7,654 | mined | - | vector-07 | vec-01 |
| natural sciences / physical sciences | 5,788 | mined | - | hybrid-10 | hyb-03, vec-04, vec-05 |
| engineering and technology / electrical engineering, electronic engineering, information engineering | 5,566 | mined | - | vector-05 | hyb-03, vec-01 |
| engineering and technology / environmental engineering | 5,178 | mined | - | vector-11 | hyb-03, vec-05 |
| social sciences / economics and business | 4,711 | mined | - | vector-10 | vec-05 |
| medical and health sciences / clinical medicine | 4,661 | mined | - | vector-06, vector-13 | vec-02 |
| natural sciences / chemical sciences | 4,331 | mined | - | - | hyb-03, vec-05 |
| medical and health sciences / basic medicine | 4,252 | unexplored | - | vector-15 | - |
| social sciences / sociology | 3,802 | unexplored | - | - | - |
| engineering and technology / mechanical engineering | 3,158 | mined | - | vector-03 | hyb-03, vec-05 |
| (unclassified - no euroSciVoc row) | 3,153 | unexplored | - | - | - |
| natural sciences / earth and related environmental sciences | 2,922 | mined | - | hybrid-01, hybrid-05, vector-02 | hyb-01, vec-05 |
| medical and health sciences / health sciences | 2,679 | mined | - | - | vec-05 |
| engineering and technology / materials engineering | 2,605 | mined | - | hybrid-09 | hyb-03, vec-05 |
| natural sciences / mathematics | 2,097 | mined | - | vector-09 | vec-04, vec-05 |
| agricultural sciences / agriculture, forestry, and fisheries | 1,943 | mined | - | hybrid-04, hybrid-07 | vec-05 |
| social sciences / political sciences | 1,795 | unexplored | - | - | - |
| humanities / history and archaeology | 1,669 | unexplored | - | vector-08 | - |
| engineering and technology / nanotechnology | 1,478 | mined | - | hybrid-02, hybrid-06 | hyb-03 |
| medical and health sciences / medical biotechnology | 1,394 | mined | - | - | vec-02 |
| social sciences / social geography | 870 | unexplored | - | - | - |
| social sciences / law | 866 | unexplored | - | - | - |
| engineering and technology / civil engineering | 844 | mined | - | hybrid-03, vector-14 | vec-05 |
| social sciences / psychology | 636 | unexplored | - | - | - |
| engineering and technology / other engineering and technologies | 633 | mined | - | - | vec-05 |
| humanities / philosophy, ethics and religion | 627 | unexplored | - | vector-04 | - |
| engineering and technology / industrial biotechnology | 613 | unexplored | - | - | - |
| humanities / arts | 552 | unexplored | - | hybrid-08 | - |
| humanities / languages and literature | 490 | unexplored | - | - | - |
| engineering and technology / medical engineering | 472 | unexplored | - | - | - |
| social sciences / other social sciences | 417 | unexplored | - | - | - |
| agricultural sciences / animal and dairy science | 402 | unexplored | - | - | - |
| social sciences / educational sciences | 308 | unexplored | - | - | - |
| engineering and technology / chemical engineering | 288 | unexplored | - | - | - |
| engineering and technology / environmental biotechnology | 286 | unexplored | - | - | - |
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

`mapped 0/46 | mined 18/46 | unexplored 28/46`

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

*No entries yet - the map is new at cp3. The 18 buckets carrying `seeds` in the frontier have candidate blocks in the Vector and Hybrid sections below, but no region description; mapping them is the first job of the next `/explore-corpus` run.*

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
