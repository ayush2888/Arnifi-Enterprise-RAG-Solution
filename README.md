# Arnifi Multi-Page Global Blog RAG

Python pipeline that crawls Arnifi blog pages, indexes them in Pinecone, and answers questions with RAG.

**Start here:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — indexing vs query pipeline, file map, data flow.

## Code layout (quick)

| Pipeline | Orchestrator | What it does |
|----------|--------------|--------------|
| **Indexing** (offline) | `src/rag/indexing_pipeline.py` | discover URLs → parse → chunk → embed → Pinecone |
| **Query** (online) | `src/rag/query_pipeline.py` | embed question → retrieve → LLM answer |
| **CLI** | `src/app.py` | `crawl`, `ingest`, `query`, etc. |
| **Facade** | `src/rag/pipeline.py` | wires both pipelines for the CLI |

## Features

- Paginated listing crawler for `https://arnifi.com/blog/` and category pages
- Auto-discovery of additional `/blog/category/` URLs from the hub page
- SQLite crawl state (`data/state.sqlite`)
- Heading-stack pairing (`h2` → `h3` → `h4`) before chunking
- Pinecone vector index with rich per-chunk metadata and `source_url` traceability
- Global retrieval with diversity across posts (max 5 chunks, max 2 per post)

## Setup

```powershell
cd Arnifi-Enterprise-RAG-Solution
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Set in `.env`:
- `GROQ_API_KEY` (for answer generation)
- `PINECONE_API_KEY`
- `OPENAI_API_KEY` (only if you switch `embedding.provider` to `openai` in `config/settings.yaml`)

Embeddings default to **sentence-transformers** (`all-MiniLM-L6-v2`, 384 dimensions). To use OpenAI embeddings later, update `config/settings.yaml`:

```yaml
embedding:
  provider: openai
  model: text-embedding-3-small
  dimension: 1536
pinecone:
  index_name: arnifi-blog-rag-openai
```

## CLI

```powershell
# Phase 1: discover post URLs from listings/categories
python src/app.py crawl

# Full ingest: crawl + fetch posts + embed + upsert
python src/app.py ingest

# Ingest only first N posts (testing)
python src/app.py ingest --limit 5

# Chunk posts without API calls (validation)
python src/app.py index-posts --limit 3 --dry-run

# Extract one post to JSON for inspection
python src/app.py extract --url "https://arnifi.com/blog/vat-tax-refund-malaysia-2026-step-by-step-guide/"

# Ask a question
python src/app.py query "What is the VAT refund process in Malaysia?"

# Inspect crawl DB + Pinecone stats
python src/app.py inspect
```

## Project layout

- `docs/ARCHITECTURE.md` — how indexing and query pipelines fit together
- `config/settings.yaml` — seeds, crawl limits, chunk/retrieval params
- `src/rag/indexing_pipeline.py` — offline index build
- `src/rag/query_pipeline.py` — online Q&A
- `src/ingest/` — crawl, fetch, parse, chunk (indexing steps 1–4)
- `src/vectorstore/` — embeddings + Pinecone (steps 5–6)
- `data/artifacts/` — optional HTML/JSON debug output

## Tests

```powershell
python -m unittest discover -s tests -v
```
