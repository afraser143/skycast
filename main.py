"""Entry point: `uvicorn main:app` locally; Vercel's FastAPI support picks up `app` from here."""

from app.main import app

__all__ = ["app"]
