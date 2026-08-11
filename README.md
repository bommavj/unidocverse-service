# UniDocVerse Service

> **Multi-tenant document intelligence backend** — FastAPI + LangGraph + pgvector + Docling. Classifies, chunks, embeds, and reasons over documents across 10+ verticals (insurance, finance, legal, HR, healthcare, real estate, supply chain).

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](../LICENSE)
[![Migrations](https://img.shields.io/badge/DB%20Migrations-V47-orange)](migrations/sql)

---

## What it does

UniDocVerse Service is the backend brain of a document intelligence platform. Upload a PDF; the pipeline automatically:

1. **Parses** — Docling ML pipeline (LayoutLM + TableFormer) extracts text, tables, and structure from any PDF
2. **Classifies** — LangGraph agent identifies domain (insurance policy, annual report, pay stub, lease, etc.) with confidence scoring
3. **Analyzes** — Domain-specific analyzer extracts structured facts (coverage limits, financial figures, key dates, parties)
4. **Chunks + Embeds** — Recursive chunking with overlap; pgvector HNSW index (1536-dim) for semantic retrieval
5. **Answers** — ReAct research agent with supervisor routing, dynamic few-shot selection (cosine similarity over 80 examples), and streaming SSE responses

### Supported document verticals

| Vertical | Example documents |
|---|---|
| Insurance | Policies, COIs, endorsements, claims |
| Finance | 10-K / annual reports, pay stubs, invoices, bank statements |
| Legal | Contracts, NDAs, leases, matter files |
| HR | Offer letters, performance reviews, I-9, W-4 |
| Healthcare | EOBs, lab results, insurance cards |
| Real Estate | Purchase agreements, deeds, inspection reports |
| Supply Chain | BOLs, POs, customs declarations |
| Immigration | Visa applications, I-140, USCIS filings |

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │           FastAPI Application           │
                    │                                         │
  PDF Upload ──────►│  Processing Pipeline (async SQS-style) │
                    │  ┌──────────┐ ┌──────────┐ ┌────────┐  │
                    │  │ Docling  │ │LangGraph │ │Chunker │  │
                    │  │  Parse   │►│ Classify │►│+Embed  │  │
                    │  └──────────┘ └──────────┘ └────────┘  │
                    │                                         │
  Research ────────►│  Supervisor Agent                       │
                    │  ┌──────────────────────────────────┐   │
                    │  │ classify_intent → route to:       │   │
                    │  │  • fast (single-doc Q&A)          │   │
                    │  │  • research (ReAct loop)          │   │
                    │  │  • traversal (page-by-page)       │   │
                    │  └──────────────────────────────────┘   │
                    └─────────────────────────────────────────┘
                                       │
                    ┌──────────────────▼───────────────────────┐
                    │   PostgreSQL + pgvector                   │
                    │   documents · document_chunks · analytics │
                    │   HNSW index (ef=64, m=16)               │
                    └──────────────────────────────────────────┘
```

### Key design decisions

- **LangGraph for document pipeline** — stateful graph with typed state; each node (parse → classify → analyze → chunk → embed) is independently retryable and observable via trace logging
- **Dynamic few-shot selection** — 80 domain examples in `research_examples.yaml`; top-8 selected at query time by cosine similarity (not hardcoded prompts)
- **4-layer domain isolation** — prevents cross-document hallucination (a resume fact cannot bleed into a pay stub answer)
- **pgvector HNSW** over IVFFlat — better recall at query time, no need to rebuild index on insert
- **Streaming SSE** — research responses stream token-by-token; done event carries `has_more` + `page` for pagination

---

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI 0.115, Uvicorn, python-multipart |
| Agent orchestration | LangGraph, LangChain Core |
| Document parsing | Docling (LayoutLM + TableFormer), EasyOCR, PyMuPDF |
| LLM | Ollama (local) / AWS Bedrock Converse API |
| Vector store | pgvector 0.4.2, PostgreSQL 16, HNSW index |
| Database ORM | SQLAlchemy 2.0 |
| Database migrations | Flyway (47 versioned migrations) |
| Auth | JWT, multi-tenant isolation via tenant_id |
| MCP | Custom MCP server (30+ tools) |
| Packaging | Docker, Tauri (desktop), PyInstaller |

---

## Getting started

### Prerequisites

- Python 3.11+
- PostgreSQL 16 with pgvector extension
- Ollama (for local LLM) — `ollama pull phi3:mini` or `mistral`
- Flyway CLI (for migrations)

### Install

```bash
git clone https://github.com/bommavj/unidocverse-service
cd unidocverse-service

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env:
#   DATABASE_URL=postgresql://user:pass@localhost:5432/unidocverse_db
#   OLLAMA_BASE_URL=http://localhost:11434
#   JWT_SECRET=your-secret
#   DEFAULT_TENANT_ID=00000000-0000-0000-0000-000000000001
```

### Run migrations

```bash
flyway -url=jdbc:postgresql://localhost:5432/unidocverse_db \
       -user=postgres -password=postgres \
       -locations=filesystem:migrations/sql migrate
```

### Start the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs available at `http://localhost:8000/docs`

### Docker

```bash
docker build -f Dockerfile.backend -t unidocverse-service .
docker run -p 8000:8000 --env-file .env unidocverse-service
```

---

## API highlights

| Endpoint | Method | Description |
|---|---|---|
| `/api/documents/upload` | POST | Upload PDF; triggers async processing pipeline |
| `/api/documents` | GET | List documents with status, type, folder |
| `/api/research/ask` | POST | Ask a question; streams SSE response |
| `/api/analytics/dashboard` | GET | Aggregated document analytics |
| `/api/approvals` | GET/POST | HITL approval queue for agent actions |
| `/api/agent/runs` | GET | ReAct agent run history |
| `/mcp` | — | MCP server (30+ tools for Claude integration) |

---

## Database schema (key tables)

```sql
documents          -- uploaded docs with status, doc_type, tenant_id
document_chunks    -- text chunks with embedding vector (1536-dim), page_number
document_facts     -- extracted structured facts per document type
analytics_events   -- per-document analytics and query history
agent_runs         -- ReAct agent trace log
approvals          -- HITL approval queue
chatbot_sessions   -- research session history
```

47 Flyway migrations — full schema evolution history in `migrations/sql/`.

---

## Research agent — how it works

```
User query
    │
    ▼
classify_intent()
    ├── doc_ids present + simple question  → fast path (single LLM call)
    ├── traversal keywords detected        → page-ordered SQL fetch, paginated SSE
    └── complex / multi-doc               → ReAct loop
              │
              ▼
         Supervisor routes to:
              ├── document_react_agent   (single-doc deep dive)
              ├── global_research_agent  (cross-doc synthesis)
              └── domain_agent          (vertical-specific reasoning)
```

Page-by-page traversal uses `ORDER BY page_number ASC` (not cosine similarity) — 20 chunks per SSE page, `has_more` flag drives Load More in the UI.

---

## MCP server

The service exposes a full MCP server with 30+ tools covering all document verticals. Plug it into Claude Desktop or any MCP-compatible client:

```json
{
  "mcpServers": {
    "unidocverse": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "env": { "API_BASE_URL": "http://localhost:8000" }
    }
  }
}
```

---

## License

Apache License 2.0 — see [LICENSE](../LICENSE).

Copyright 2024 Vijay Bomma
