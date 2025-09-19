import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from .core.config import get_settings
from .models.provider_factory import get_provider


APP_NAME = os.getenv("APP_NAME", "DeskMate — GraphRAG Service Desk Pilot")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("deskmate")


class SpaStaticFiles(StaticFiles):
    """
    StaticFiles subclass that serves index.html for unknown routes (SPA fallback).
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except Exception:
            # On any error retrieving a static asset, fall back to index.html if it exists
            index_file = FRONTEND_DIST / "index.html"
            if index_file.exists():
                return FileResponse(index_file)
            # If no frontend build, return 404 JSON
            return JSONResponse({"detail": "Not Found"}, status_code=404)


app = FastAPI(title=APP_NAME)

# CORS for local dev frontends
allowed_origins = get_settings().allowed_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


class AskRequest(BaseModel):
    question: str


@app.post("/ask")
def ask(payload: AskRequest):
    provider = get_settings().model_provider
    model = get_provider()
    answer = model.generate(payload.question)
    return {"answer": answer, "provider": provider, "question": payload.question}


# Mount frontend build at root with SPA fallback
if FRONTEND_DIST.exists():
    app.mount("/", SpaStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
