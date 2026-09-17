---
title: CARDIO4Cities City Intelligence
emoji: 🫀
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# CARDIO4Cities City Intelligence

An AI research system that prepares a City Lead for a city nobody has researched before. Name any city: it
researches the public web **live**, checks that each source **permits reading**, extracts claims with
**verbatim quotes**, has an **independent model try to disprove them**, stores what survives in **three
datastores** (relational, vector, and a **Graphiti** knowledge graph), and answers questions with
**evidence on every sentence**, saying plainly what is only national data and what is not known.

- Architecture, design decisions and trade-offs: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Backend internals: [backend/README.md](backend/README.md)
- Example output: [backend/samples/](backend/samples/) (a full Nairobi briefing in Markdown and HTML)
- Workflow diagram (generated from the code): [backend/docs/workflow.mmd](backend/docs/workflow.mmd)

## The non-negotiables, and where they live

| Requirement | Where |
|---|---|
| Live internet research | `backend/app/research/search.py` (Serper → Tavily → Ollama → DuckDuckGo); cache TTL is 0 in production |
| Orchestrated agentic workflow | `backend/app/graph/builder.py` (LangGraph, 11 nodes, `Send` fan-out) |
| Crawlability detection agent | `backend/app/research/crawlability.py`, the `crawl_check` node, runs before any fetch |
| Independent fact-checking agent with consequences | `backend/app/graph/nodes/verification.py`; rejected claims never reach stores, answers or the report |
| Knowledge graph built with Graphiti, used at query time | `backend/app/stores/graph.py`, `backend/app/qa.py`; Ask is locked until the graph is ready |
| Three datastores | `backend/app/stores/relational.py`, `vector.py`, `graph.py` |
| Evidence on every fact | quote + evidence window + source + crawl record per claim; "Where did this come from?" in the UI |
| No fabrication | code-level quote check, geography levels with checker correction, citation validation, explicit gaps |
| Deployed | `render.yaml` (API + static frontend) |

## Run locally

```bash
python -m venv case_venv && source case_venv/Scripts/activate      # Windows Git Bash; use bin/activate elsewhere
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env                                # add keys (all have free tiers)
uvicorn app.api:app --app-dir backend --port 8000
```

```bash
cd frontend && npm install && npm run dev                           # http://localhost:5173
```

Minimum keys: one `SERPER_API_KEY_1` (or none, it falls back to DuckDuckGo), one `OLLAMA_API_KEY_1` or
`GROQ_API_KEY`, and Neo4j Aura credentials for the graph. With no `DATABASE_URL` / `QDRANT_URL` the app uses
local SQLite and embedded Qdrant, so it runs with nothing else installed.

Useful scripts (from `backend/`):

```bash
python scripts/test_graph.py "Kisumu, Kenya"      # full workflow in the terminal with streamed progress
python scripts/test_qa.py kisumu-ke "Who runs hypertension programmes?"
python scripts/rebuild_graph.py kisumu-ke        # rebuild the knowledge graph from the relational store
```

## Deploy (Render, free tier)

`render.yaml` defines both services from this one repo. Create a Blueprint from the repo, then set the
secrets listed in the file on the backend service: Serper and Ollama keys, `DATABASE_URL` (Neon Postgres),
`QDRANT_URL` + `QDRANT_API_KEY` (Qdrant Cloud), and the Neo4j Aura credentials.

## Repository layout

```
backend/   FastAPI, LangGraph workflow, research layer, three stores, Q&A, report
frontend/  React + TypeScript UI
docs/      architecture overview
```
