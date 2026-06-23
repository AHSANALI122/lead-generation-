"""FastAPI application entry point.

Holds the app, CORS, and the health check. Later features add chat/admin routes,
rate limiting, and auth on top of this. Env is loaded before anything reads it.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db import create_db_and_tables

load_dotenv()


def _allowed_origins() -> list[str]:
    """Parse ALLOWED_ORIGINS (comma-separated).

    Defaults to the Next.js dev origin. ALLOWED_ORIGINS overrides this in every real
    deployment, so the default only matters for local dev.
    """
    raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (F2). No-op if they already exist.
    create_db_and_tables()
    yield


app = FastAPI(title="Lead-Gen Chatbot API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
