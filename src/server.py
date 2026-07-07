"""
Arnifi RAG web portal — thin API over the query pipeline.

Run: python -m src.server
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings
from src.query import QueryEngine
from src.utils import setup_logging

CONFIG_PATH = ROOT / "config" / "settings.yaml"
_settings: Settings | None = None
_querier: QueryEngine | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _settings, _querier
    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY") and os.getenv("OPEN_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["OPEN_API_KEY"]
    setup_logging()
    _settings = Settings(CONFIG_PATH)
    _querier = QueryEngine(_settings)
    yield
    if _settings is not None:
        _settings.close()
        _settings = None
        _querier = None


app = FastAPI(title="Arnifi Knowledge Assistant", lifespan=lifespan)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "arnifi-rag-portal"}


@app.post("/api/chat/stream")
def chat_stream(body: ChatRequest):
    if _querier is None:
        raise RuntimeError("Pipeline not initialized")

    def event_stream():
        for event in _querier.ask_stream(body.question.strip()):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "src.server:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
