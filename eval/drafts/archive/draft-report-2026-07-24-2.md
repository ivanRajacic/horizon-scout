# Draft batch - 2026-07-24 (batch 2, hybrid ladder)

Draft-bank-file: eval/drafts/draft-bank-2026-07-24-2.jsonl
Order: 4 hybrid-route slots pre-chosen by the user - 1 x L1, 2 x L2, 1 x L3 (no gap report requested). Subtypes follow the level-bound vocabulary: L1 = filter-read, L2 = filter-synthesize (x2), L3 = filter-survey (filter-compare is already held by hyb-03).
Corpus profile: cp3 f33f150ff077 | schema_docs: sd2 f8c001e8cc8f | index: be84cbad9182 (n_vectors 190248, bge-base-en-v1.5)
Tally: 4 accepted / 0 failed / 0 blocked

ids start at hyb-04: hyb-01 and hyb-03 are in the bank, hyb-02 is a gap left by the pilot batch's abandoned slot. This batch leaves no new gaps; next free hybrid id is hyb-08.

Batch schema check: `validate-bank` on (existing bank + these 4 accepted records) = OK, 21 questions, exit 0. Resulting hybrid coverage would be L1=2, L2=2, L3=2.

Each drafter followed `/draft-hybrid-question` in orchestrated mode over the read-only MCP tools and verified every number by execution. Each draft was then attacked by an independent `question-reviewer` in draft mode, which re-executed the filter SQL, re-read and re-adjudicated every survivor blind, and ran its own out-of-filter discrimination search. Three passed first review; hyb-07 took one rectification round (the cap) and passed re-review.

Pre-flight: `search_corpus("probe", k=1)` returned all four conditions, so no slot was blocked.

## Summary

| id | route/level/subtype | candidate topic | review verdict | decision |
|----|---------------------|-----------------|----------------|----------|
| hyb-04 | hybrid/L1/filter-read (exact-term) | ERC-COG textiles x fast-fashion waste (MYCLOTH) | SOUND | APPROVE / REJECT below |
| hyb-05 | hybrid/L2/filter-synthesize (paraphrase) | MSCA-IF-EF-ST musicology x music and nationhood | SOUND | APPROVE / REJECT below |
| hyb-06 | hybrid/L2/filter-synthesize (exact-term) | graphene x Swedish participant x antibiotic resistance | SOUND | APPROVE / REJECT below |
| hyb-07 | hybrid/L3/filter-survey (exact-term) | volcanology x Italy x monitoring and forecasting | SOUND (after 1 rectification round) | APPROVE / REJECT below |

Axis spread added by this batch: two funding-scheme filters (ERC-COG, MSCA-IF-EF-ST) and two country-participation filters (SE, IT), against the bank's existing start-date (hyb-01) and funding-percentile (hyb-03) filters. Topic buckets: materials engineering (textiles), humanities/arts (musicology - first hybrid question from that branch), nanotechnology (graphene), earth sciences (volcanology).

---

## hyb-04 - SOUND

**Question:** "Among the ERC Consolidator Grant projects classified under textiles, how does the one that sets out to counter the waste created by fast fashion propose to change the way garments are designed and produced?"  (hybrid / L1 / filter-read, term_style exact-term, well-specified)

**Gold + evidence:**

Filter SQL (executed by the drafter, re-executed by the reviewer, same 7 rows both times):

```sql
SELECT DISTINCT p.id, p.acronym
FROM project p JOIN euroscivoc e ON e.projectID = p.id
WHERE e.euroSciVocPath LIKE '%/textiles%' AND p.fundingScheme = 'ERC-COG'
ORDER BY p.id
```

| id | acronym | title |
|---|---|---|
| 648763 | INTERACT | Intelligent Non-woven Textiles and Elastomeric Responsive materials by Advancing liquid Crystal Technology |
| 682711 | PENELOPE | A study of weaving as technical mode of existence |
| 726195 | RE-FASHIONING | Re-fashioning the Renaissance: Popular Groups, Fashion and the Material and Cultural Significance of Clothing in Europe, 1550-1650 |
| 771288 | TextileLab | Race to the bottom? Family labour, household livelihood and consumption in the relocation of global cotton manufacturing, ca. 1750-1990 |
| 819458 | PoliticsOfPatents | Politics of Patents: Re-imagining citizenship via clothing inventions 1820-2020 |
| 101002711 | BODYinTRANSIT | Sensory-driven Body Transformation Experiences On-the-move |
| **101003104** | **MYCLOTH** | **Sustainable Algorithmic Modeling of Personalized Garments** |

Survivor count 7 of the textiles subtree's 295 projects. Gold = [101003104], inside the survivor set, |gold| = 1 as filter-read requires.

Gold passage, verbatim from MYCLOTH's `project.objective` (the project has no published report row, so the objective is the whole evidence):

> "In addition, the "fast fashion" approach of selling large volumes of garments at very low prices and changing collections very frequently leads to significant waste of natural and human resources: according to recent research nearly 50% of all manufactured garments are imminently destined for landfill or incineration. A fundamental, radical change is required to the way clothes are designed, manufactured and delivered to consumers, generating value and attachment via quality instead of quantity and curbing toxic overproduction. The goal of this project is to bring transformative technological advances in geometric modeling and optimization of personalized, custom-fitted and fabricable garments in order to crucially support this change. To reach our goals, we must break away from traditional shape representations and modeling pipelines and develop a dedicated mathematical and algorithmic foundation for digital cloth and garment modeling. Our envisioned theoretical basis of the digital garment shape space will on the one hand facilitate a novel interactive modeling framework to support apparel designers in the creative task of template garment design in a reality-faithful manner, and on the other hand serve as the enabling foundation for automated, algorithmic garment personalization to perfectly fit any human body model. In stark contrast to current practice of standardized confection sizes, our framework will enable on-demand fabrication of custom-tailored clothing while being inclusive of the full diversity range of human shapes."

