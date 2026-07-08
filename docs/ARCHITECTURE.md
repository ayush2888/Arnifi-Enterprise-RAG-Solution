# Arnifi Blog RAG — Architecture

This project implements a standard **two-pipeline RAG** system: one for building the index (offline), one for answering questions (online).

## Indexing pipeline (offline)

Run with: `python src/app.py crawl`, `ingest`, `index-posts`, or `extract`

```
settings.yaml seeds
       │
       ▼
┌──────────────────┐
│ 1. Discover URLs │  crawler.py + listing_parser.py
└────────┬─────────┘
         │  post URLs saved to data/state.sqlite
         ▼
┌──────────────────┐
│ 2. Fetch HTML    │  fetcher.py
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 3. Parse         │  extractor.py  →  Document (title, sections, headings)
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 4. Chunk         │  chunker.py    →  Chunk list
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 5. Embed         │  embeddings.py →  vectors
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 6. Upsert        │  pinecone_client.py → Pinecone index
└──────────────────┘
```

Orchestrated by: `src/rag/indexing_pipeline.py` (`IndexingPipeline`)

## Query pipeline (online)

Run with: `python src/app.py query "..."`

```
User question
       │
       ▼
┌──────────────────┐
│ 1. Embed query   │  embeddings.py
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 2. Retrieve      │  pinecone_client.py + retriever.py (top-k, diversify)
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 3. Generate      │  generator.py (Groq / OpenAI-compatible LLM)
└────────┬─────────┘
         ▼
   Answer + source citations
```

Orchestrated by: `src/rag/query_pipeline.py` (`QueryPipeline`)

## File map (RAG step → code)

| RAG step | File |
|----------|------|
| Discover listing/post URLs | `src/ingest/crawler.py`, `src/ingest/listing_parser.py` |
| Fetch pages | `src/ingest/fetcher.py` |
| Parse HTML to documents | `src/ingest/extractor.py` |
| Chunk documents | `src/ingest/chunker.py` |
| Embed text | `src/vectorstore/embeddings.py` |
| Vector DB read/write | `src/vectorstore/pinecone_client.py` |
| Diversify retrieval | `src/rag/retriever.py` |
| LLM answer | `src/rag/generator.py` |
| Data models | `src/rag/schemas.py` |
| Shared config + services | `src/rag/context.py` |
| Shared config + services | `src/rag/context.py` |
| Indexing orchestration | `src/rag/indexing_pipeline.py` |
| Query orchestration | `src/rag/query_pipeline.py` |
| CLI facade | `src/rag/pipeline.py` |
| URL crawl registry | `src/store/state_db.py` |
| Debug HTML/JSON | `src/store/artifacts.py` |

## Entry point for reading code

1. `src/app.py` — which command runs which pipeline
2. `src/rag/pipeline.py` — thin facade (`indexing` + `query`)
3. `src/rag/indexing_pipeline.py` or `src/rag/query_pipeline.py` — pick your flow

## Data storage

| What | Where |
|------|-------|
| Discovered URLs | `data/state.sqlite` |
| Raw / parsed debug files | `data/artifacts/` |
| Searchable vectors | Pinecone (`config/settings.yaml` → `pinecone.index_name`) |

## Configuration

All tunables live in `config/settings.yaml`:
- `seeds` — where crawling starts (not the only pages crawled)
- `crawl` — politeness, limits
- `chunk` — chunk size and overlap
- `embedding` — provider, model, dimension
- `pinecone` — index name and region
- `llm` — answer model
- `retrieval` — how many chunks to fetch and return
