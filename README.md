# Arnifi Multi-Page Global Blog RAG

AWS-native RAG: crawl Arnifi blogs → **Amazon Titan Embed Text V2** → **Pinecone** → **Amazon Nova Lite** answers.

**Architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)  
**Lambda deploy (later):** [deploy/README.md](deploy/README.md)

## Layout

| Path | Role |
|------|------|
| `app/api/` | FastAPI + Lambda Mangum handler |
| `app/services/bedrock/` | Titan embeddings + Nova Lite |
| `app/services/pinecone/` | Vector store |
| `app/services/retrieval/` | Query / diversify |
| `app/services/ingestion/` | Index pipeline |
| `app/services/scraper/` | Blog crawl / fetch |
| `app/cli.py` | CLI entry |
| `config/settings.yaml` | Non-secret tunables |

## Setup

```powershell
cd Arnifi-Enterprise-RAG-Solution
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env` (see `.env.example`):

- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`
- `BEDROCK_REGION` (e.g. `us-east-1` if models are enabled there)
- `BEDROCK_CHAT_MODEL` / `BEDROCK_EMBED_MODEL`
- `PINECONE_API_KEY` / `PINECONE_INDEX` / `PINECONE_ENVIRONMENT`

Enable **Titan Embed Text V2** and **Nova Lite** in the Bedrock console for `BEDROCK_REGION`.

Pinecone index must be **1024-d**, cosine, **no hosted embedding model**.

## CLI

```powershell
python -m app.cli crawl
python -m app.cli ingest --limit 5
python -m app.cli index-posts --limit 3 --dry-run
python -m app.cli query "What is the VAT refund process in Malaysia?"
python -m app.cli inspect
```

## Web portal (local)

```powershell
.\scripts\run_portal.ps1
# http://127.0.0.1:8000
```

## Important

1. **Re-ingest** after switching to Titan — old MiniLM vectors are incompatible.
2. Test **locally** before Lambda deploy (see `deploy/README.md` cost checklist).
3. Set an AWS **Budget alert** ($5 / $10) before exposing a public Function URL.