Rejected survivors, each read in full and independently re-adjudicated by the reviewer (whose IN/OUT matched the drafter's exactly):

- 648763 INTERACT - "The extreme responsiveness of LCs is transferred to a non-woven textile by incorporating the LC in the fiber core, yielding a smart flexible mat with sensory function": smart materials for wearables, no waste motivation.
- 682711 PENELOPE - "there was a significant but tacit contribution of textile technology involved in the advent of science in ancient Greece": epistemology of weaving.
- 726195 RE-FASHIONING - "This study of Renaissance dress offers a better understanding of how fashion developed at popular levels of society in Europe, 1550-1650": the word "fashion" is surface overlap only.
- 771288 TextileLab - "gender divisions of work, households' multiple livelihood strategies, and local consumption patterns ... the continuation and disappearance of textile manufacturing over time and space": labour history.
- 819458 PoliticsOfPatents - "It focuses on clothing patents in Espacenet, the European Patent Office's free online database": citizenship studies.
- 101002711 BODYinTRANSIT - "individualised sensorial manipulation of body perceptions"; fashion design named only as a downstream application.

Non-embedding sweep over all 7 survivors' objective + report (summary/workPerformed/finalResults/teaser) for 'fast fashion', 'landfill', 'overproduction': MYCLOTH hits, all six others empty.

Discrimination counter-examples - projects that satisfy the textual ask but fail the filter, all carrying the same textiles euroSciVoc tag, so it is precisely `fundingScheme='ERC-COG'` that excludes them:

- 646226 Trash-2-Cash (IA) - "textile fibre waste ... solved through design-driven innovation ... decrease landfill volumes and energy consumption".
- 101003906 SCIRT (IA) - "less than 1% of textile waste is recycled into new textile fibres... SCIRT aims to support systemic innovation towards a more circular fashion system".
- 101000632 HEREWEAR (IA) - "creation of an EU market for locally-produced circular textiles and clothing made from bio-based waste".
- Also 825647 REFREAM (RIA), 641942 RESYNTEX (IA), 646133 TCBL (IA), 101000559 New Cotton (IA).
- Found independently by the reviewer on its own reformulation: 885956 Rodinia, "Clothing manufacturing 4.0 - Changing the way we make fashion" - "in a fast fashion world ... Rodinia is an automated and digital clothing production process ... with the potential to eliminate overproduction of clothes" (its lexical rank 1) and 895711 FReSCH - "a transition to a low-carbon fashion industry is impeded by overproduction".

Scoped rank matrix (search over the 7 survivors, k=10, index be84cbad9182), lexical/dense/hybrid/hybrid_rerank: MYCLOTH 2/2/2/1; RE-FASHIONING 1/1/1/2; TextileLab 6/3/3/3; PoliticsOfPatents 5/4/4/4; BODYinTRANSIT 3/null/7/5; INTERACT 4/null/5/7; PENELOPE 7/null/6/6. RE-FASHIONING outranking the gold on surface "fashion" vocabulary means the question is not solved by taking scoped rank 1.

**Reference answer:** "MYCLOTH (Sustainable Algorithmic Modeling of Personalized Garments) is that project: against a fast fashion system in which, by its own account, nearly 50% of all manufactured garments are imminently destined for landfill or incineration, it proposes to abandon traditional shape representations and modeling pipelines and build a dedicated mathematical and algorithmic foundation for digital cloth and garment modeling - a digital garment shape space that both supports apparel designers in reality-faithful template garment design and drives automated, algorithmic garment personalization to fit any human body model. The aim is to replace standardized confection sizes with on-demand fabrication of custom-tailored, custom-fitted clothing covering the full diversity range of human shapes."

**Why this is a good question:** The question discriminates two things at once: a system must apply a scheme + taxonomy filter it cannot get from text (seven ERC-COG textiles projects out of 295 in the subtree), and then read which of those seven actually argues from fast-fashion waste - a theme that dominates the corpus *outside* the filter (Trash-2-Cash, SCIRT, HEREWEAR, RESYNTEX, New Cotton, Rodinia, all IA/RIA), so pure vector retrieval lands on circular-economy consortia rather than on MYCLOTH. It fills the hybrid coverage gaps of a fundingScheme filter (hyb-01 uses a date filter, hyb-03 a funding-percentile filter) and a materials-engineering topic branch. L1/filter-read is honest: exactly one survivor satisfies the textual requirement and the answer is read from that one project's objective.

**Drafting history:**

- Drift check: the candidate's filter SQL (corpus profile cp3, candidate hybrid-09) re-executed to the same 7 survivors, no drift.
- The candidate's framing ("how does a given ERC-COG project approach textiles as a subject") was too open for an L1 single-fact read, so the drafter sharpened the textual ask to the fast-fashion-waste motivation, which exactly one survivor carries.
- Concern raised and resolved during drafting: a predicate as specific as PENELOPE's "penemorphism" or PoliticsOfPatents' "Espacenet" would have made the filter decorative, since the text alone would identify the project corpus-wide. The fast-fashion-waste predicate was chosen precisely because it is a crowded corpus theme outside the filter.
- Reviewer attacks executed: filter re-run (same 7 ids); all six non-gold survivors re-read and independently adjudicated before comparing (match); every reference clause traced to the live objective; `report: null` confirmed; ERC-COG confirmed as the sole Consolidator code (`fundingScheme LIKE '%ERC%'` -> STG/COG/ADG/POC/POC-LS/SyG/LVG); the alternate free-text reading of "classified under textiles" was executed and collapses to a dead end (it drops MYCLOTH and contains no project answering the fast-fashion clause, so it yields no competing answer); staleness checks matched on both schema_docs and index.
- Reviewer MINOR flags, recorded not rectified: (1) partial column leak - MYCLOTH's `keywords` ("garment modeling, garment fabrication, digital fabrication") and title sketch the answer's shape though not its substance; (2) mild telegraph inherent to filter-read - "the one that sets out to..." asserts the count is exactly 1, true here and the same convention as hyb-01; (3) structural echo of hyb-01's sentence frame, different topic/filter/gold; (4) the `notes` rank-matrix detail is phrasing-dependent - under the reviewer's literal-question run MYCLOTH is dense rank 1, where the notes say dense 2.

Decision: [ ] APPROVE  [x] REJECT

---

## hyb-05 - SOUND

**Question:** "Among the Marie Skłodowska-Curie standard European individual fellowships (funding scheme MSCA-IF-EF-ST) classified under musicology, what do those examining how music helped shape a country's sense of nationhood set out to investigate?"  (hybrid / L2 / filter-synthesize, term_style paraphrase, well-specified)

**Gold + evidence:**

Filter SQL (executed by the drafter, re-executed by the reviewer, same 13 rows):

```sql
SELECT DISTINCT p.id FROM project p JOIN euroscivoc e ON e.projectID = p.id
WHERE e.euroSciVocPath LIKE '%/musicology%' AND p.fundingScheme = 'MSCA-IF-EF-ST'
ORDER BY p.id
```

Survivors (13, all confirmed MSCA-IF-EF-ST): 656349 IPBMNES, **659468 Transnational Localism**, 661734 PE4PPI, 707827 OXFORDCHOIRS, **745631 GRIDAMUS**, **750086 Aural Paris**, 750618 VRAASP, 750706 MEMORISING, 752884 INTIMAL, 792150 LadinoProverbs, 800280 DiCrEd, 844238 ClassRockED, 867427 DUEL. Musicology overall spans 86 projects, so the scheme half of the filter does the cutting. The reviewer additionally verified the LIKE is complete: only three musicology paths exist (`humanities/arts/musicology` 51, `.../ethnomusicology` 20, `.../popular music studies` 17) and all match `%/musicology%`, so no musicology project escapes the filter.

Gold = [659468, 745631, 750086], all inside the survivor set, |gold| = 3 inside filter-synthesize's [2,4].

GOLD 1 - 659468 Transnational Localism, "Transnational Localism and Music after the two World Wars: the case of Francis Poulenc" (objective, verbatim):

> "This project looks at the role composers played in the construction of European culture in the aftermath of two World Wars. Taking Francis Poulenc as an example of a French composer who experienced war twice, it looks at his creative responses to the wars. It prioritises the musical and cultural significance of localised urban, suburban and rural places in shaping a distinctive musical and national identity, an identity that was recognised by his contemporaries as representing a generation; it also scrutinises his international activities in pursuit of cultural and artistic co-operation, collaboration and exchange. The project includes a study of Poulenc's UK connections, using understudied archival materials to explore his collaborations with composers such as Britten and Lennox Berkeley, his presence in concert life and his clandestine WWII activities with the BBC. ... This project responds to this challenge by exploring the role of music in shaping identities on individual, generational, national and European levels."

GOLD 2 - 745631 GRIDAMUS, "Greek Identity in Art Music since the Early Nineteenth Century: Towards an Interdisciplinary Methodology" (objective, verbatim):

> "This project is the first large-scale, interdisciplinary attempt to study mechanisms of national identity construction through modern Greek (post-1830) art music. It has two primary objectives: a) to develop an interdisciplinary methodological framework for the analysis of Greek art music drawing on historical musicology, Modern Greek Studies and ethnomusicology; and b) to offer a revisionist study of Greek art music - a repertory that remains to be investigated in the depth it merits - by exploring the ways in which it has mediated Greek identity during the period since the formation of the Greek nation-state in the early 19th century. Traditionally, the issue of 'Greekness' - a perceived national character or identity - in music has been analysed with respect to the question of the assumed 'continuity' of Greek history and culture since antiquity... Yet, such treatment of national identity is essentialising and self-exoticising."

(report finalResults, verbatim): "GRIDAMUS is an important contribution to scientific enquiries that have challenged the understanding of national identity and, more generally, Greek identity as a monolithic and atemporal entity."

GOLD 3 - 750086 Aural Paris, "Aural Paris: The Changing Identities of The City of Sound in Music, Film and Literature, 1870-1940." (objective, verbatim):

> "This project examines the creative assimilation of the city of Paris into the music, film and literature of the French Third Republic (1870-1940). ... the study, which analyses how singer/songwriters, composers, authors and filmmakers -such as Victor Fournel, Aristide Bruant, Jean Cocteau, Gustave Charpentier, René Clair and Pierre Mac Orlan- used the city and its sounds as creative force and political metaphor. ... The project allows us to re-evaluate the politics of the city soundscape and its role in defining French identity."

(report summary, verbatim): "The changing landscape of the city is the background to this project, which considered how the sonic fabric of the urban context was conceptualised as a defining characteristic of Frenchness."

Survivors that fail the textual requirement (10 of 13; the reviewer re-read all 13 independently and reproduced this split exactly): 656349 IPBMNES (pedestrian flow modelling and evacuation simulation - no music at all, euroSciVoc tag noise); 661734 PE4PPI (peer ethnography, young people's sexual health services); 707827 OXFORDCHOIRS - the near miss - "collective identities and (sometimes contested) cultures of collegiate choirs at University of Oxford", with gender/admissions/socio-economic strands and no national-identity claim anywhere; 750618 VRAASP (archaeoacoustics and VR of ancient spaces); 750706 MEMORISING (rock-art acoustics and group memory); 752884 INTIMAL (telematic "relational listening" with nine Colombian migrant women); 792150 LadinoProverbs (paremiology of Sephardic manuscripts - verbal, not musical); 800280 DiCrEd (digital critical edition of Verdi's French Macbeth, no national argument); 844238 ClassRockED (social class in a US Midwest rock music school); 867427 DUEL (poetic duelling as intangible heritage).

Filter-discrimination counter-examples - satisfy the text, fail the filter. Wording-independent SQL sweep (executed):

```sql
SELECT DISTINCT p.id, p.acronym, p.fundingScheme, p.title
FROM project p JOIN euroscivoc e ON e.projectID=p.id
WHERE e.euroSciVocPath LIKE '%/musicology%' AND p.fundingScheme <> 'MSCA-IF-EF-ST'
  AND (lower(p.objective) LIKE '%national identity%' OR lower(p.objective) LIKE '%nationalism%'
       OR lower(p.objective) LIKE '%patriotic%' OR lower(p.objective) LIKE '%nation-state%')
```
-> 2 rows: 833366 CLEFNI (MSCA-IF), 101018743 Transopera (ERC-ADG).

- CLEFNI 833366 (objective): "During the 19th century, several music and choral societies arose in Europe, giving rise to a choral movement that fostered not only communal singing, but also patriotic feelings. In Switzerland, this movement involved several men's choirs that had a strong liberal and patriotic character. ... With its songs and festivals, the Swiss choral movement became a crucial element in this integration process."
- Transopera 101018743 (report summary): "our work challenges conventional and pre-conceived ideas about music as being organised principally according to national categories. Showing how music, during the age of nationalism, served building bridges between national and religious communities..."
- Unscoped retrieval on the final wording also returned MUSICOL 834195 (ERC-ADG, "the musical dimension of the French empire ... colonial identities") and EURJAZZ 800242 (MSCA-IF, "the role of music in shaping identities at European level").
- The reviewer's own unscoped reformulation added MESG 758221, MEFCPFS 101022942 ("states' foreign cultural policies ... new definitions of national identities"), LA Music Invention 101028215 and PoPoliBB 101023328 ("British national identity is articulated through the genre of country music") - none survives the filter.

Scoped rank matrix (final wording, k=10, scope = the 13 survivors, index be84cbad9182), lexical/dense/hybrid/hybrid_rerank: 659468 **2/1/1/1**; 844238 ClassRockED 1/5/3/3; 707827 OXFORDCHOIRS 4/2/2/6; 745631 **5/3/4/2**; 750086 **3/4/5/4**; 792150 10/-/-/5; 750618 6/6/7/10; 752884 7/7/6/8; 800280 8/-/9/7; 750706 -/8/8/-; 867427 9/-/10/9.

Term-style check (executed SQL over the 13 survivors): `lower(objective) LIKE '%nationhood%'` = 0 hits, `lower(summary||teaser) LIKE '%nationhood%'` = 0 hits; `lower(objective) LIKE '%national identity%'` = 2 hits (659468, 745631). The question's key phrase is absent from every survivor text, so `paraphrase` is honest.

**Reference answer:** "Transnational Localism (659468) takes Francis Poulenc, a French composer who lived through both World Wars, as its case for asking how localised urban, suburban and rural places fed a distinctive musical and national identity, following his UK collaborations with Britten and Lennox Berkeley, his clandestine WWII activities with the BBC, and post-WWI European-American artistic exchange. GRIDAMUS (745631) sets out to build an interdisciplinary framework - historical musicology, Modern Greek Studies and ethnomusicology - for a revisionist reading of modern Greek art music since the formation of the Greek nation-state in the early 19th century (post-1830), treating 'Greekness' as something the repertory mediates rather than an essential, timeless trait. Aural Paris (750086) examines how composers, singer/songwriters, authors and filmmakers of the French Third Republic (1870-1940) - among them Aristide Bruant, Jean Cocteau, Gustave Charpentier and René Clair - turned the city's sounds into creative force and political metaphor, re-evaluating the politics of the city soundscape and its role in defining French identity. All three work outward from archival sources on one composer, repertory or city, and treat music as a place where a nation's self-image is assembled and contested rather than merely reflected."

**Why this is a good question:** It discriminates on a property no column stores - whether a musicology fellowship treats music as a maker of national self-image - and only 3 of the 13 enumerated survivors qualify, with a genuine near-miss (OXFORDCHOIRS, collegiate rather than national identity) that punishes topic-level matching. It fills three open axes at once: the first humanities/arts topic in the hybrid ladder, the first funding-scheme filter on that route, and the first hybrid filter-synthesize/paraphrase cell. L2/filter-synthesize is honest because |gold| = 3 sits mid-bound and the answer is one integrated statement across three fellowships; paraphrase is honest because "nationhood" appears nowhere in any survivor's text while the gold texts say "national identity", "Greekness" and "Frenchness".

**Drafting history:**

- Drift check: the candidate's (cp3 hybrid-08) count SQL re-executed to 13 survivors; all 13 enumerated and read.
- Exhaustive read confirmed the euroSciVoc noise the profile warned about: IPBMNES (pedestrian modelling) and PE4PPI (sexual-health ethnography) carry the musicology tag but are not music research.
- Theme selection: three candidate textual requirements were visible in the survivor texts - archaeoacoustics of ancient sites (|gold| = 2), oral/intangible heritage (weakly coherent), and music as a maker of national self-image (3 survivors). The last was chosen for its mid-bound gold count, its sharp in-filter near-miss, and its out-of-filter counter-examples.
- One SQL parser error (misplaced parenthesis in a `CASE WHEN` term-style probe) was fixed and re-run, giving the 0/13 "nationhood" result.
- The question wording was edited after the first retrieval pass (explicit scheme code added; "forge" -> "shape"; "study" -> "investigate"), so all retrieval verification was re-run against the exact final wording, and the filter-discrimination claim was additionally grounded in the wording-independent SQL sweep above. The recorded `filter_sql` was executed once more at the end and returned the same 13 ids.
- Reviewer attacks executed: filter re-run (same 13); completeness of the musicology LIKE verified across all three musicology paths; all 13 survivors independently re-read and re-adjudicated (IN set reproduces the gold exactly, no wrong IN, no wrong OUT); every reference entity, date and claim traced to fetched text, including "all three work outward from archival sources"; both readings of the fellowship phrase tested (the parenthetical scheme code pins it, so the MSCA-IF vs MSCA-IF-EF-ST confusion cannot be read in); own unscoped reformulation used for the discrimination check; staleness matched on schema_docs and index.
- Reviewer MINOR flags, recorded not rectified: (1) filter-reading tension - the narrower `euroSciVocTitle='musicology'` leaf-only reading is runnable and gives 5 survivors, dropping GRIDAMUS (tagged only `.../musicology/ethnomusicology`), so a system that writes the leaf filter loses one of three golds; the reviewer judges "under" to mean the branch, making this difficulty rather than ambiguity; (2) Aural Paris is the weakest gold - a city-soundscape frame whose nationhood claim rests on "its role in defining French identity" and "a defining characteristic of Frenchness" - honest but marginal next to the two explicit national-identity projects; (3) tagging is noisy in both directions - ERIN 658376 (MSCA-IF-EF-ST, "the cultural articulation of national identity in 19th-century Europe as found in the musical works of ... Thomas Moore") satisfies the textual requirement but is tagged databases/history/digital humanities and is therefore correctly excluded by the filter as written, though a solver reasoning semantically about "musicology" would surface it.

Decision: [ ] APPROVE  [x] REJECT

---

## hyb-06 - SOUND

**Question:** "Among graphene projects that include a Swedish participant, how do the ones tackling antibiotic-resistant bacterial infections make use of graphene?"  (hybrid / L2 / filter-synthesize, term_style exact-term, well-specified)

**Gold + evidence:**

Filter SQL (executed by the drafter, re-executed by the reviewer, same 18 rows):

```sql
SELECT DISTINCT p.id FROM project p
JOIN euroscivoc e ON e.projectID=p.id
JOIN organization o ON o.projectID=p.id
WHERE e.euroSciVocPath LIKE '%graphene%' AND o.country='SE'
```

Survivors (18, of the graphene subtree's 290 projects): 641416 iPUBLIC, 646155 INSPIRED, 650029 TAIPI, 686135 PROCETS, **690836 PANG**, 696656 GrapheneCore1, 721991 GreenCarbon, 758935 1D-Engine, 764977 mCBEEs, 785219 GrapheneCore2, 810451 HERO, 824962 Car2TERA, 881603 GrapheneCore3, 952792 2D-EPL, **955626 PEST-BIN**, 101002772 SPINNER, 101006963 GreEnergy, 101017186 AEOLUS.

The reviewer probed the bare `LIKE '%graphene%'`: `SELECT DISTINCT euroSciVocPath ... LIKE '%graphene%'` returns exactly ONE path, `engineering and technology/nanotechnology/nano-materials/two-dimensional nanostructures/graphene` (290 projects), so the wildcard matches nothing unintended.

Gold = [690836, 955626], both inside the survivor set, |gold| = 2 inside filter-synthesize's [2,4].

Survivor-scoped SQL sweep over objective AND report_text (teaser||summary||workPerformed||finalResults) for bacter/antimicrob/antibiotic/pathogen/infection/biofilm: PANG true/true, PEST-BIN true/true, all sixteen others false/false. The reviewer ran its own wider 10-term sweep (`microb|bacter|infect|antibiotic|biomedic|wound|health|sepsis|amr|pathog`) and reached the same two, adjudicating the three soft-term hits OUT on their text: 764977 mCBEEs ("TiO2 nanotubes ... decorated with either Cu, Zn or Ag nanoparticles to increase osteointegration and reduce infection risks" - no graphene, no resistance), 881603 GrapheneCore3 (biomedical work is "graphene-based bioelectronic vagus nerve therapies"), 785219 GrapheneCore2 (no infection context).

GOLD 1 - 690836 PANG, "Pathogen and Graphene" (countries SE,ES,TR,FR,DE,UA):

- objective: "Among the various approaches, the use of graphene and its derivatives is currently considered a highly promising strategy to overcome microbial drug resistance. ... we respond in this consortium by exploring the utility of novel graphene based nanocomposites for the management and better understanding of microbial infections. The anti-microbical potential of the novel graphene based nanomaterials, the possibility of using such structures for the development of non-invase therapies together with the understanding of the mechanism of action will be the main focal points of the proposed project entitled 'PANG', relating to Pathogen and Graphene."
- report summary: "Objective I: Development of graphene-based antibacterial matrixes though chemical functionalization of these structures using antibacterial peptides and molecules (e.g. menthol and others) and test them for their toxicity and bactericidal potential (in particular against AMR strains) / Objective II: Use of the novel architectures in form of suspensions and transdermal patches for the killing of pathogens via non invasive photothermal therapy taking advantage of the good photothermal properties of rGO / Objective III: To get a deeper understanding of the effects of the novel structures on the immune system / Objective IV: To develop prototypes of antibacterial graphene matrixes and antibacterial transdermal patches with the SMEs involved in the project"
- report workPerformed: "1. Development of a flexible skin patch allowing a rapid and highly efficient treatment of subcutaneous wound infections via photothermal irradiation."

GOLD 2 - 955626 PEST-BIN, "Pioneering Strategies Against Bacterial Infections" (2 SE organisations; countries DE,HR,SE,IT,DK,FR,ES):

- objective: "1) Diagnostics: ... PEST-BIN will develop infection diagnostic kits based on graphene, that will be functionalized by receptors capturing infection biomarkers. Our chips will contain only pure carbon and biodegradable polymers - zero environmental footprint. They will be used as 'plug-and-play' disposable chips with a micro-SD jack. ... 3) Killing biofilms: ... PEST-BIN will engineer magnetic nanoparticles (directed by magnetic field), spiked with antibacterial graphene coating which will be loaded with antibiotics. Such molecular 'nano-weapons' will physically penetrate biofilms and ensure sustained delivery of antibiotics inside biofilms."
- report finalResults: "PEST-BIN is going beyond state-of-the-art by functionalizing graphene-based sensors with receptors specifically designed to capture biomarkers from the bacterial surface ... We delivered the promised technology, protected the IP rights, and actually started producing the diagnostic chips in the PEST-BIN spin out LayerLogic AB. ... We have engineered various hydrogel/polymer coatings involving 'green' nanoparticles and antibacterial graphene coatings. Such coatings, when loaded with antibacterial molecules from Naicons, become a very effective 'nano-weapon' against bacterial biofilms."

Discrimination counter-examples - satisfy the textual ask, fail the filter (each SQL-confirmed to have zero Swedish organisations):

- 664782 VANGUARD (IT only) - "The project demonstrated novel antibacterial scaffolds made by graphene for coating of medical devices. ... antibacterial cloak by laser printing of graphene oxide hydrogels. We observe up to 90% reduction of bacteria cells."
- 966720 GRAPHFITI (NL only), "Graphene to Fight Antimicrobial Resistance" - graphene membranes for antimicrobial susceptibility testing. On the reviewer's own unscoped search this project is rank 1 in all four conditions.
- 753636 NOVA (UK only), "Novel Antimicrobial Graphene Oxide-Lanthanide-Hydroxyapatite Composites as Therapeutic Materials in Bone Infection and Repair".
- Added by the reviewer: 802093 ENIGMA (graphene drums for antibiotic susceptibility testing), also outside the filter.

Scoped rank matrix (final wording, scope = the 18 survivors, k=10, index be84cbad9182): PANG **1/1/1/1**, PEST-BIN **2/2/2/2** across lexical/dense/hybrid/hybrid_rerank. Nine further survivors surfaced and were rejected on their text. The reviewer's independent scoped reformulation ("killing drug-resistant bacteria and treating infections with nanomaterial coatings") returned PEST-BIN 1/1/1/1 and PANG 2/2/2/2, with all next-ranked survivors off-topic on their best chunks.

**Reference answer:** "Of the 18 graphene-classified projects with a Swedish participant, two attack antibiotic-resistant bacteria, and each puts graphene to a different use. PANG (Pathogen and Graphene) builds graphene-based antibacterial matrixes by chemically functionalising them with antibacterial peptides and molecules such as menthol, then exploits the photothermal properties of rGO in suspensions and transdermal patches - among them a flexible skin patch that treats subcutaneous wound infections under photothermal irradiation - to kill pathogens non-invasively, while also probing the effect of these structures on the immune system and their mechanism of action. PEST-BIN uses graphene on two fronts: infection diagnostic kits built as disposable 'plug-and-play' chips of graphene functionalised with receptors that capture bacterial-surface biomarkers, containing only carbon and biodegradable polymers and now produced by the spin-out LayerLogic AB; and antibacterial graphene coatings on magnetic nanoparticles and hydrogel/polymer coatings, loaded with antibiotics so that the resulting nano-weapon physically penetrates bacterial biofilms and delivers the drug inside them. Both therefore rely on graphene as an intrinsically antibacterial, functionalisable surface, PANG for light-triggered killing on the skin and PEST-BIN for sensing plus biofilm eradication."

**Why this is a good question:** It discriminates a system that can intersect a structured population filter (graphene subtree x any Swedish participant, 18 of 290) with a narrow free-text requirement and then synthesise across the two survivors that meet it - a system that ignores the filter pulls in GRAPHFITI, VANGUARD and NOVA, which are textually perfect but Dutch, Italian and British, and GRAPHFITI in particular is rank 1 unscoped. It adds a country-participation filter on a nanomaterials topic that no bank hybrid question uses. L2/filter-synthesize is honest: |gold| = 2 sits inside [2,4] and the answer is one integrated statement about graphene's role that neither project's text yields alone.

**Drafting history:**

- Deliberate re-cast: candidate hybrid-02 (cp3) recommends L3/filter-survey, but this slot was assigned L2/filter-synthesize and the cell was binding. The drafter kept the graphene x SE filter unchanged and searched the survivors' own text for a requirement narrow enough to leave 2-4 gold. A printed-electronics/inkjet-ink angle (iPUBLIC, INSPIRED, plus possible Flagship and 2D-EPL overlap) was considered and rejected as likely to exceed 4 gold once Flagship reports were counted; the antibacterial angle was chosen instead.
- Drift check: the candidate's filter SQL re-executed to the same 18 survivors; all 18 objectives read.
- Gold-bound risk closed by execution rather than assumption: because the three GrapheneCore umbrella objectives are generic and could hide antibacterial work packages, the drafter ran the survivor-scoped LIKE sweep across objective AND all four report sections. Exactly 2 survivors hit, so |gold| = 2 is safe and no Flagship project qualifies.
- Filter-discrimination check passed first attempt; the counter-examples were then confirmed by SQL to have zero SE organisations, so the claim is executed rather than inferred.
- Reviewer attacks executed: filter re-run (same 18) plus the euroSciVocPath wildcard probe; an independent wider keyword sweep over all 18 survivors; an independent scoped pooled reformulation; every reference clause traced to live text (18 count, both projects' objectives and reports); two alternate readings run - strict `role='participant'` (survivors 18 -> 15, both gold SE orgs are literally role='participant', the three dropped projects hit no infection term, same answer) and a text-based rather than tag-based sense of "graphene projects" (5 SE projects mention graphene and a bacteria term: the two gold plus 634415 PoC-ID, 862100 NewSkin, 949012 DeepProton, none tackling antibiotic-resistant infections, same answer); staleness matched on schema_docs and index.
- Reviewer MINOR flags, recorded not rectified: (1) template echo with hyb-03's phrasing mould, different topic/filter/level/subtype so no content duplication; (2) PEST-BIN's diagnostics front targets bacterial infection detection generally rather than resistant strains specifically, its resistance framing sitting in the opening ("WHO classified antibiotic resistance as one of the greatest threats") and the biofilm front - an honest gold member, slightly wider than the question's exact phrase; (3) scope is tag-based while a user may read "graphene projects" textually - verified non-scoring above but worth recording; (4) "the ones tackling" mildly signals plurality, with no count or content leak.

Decision: [x] APPROVE  [ ] REJECT

---

## hyb-07 - SOUND (after 1 rectification round)

**Question:** "Across the Horizon 2020 projects classified under volcanology that include at least one organisation based in Italy, what kinds of approaches for monitoring volcanoes and forecasting eruptions are being developed?"  (hybrid / L3 / filter-survey, term_style exact-term, well-specified)

**Gold + evidence:**

Filter SQL (executed by the drafter, re-executed by the reviewer, and re-executed again after the wording fix - the same 15 rows every time):

```sql
SELECT DISTINCT p.id FROM project p
JOIN euroscivoc e ON e.projectID = p.id
JOIN organization o ON o.projectID = p.id
WHERE e.euroSciVocPath LIKE '%/volcanology%' AND o.country = 'IT' ORDER BY p.id
```

Survivors (15): **653980 ARISE2**, 658591 CIAO, 674907 EVER-EST, 714936 TRUE DEPTHS, **731070 EUROVOLC**, **749249 VULCAN.ears**, 765710 INSIGHTS, **793811 PICVOLC**, **801221 NEWTON-g**, **823844 ChEESE**, 845115 ICELEARNING, **858092 IMPROVE**, **863220 SiC nano for PicoGeo**, 101024337 PUSKURUM, 101025887 ENDGAME.

Topic total without the country condition: `SELECT COUNT(DISTINCT projectID) FROM euroscivoc WHERE euroSciVocPath LIKE '%/volcanology%'` -> 62, so the filter removes 47. The reviewer confirmed only one euroSciVoc path contains "volcan", so the LIKE cannot over-match.

Gold = 8 projects, all inside the survivor set, |gold| = 8 >= filter-survey's floor of 5.

Gold passages, verbatim:

- **653980 ARISE2** - report/finalResults: "The Volcano Information System (VIS) implemented in the ARISE platform and presently calibrated using Etna monitoring will be very relevant for remote monitoring of non-instrumented volcanoes. The ARISE team demonstrated the possibility to predict the ash plume height and mass eruption rate based on the infrasound signals associated to the eruptions with a large impact for the aviation security." report/workPerformed: "The proof of concept and prototype of the VIS (Volcano Information System) have been achieved. Near-real time notification of the Volcano Infrasound Early-Warning (VIEW) system are sent to the ARISE portal".
- **731070 EUROVOLC** - objective: "connecting still isolated volcanological infrastructures located at in situ volcano observatories (VO) and volcanological research institutions (VRIs) ... Joint research activities include production of services to initialize volcanic ash transport and dispersal models during eruptions, integrated modelling of pre-eruption data, and a complete catalogue of European Volcanoes ... virtual access to various modelling and assessment tools for responding to volcanic unrest and eruptions will be offered."
- **749249 VULCAN.ears** - objective: "The aim of this proposal is to build an automatic VSR system focused on recognising events in unsupervised scenarios, robust enough to be integrated into the VM centre of any volcano, allowing online risk assessment by real-time seismicity analysis. It will be based on state-of-the-art VSR technologies: a) class description by statistical means (structured Hidden Markov Models) and b) Parallel System Architecture (PSA-VSR) ... the system will be integrated into several VM scenarios and eruption forecasting tools".
- **793811 PICVOLC** - report/workPerformed: "I performed a first land gravity campaign of the edifice of Nevado del Ruiz with measurements collected up to about 5000 m altitude in collaboration with the Colombian Geological Survey - Volcanological and Seismological Observatory of Manizales (OVSM). This campaign laid the foundations for a future set up of a geodetic network for the monitoring of temporal micro-gravity changes of the volcano ... With the collaboration of the group of Dr. Pasquale de Gori and Dr. Claudio Chiarabba (INGV - Rome) I developed a new up-to-date seismic tomography of the Nevado de Ruiz area and obtained a high-resolved model of the distribution of elastic material properties that was used to build a robust physics-based 3D FEM inverse model of the volcano." objective: "integrates seismic, gravity, and deformation data with 3D numerical inversion to create a detailed representation of the source of volcanic unrest".
- **801221 NEWTON-g** - report/workPerformed: "activities carried out under NEWTON-g included (a) completion of the field deployments with the installation of 4 MEMS gravimeters at Mt. Etna (D4.4); (b) evaluation of the performance of the AQG-B installed at Mt. Etna in 2020 ... We fully demonstrated that the AQG-B provides continuous gravity data suitable for volcano monitoring purposes ... Even though, mostly because of COVID-19-related delays, it was not possible to make the original idea of 'gravity imager' came completely true, we performed field tests with the MEMS gravimeters that have provided crucial information for further upgrades."
- **823844 ChEESE** - report/workPerformed: "Urgent computing during La Palma eruption. The PD12 ran operationally (TRL=9) during the eruption. Ensemble-based (scenarios) ash dispersal forecasts ran @MN4 from 19 Nov to 13 Dec 2021 and delivered daily (at 8:00 am LT) to the scientific committee of the PEVOLCA and to the civil protection authorities for real operational decision-making". report/finalResults: "Volcanic ash dispersal forecasts at unprecedented resolution (few km) based on satellite data assimilation and ensemble forecast."
- **858092 IMPROVE** - report/summary: "It produced new methods to image magma storage zones and to link seismic, geodetic, and geochemical observations across scales. It also created prototypes for advanced gas and steam monitoring devices, demonstrated new uses of fibre-optic sensing for volcano monitoring". report/finalResults: "IMPROVE pioneered new techniques for subsurface imaging, combining seismic, geodetic, gravity, and geochemical data with machine learning and numerical modelling ... Improved understanding of subsurface processes directly enhances eruption forecasting and volcanic hazard assessment." report/workPerformed: "an automated gas-steam ratio sensor and applications of distributed acoustic sensing for geothermal and volcanic monitoring."
- **863220 SiC nano for PicoGeo** - report/finalResults: "The outcome of the proposed technology will be an optical fibre strain sensor with picostrain resolution (10-12), measurement band 0-100 Hz ... The proposed device will be constituted by a vacuum-encapsulated SiC (silicon carbide) thin membrane resonator mounted on the termination of a multimode optical fiber". report/workPerformed: "In the first year of the project the participants have developed the process of SiC growth of thin film on Si (111) and Si (100) ... the first drilling both for the strain-meters and the seismometer installations has been performed before the winter." objective: "Ultra small and slow strain transients preceding earthquakes and eruptions could be revealed ... direct implications in forecasting volcanic eruptions".

Rejected survivors (7; the reviewer re-read all 15 independently and reproduced the same 8 IN / 7 OUT split): 658591 CIAO ("amphibole-bearing peridotites of Nain ophiolite (Iran)" - mantle petrology); 674907 EVER-EST (generic Earth-science Virtual Research Environment, volcanoes one of four validation communities); 714936 TRUE DEPTHS (X-ray-diffraction elastic thermobarometry for subduction depths); 765710 INSIGHTS (statistical methods for particle physics, volcanology only a secondment domain); 845115 ICELEARNING (ice-core insoluble-particle counting for palaeoclimate); 101024337 PUSKURUM (retrospective Anatolian tephrostratigraphy); 101025887 ENDGAME (shock-tube fragmentation experiments and a magma-ascent model - laboratory eruption physics).

Discrimination counter-examples - satisfy the textual ask, fail the filter, each SQL-confirmed volcanology-classified with 0 Italian organisation rows in any role: 798480 VOLCANOWAVES (ES, "improve our ability to detect and track volcanic unrest and to forecast volcanic eruptions"); 866085 DEEPVOLC (UK, "radically advance the way future activity is forecast at volcanoes by applying advances in artificial intelligence to transformative new geodetic datasets"); 864052 VOLTA (DE, "a game-changing tool for volcano monitoring ... new electrostatic sensors to measure real-time electrical activity at target volcanoes"); 646858 VOLCAPSE (DE, "Time-lapse camera systems ... synchronized with satellite radar observations from the TerraSAR-X satellite"); 677493 FEVER (CH, "a physically based statistical model able to ForEcast the recurrence rate of Volcanic Eruptions"). The same run also returned STEMMS 864923 (UK) and VESPER 749611 (UK); the reviewer's own reformulation added MAST 101003173, TREMOR 789887, SEISMAZE 787399 and PRESEISMIC 805256.

Scoped rank matrix, re-run against the exact final wording (scope_size 15, k=10, index be84cbad9182), gold as lexical/dense/hybrid/hybrid_rerank: VULCAN.ears 1/3/4/4; IMPROVE 2/2/1/1; ARISE2 3/4/3/5; EUROVOLC 4/1/2/2; PICVOLC 6/8/6/6; SiC nano for PicoGeo 7/10/5/3; NEWTON-g 8/null/null/7; ChEESE 9/6/8/8. Non-gold survivors also returned: ENDGAME 5/null/9/9, PUSKURUM 10/5/7/10, EVER-EST null/7/10/null, CIAO null/9/null/null (12 distinct projects in the scoped union, matching `pooled_candidate_count`). All 8 gold sit in the lexical top-10, supporting exact-term.

**Reference answer:** "The eight qualifying projects converge on making volcanic unrest measurable and interpretable in near-real time, along three complementary lines. New sensing hardware: NEWTON-g developed and field-tested the building blocks of a gravity imager, installing 4 MEMS gravimeters at Mt. Etna alongside an AQG-B absolute quantum gravimeter that it showed delivers continuous gravity data suitable for volcano monitoring, while reporting that COVID-19 delays meant the original 'gravity imager' idea did not come completely true; SiC nano for PicoGeo targets an optical fibre strain sensor built on a vacuum-encapsulated SiC thin-membrane resonator with picostrain resolution (10-12) to reveal the slow strain transients preceding eruptions, and reports first-stage work on SiC thin-film growth plus the first drilling of its test sites for strain-meter and seismometer installation; IMPROVE prototyped an automated gas-steam ratio sensor and demonstrated fibre-optic distributed acoustic sensing for volcano monitoring. Data-driven signal analysis and subsurface imaging: VULCAN.ears builds an automatic Volcano Seismic Recognition (VSR) system based on structured Hidden Markov Models and a Parallel System Architecture to classify precursory seismicity in real time; PICVOLC integrated seismic, gravity and deformation data (GNSS, tilt, InSAR) with a physics-based 3D FEM inverse model and a new seismic tomography of Nevado del Ruiz, run with the Colombian Geological Survey's observatory (OVSM) and INGV - Rome, to locate and interpret sources of unrest; IMPROVE also combined seismic, geodetic, gravity and geochemical observations with machine learning to image magma storage at Krafla and Mount Etna. Simulation, early warning and observatory integration: ChEESE prepared exascale flagship codes for ensemble volcanic ash dispersal forecasting with satellite data assimilation and physics-based probabilistic hazard assessment, running daily ensemble ash forecasts operationally during the La Palma eruption for PEVOLCA and civil protection; ARISE2 delivered a Volcano Information System (VIS) and near-real-time Volcano Infrasound Early-Warning (VIEW) notifications that infer ash plume height and mass eruption rate from infrasound signals; EUROVOLC networked volcano observatories and research institutions and opened virtual access to modelling and assessment tools for responding to volcanic unrest and eruptions."

**Why this is a good question:** It discriminates a system that can both apply a structured constraint and read free text: the filter removes 47 of 62 volcanology projects - including five that answer the textual ask as well as any survivor (DEEPVOLC, VOLCANOWAVES, VOLTA, VOLCAPSE, FEVER) - while inside the filter, 7 of 15 survivors are volcanology-tagged but do nothing with monitoring or forecasting, so neither side alone yields the gold set. It adds the country-participation filter axis on a solid-earth instrumentation topic distinct from hyb-01's glaciology and hyb-03's superconductivity. L3/filter-survey is honest: 8 gold projects, all inside the enumerated survivor set, and the answer is a group characterisation across three method families rather than a single read or a two-way contrast.

**Drafting history:**

- Drift check: the candidate's (cp3 hybrid-01) filter SQL re-executed to 15 survivors, with the unfiltered volcanology total still 62.
- Grounding: exhaustive read of all 15 survivors. Four cases were ambiguous from the abstract alone (ARISE2, EVER-EST, IMPROVE, ENDGAME), so their report sections were pulled - this moved ARISE2 and IMPROVE to IN and confirmed EVER-EST and ENDGAME OUT. euroSciVoc tag noise confirmed as the profile warned: INSIGHTS (particle-physics statistics) and ICELEARNING (ice-core particle counting) carry the volcanology leaf but are off-theme.
- **Review round 1 returned FATAL-RECOVERABLE with two findings, both rectified by the same drafter and re-reviewed once:**
  - FATAL 1 (filter wording): the original text said "at least one Italian **participating** organisation", which collides with the `organization.role` value `participant`. The reviewer executed the role breakdown and found VULCAN.ears, PICVOLC, NEWTON-g and IMPROVE have their only Italian row as *coordinator*; under the strict reading survivors drop 15 -> 6 and gold 8 -> 4, below the filter-survey floor, moving the cell from L3 to L2. Fixed by rewording to the role-neutral "at least one organisation based in Italy", with `notes` now recording the any-role decision explicitly. The reviewer confirmed on re-review that the new phrase maps onto exactly one column (`organization.country`) with no competing role vocabulary, so the reading is closed.
  - FATAL 2 (reference overclaim): the original reference said NEWTON-g "built a field gravity imager ... deployed at Etna", contradicted by 801221's own workPerformed ("it was not possible to make the original idea of 'gravity imager' came completely true"). Fixed to partial deployment plus the reported shortfall; the reviewer re-checked the clause against the report and confirmed the false accomplishment claim is gone.
  - Reviewer MINOR fixed in the same pass: the SiC nano clause originally stated the 10-12 strain resolution as achieved, where 863220 frames it as the target with first-year work on SiC film growth and test-site drilling. Rephrased as target-plus-progress; confirmed on re-review.
- Because the question text changed, the filter SQL was re-executed in final form (same 15 ids) and BOTH retrieval runs were re-executed against the exact final wording, so the record carries no stale retrieval results.
- Reviewer attacks executed across the two rounds: filter re-run and euroSciVoc "volcan" path uniqueness probe; independent re-read and re-adjudication of all 15 survivors (matching split); entity spot-checks by SQL `contains()` (Nevado del Ruiz -> 793811, Krafla+Etna -> 858092, VIS/VIEW -> 653980, satellite data assimilation -> 823844, gas-steam ratio + distributed acoustic sensing -> 858092); own unscoped reformulation for the discrimination check; verification of the two newly expanded clauses (ChEESE La Palma/PEVOLCA is verbatim supported and is an achieved TRL-9 result; the PICVOLC tomography is supported as achieved); staleness matched on schema_docs and index in both rounds.
- Surviving reviewer MINOR flags, recorded not rectified (the rectification cap was reached): (1) **PICVOLC provenance slip** - the reference says the seismic tomography was "run with the Colombian Geological Survey's observatory (OVSM) and INGV - Rome", but 793811 pairs OVSM with the land gravity campaign and the tomography only with INGV. Every entity is real and the achievement genuine; only the attribution is loose. The reviewer's suggested one-clause fix, if you want it applied before promotion: "a new seismic tomography of Nevado del Ruiz developed with INGV - Rome, alongside a first land gravity campaign run with the Colombian Geological Survey's observatory (OVSM)". (2) ENDGAME is the weakest OUT and still outranks two gold members in the refreshed matrix (lexical 5 vs NEWTON-g 8 and ChEESE 9), so precision scoring on a defensible answer that mentions it will be noisy. (3) "Horizon 2020" is a no-op qualifier - `frameworkProgramme` is `H2020` for all 35,389 project rows - so the missing condition in `filter_sql` is harmless and consistent with hyb-03; template proximity to hyb-03's stem, different topic and subtype; and `pooling_evidence.rejected_count=7` counts rejected *survivors* rather than rejected pooled candidates, as the notes disclose.

Decision: [x] APPROVE  [ ] REJECT
