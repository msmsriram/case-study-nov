# CARDIO4Cities Case Study – Problem Understanding & Plan

Role: Sr. Specialist DDIT PO&CF Data Science & AI (REQ 10079677), Novartis Hyderabad
Deadline: Friday 18 Sep 2026, 8 PM IST, reply by email with repo link + materials
Timebox: 2–3 days (today is Wed 16 Sep)

## 1. The ask in one line
Build and deploy an AI system that, given ANY city named at demo time, researches the public
internet live, turns findings into a reusable, evidence-backed knowledge asset, lets a
non-technical City Lead explore and question it, and clearly flags uncertainty and gaps.

## 2. What the system must do (Problem Statement)
1. Gather and organize public information about a city
2. Create a reusable intelligence asset for future use
3. Help users explore and understand the information
4. Clearly communicate the evidence behind generated insights
5. Identify uncertainty, missing information, and data-quality issues

What a City Lead needs to understand: CVD health landscape, existing healthcare programmes,
major policy initiatives, stakeholders/organizations, opportunities/risks/gaps, evidence.

## 3. Non-negotiables (mandatory, implementation is free)
1. Live internet research at request time – no pre-seeded/hard-coded city data
2. Orchestrated agentic workflow (LangGraph or comparable) – must walk through the graph
3. Crawlability detection agent – decides if a source permits automated extraction BEFORE crawling
4. Independent fact-checking agent – can rule UNSUPPORTED/MISSING, with workflow consequences
5. Knowledge graph built with Graphiti – not substitutable, genuinely used at query time
6. At least three datastores – relational, vector, graph; justify what lives where
7. Evidence on every fact – "where did this come from?" always gets a real answer
8. No fabrication – never invent stats/people/attitudes; flag national data used for a city
9. Deployed and reachable at a URL

## 4. Evaluation weights
- Architecture & System Thinking 30%
- AI & Data Design 25%
- Trustworthiness & Evidence 20%
- Product & UX 15%
- Engineering Quality & Communication 10%
They repeatedly say: what you deliberately CUT and why matters more than tech choices.
"A working end-to-end solution beats an impressive half-built platform."

## 5. Deliverables
- Working deployed application
- Repo: source, setup instructions, architecture docs, env config guidance, deployment config
- Architecture overview (components, agents, data architecture, retrieval, trade-offs)
- Example output: one completed city research report
- Presentation deck: 5–8 slides (problem, solution/architecture, trust & evidence, UX, trade-offs)

## 6. Demo script (what they will do)
1. Pick a city  2. Ask you to explain architecture/workflow  3. Show collection + verification
4. Explore findings  5. Ask questions against the knowledge  6. Trace answers to evidence
7. Discuss trade-offs and limitations

## 7. Open design questions to answer explicitly in docs
- What does "understanding a city" consist of, and in what depth?
- How is research planned? How are sources evaluated?
- Should humans review before storage?
- When is research sufficient, and what happens when it isn't?
- How are conflicts handled? How is institutional memory represented over time?
- How are people/orgs/programmes/policies related (graph model)?
- What belongs in which datastore? How does conversational retrieval work?
- Facts vs assumptions? How is missing information represented?

## 8. Strategy: small, complete, easy to demo – DO NOT OVERBUILD

Flow:
User enters city → Research Planner → Live Web Search → Crawlability Check → Source Extraction
→ Fact Extraction → Independent Fact Verification → Storage (SQL + Vector + Graphiti)
→ Conversational Query with Evidence → Downloadable Report

LangGraph: ~6–7 clear nodes, not dozens
1. Research Planner – decomposes city into categories: CVD health, healthcare infrastructure,
   policies, programmes, stakeholders, risks/gaps, evidence needs
2. Search Agent – live search per category (Tavily/search API)
3. Crawlability Agent – robots.txt, content type, HTTP status, public accessibility;
   if not permitted: record URL as discovered source, do NOT scrape content
4. Extraction / Evidence Agent – claims + supporting text, URL, title, publisher,
   publication date, retrieval timestamp, geography level (city/state/national)
5. Fact Checker Agent – INDEPENDENT of extractor. Verdicts:
   SUPPORTED | PARTIALLY_SUPPORTED | CONFLICTING | UNSUPPORTED | MISSING
   UNSUPPORTED claims must not flow into verified intelligence (workflow consequence)
6. Knowledge Builder – writes verified facts to all three stores
7. Answer / Report Agent – hybrid retrieval, evidence exposure, uncertainty, report generation

Datastores:
| Store      | Contents                                                                              |
|------------|---------------------------------------------------------------------------------------|
| Relational | sources, claims, verification status, timestamps, city, geography level, confidence, research jobs |
| Vector     | source chunks + verified evidence for semantic retrieval                              |
| Graphiti   | relationships: people, orgs, policies, programmes, diseases, hospitals, govt bodies   |

Example graph edges:
City -HAS_PROGRAM-> Hypertension Screening Program; Program -OPERATED_BY-> Municipal Health Dept
Organization -LED_BY-> Person; Policy -TARGETS-> Hypertension; Hospital -LOCATED_IN-> City

Hybrid retrieval (Graphiti MUST be used at query time, not vector-only):
Question → intent decomposition → SQL metadata + vector evidence + Graphiti relationships
→ evidence synthesis → answer with citations

Trustworthiness in the UI (the biggest scoring lever):
Every claim carries: status (Verified/...), source, published date, retrieved date,
geography level, [View source]. If only national data exists:
"Geographic limitation: National-level data; city-specific evidence not found."
Conflicts: store both, surface both values + "Current status: conflicting evidence."
Never let the LLM silently pick one.

UI (keep simple):
- New City Research: [ city ] [Research City]
- Progress: Planning → Searching → Checking Sources → Extracting → Verifying → Building KG → Complete
- Result tabs: City Overview | Health Landscape | Programs | Policies | Stakeholders |
  Gaps & Risks | Sources | Ask the City
- Header stats: "42 verified facts • 17 sources • 5 knowledge gaps • 2 conflicting claims"
- Downloadable report (PDF or Markdown/HTML) with same sections + numbered evidence refs

Stack (candidate):
Frontend Streamlit or light React | Backend FastAPI | Orchestration LangGraph
LLM: whichever deploys reliably | Search: Tavily + direct fetch
Relational: PostgreSQL | Vector: Qdrant (or pgvector, but prefer distinct systems to be safe)
Graph: Graphiti + a supported backend (verify current Graphiti docs before choosing)
Deploy: Render / Railway / AWS – simple and stable

Do NOT reuse the previous Neptune implementation as a substitute for Graphiti.
(Check whether Graphiti itself supports Neptune as a backend; if so, that is acceptable
because Graphiti is still the KG layer.)

## 9. Timeline
Wed night:  architecture, repo skeleton, LangGraph state/schema; search + crawlability + extraction working
Thu AM:     fact checker, PostgreSQL + vector storage, Graphiti
Thu PM:     QA endpoint, evidence UI, report generation
Thu eve:    deployment + unseen-city testing
Fri:        bug fixes, README, architecture diagram, sample report, 5–8 slide deck, submit by 8 PM

## 10. Next step
Design the exact LangGraph state, nodes, transitions, DB schemas, and repo structure.
