"""
Arnifi RAG web portal — thin API over the query pipeline.

Run locally: python -m app.api.server
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Lambda response streaming buffers small SSE chunks; pad with an SSE comment
# so each token flushes to the client (Function URL RESPONSE_STREAM).
_SSE_FLUSH_PAD = ":" + (" " * 1024) + "\n\n"

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import Settings
from app.config.env import load_env
from app.services.periskope.client import PeriskopeClient
from app.services.retrieval.engine import QueryEngine
from app.services.usage.context import bind_collection, end_collection, start_collection
from app.services.usage.identity import ChatUser, fetch_profile_role, resolve_user
from app.services.usage.store import save_usage
from app.utils.helpers import get_logger, setup_logging

logger = get_logger(__name__)

CONFIG_PATH = ROOT / "config" / "settings.yaml"
_settings: Settings | None = None
_querier: QueryEngine | None = None

# chat_id -> (expires_at_epoch, payload)
_invite_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_INVITE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_CHAT_ID_RE = re.compile(r"^[\w.-]+@(g|c)\.us$", re.IGNORECASE)

# Lifespan manager for the FastAPI app we use to initialize and close the QueryEngine
#QueryEngine is the main class that handles the RAG pipeline
@asynccontextmanager
async def lifespan(_: FastAPI):
    global _settings, _querier
    load_dotenv(ROOT / ".env")
    setup_logging()
    _settings = Settings(CONFIG_PATH)
    _querier = QueryEngine(_settings)
    yield
    if _settings is not None:
        _settings.close()
        _settings = None
        _querier = None


app = FastAPI(title="Arnifi Knowledge Assistant", lifespan=lifespan)

# CORS for a separate UI host (e.g. Vite `npm run dev` on :5173). Default "*"
# for local/demo. Static Arnifi-dashboard is served from this app on :8000.
_cors_origins_env = (os.environ.get("CORS_ALLOW_ORIGINS") or "").strip()
_cors_origins = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env
    else ["*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatHistoryTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="all", pattern="^(all|website|blog|drive|whatsapp)$")
    history: list[ChatHistoryTurn] = Field(default_factory=list, max_length=8)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "arnifi-rag-portal"}


def _require_admin(authorization: str | None) -> ChatUser:
    """WhatsApp invite is admin-only: valid Supabase JWT + profiles.role = admin."""
    user = resolve_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    role = fetch_profile_role(authorization, user.id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@app.get("/api/internal/whatsapp/invite")
def whatsapp_invite(
    chat_id: str = Query(..., min_length=5, max_length=128),
    authorization: str | None = Header(default=None),
):
    """Admin-only: resolve Periskope invite_link; create/refresh if missing."""
    _require_admin(authorization)

    cid = chat_id.strip()
    if not _CHAT_ID_RE.match(cid):
        raise HTTPException(status_code=400, detail="Invalid chat_id")

    now = time.time()
    cached = _invite_cache.get(cid)
    # Only reuse cache when we already have a real invite link.
    if cached and cached[0] > now and cached[1].get("invite_link"):
        return cached[1]

    env = load_env()
    if not env.periskope_api_key or not env.periskope_phone:
        raise HTTPException(status_code=503, detail="Periskope is not configured")

    try:
        client = PeriskopeClient(env.periskope_api_key, env.periskope_phone)
        chat = client.get_chat(cid)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Periskope lookup failed: {exc}",
        ) from exc

    chat_name = str(chat.get("chat_name") or cid)
    invite = PeriskopeClient.extract_invite_link(chat)
    refreshed = False

    if not invite:
        try:
            invite = client.refresh_invite(cid)
            refreshed = bool(invite)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not create WhatsApp invite for this group. "
                    "The connected Periskope number may need to be a group admin. "
                    f"({exc})"
                ),
            ) from exc

    payload = {
        "chat_id": cid,
        "chat_name": chat_name,
        "invite_link": invite,
        "refreshed": refreshed,
    }
    if invite:
        _invite_cache[cid] = (now + _INVITE_CACHE_TTL_SECONDS, payload)
    return payload


# This function is used to pad the SSE frames when running under Lambda response streaming.
def _should_pad_sse() -> bool:
    """Pad SSE frames when running under Lambda response streaming."""
    mode = (os.environ.get("AWS_LWA_INVOKE_MODE") or "").lower()
    return mode == "response_stream" or bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


@app.post("/api/chat/stream")
def chat_stream(
    body: ChatRequest,
    authorization: str | None = Header(default=None),
):
    if _querier is None:
        raise RuntimeError("Pipeline not initialized")

    user = resolve_user(authorization)
    if authorization and not user:
        logger.warning("Chat usage: bearer token present but user lookup failed")
    elif not authorization:
        logger.warning("Chat usage: no Authorization header; usage will not be saved")
    pad = _should_pad_sse()

    def event_stream():
        records = start_collection()
        try:
            history = [{"role": t.role, "content": t.content} for t in body.history]
            for event in _querier.ask_stream(
                body.question.strip(),
                source=body.source,
                history=history or None,
            ):
                frame = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if pad:
                    frame += _SSE_FLUSH_PAD
                yield frame
                bind_collection(records)
        finally:
            if user and authorization:
                if not records:
                    logger.warning("Chat usage: no Bedrock usage records collected")
                else:
                    logger.info(
                        "Chat usage: saving %d records for %s",
                        len(records),
                        user.email or user.id,
                    )
                    save_usage(user, authorization, records)
            else:
                logger.warning("Chat usage: skipped save (not signed in to API)")
            end_collection()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _should_serve_static_frontend() -> bool:
    """
    Serve the Vite portal from frontend/ (Arnifi-dashboard-1 build).

    Default ON when index.html exists. Set SERVE_STATIC_FRONTEND=0 to run
    API-only (e.g. Next.js on another port talking to this backend).
    """
    flag = (os.environ.get("SERVE_STATIC_FRONTEND") or "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return FRONTEND_DIR.is_dir() and (FRONTEND_DIR / "index.html").is_file()


if _should_serve_static_frontend():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.api.server:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
