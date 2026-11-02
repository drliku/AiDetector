"""Optional local Stockfish analysis.

Lichess only includes evals for games someone requested computer analysis on.
If Stockfish is installed (set STOCKFISH_PATH, or have `stockfish` on PATH),
we analyse unanalysed games ourselves and also get the engine's top move for
every position, which enables the top-move match-rate check.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

import chess
import chess.engine

EVAL_CAP = 1000


def stockfish_path() -> str | None:
    p = os.getenv("STOCKFISH_PATH") or shutil.which("stockfish")
    return p if p and os.path.exists(p) else None


def _analyse_sync(game: dict[str, Any], path: str, depth: int) -> list[dict[str, Any]]:
    board = chess.Board(game["initialFen"]) if game.get("initialFen") else chess.Board()
    sans = (game.get("moves") or "").split()
    positions_best: list[str | None] = []
    evals: list[dict[str, Any]] = []
    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        engine.configure({"Threads": max(1, (os.cpu_count() or 2) - 1), "Hash": 128})
        for i in range(len(sans) + 1):
            info = engine.analyse(board, chess.engine.Limit(depth=depth))
            pv = info.get("pv") or []
            positions_best.append(pv[0].uci() if pv else None)
            score = info["score"].white()
            if i > 0:
                if score.is_mate():
                    evals.append({"mate": score.mate()})
                else:
                    evals.append({"eval": max(-EVAL_CAP, min(EVAL_CAP, score.score()))})
            if i == len(sans) or board.is_game_over():
                break
            board.push_san(sans[i])
    # entry i = eval after ply i, plus the best move in the position before ply i
    for i, e in enumerate(evals):
        if positions_best[i]:
            e["best"] = positions_best[i]
    return evals


async def analyse_game(game: dict[str, Any], depth: int | None = None) -> bool:
    """Fill game["analysis"] using local Stockfish. Returns True if it ran."""
    path = stockfish_path()
    if not path:
        return False
    depth = depth or int(os.getenv("STOCKFISH_DEPTH", "14"))
    game["analysis"] = await asyncio.to_thread(_analyse_sync, game, path, depth)
    game["_local_engine"] = True
    return True
