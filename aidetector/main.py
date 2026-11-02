"""FastAPI app: JSON API + the static web page."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis.engine import stockfish_path
from .lichess import LichessError
from .service import analyze

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="AiDetector", description="Lichess fair-play analysis")


class AnalyzeRequest(BaseModel):
    query: str = Field(..., description="Lichess username, profile link or game link")
    max_games: int = Field(30, ge=5, le=100)


@app.post("/api/analyze")
async def api_analyze(req: AnalyzeRequest):
    try:
        report = await analyze(req.query, max_games=req.max_games)
    except LichessError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e
    return report.to_dict()


@app.get("/api/health")
async def health():
    return {"ok": True, "local_engine": bool(stockfish_path())}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
