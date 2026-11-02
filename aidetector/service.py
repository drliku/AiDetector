"""Orchestrates fetching from Lichess and running every check."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from .analysis import engine
from .analysis.account import account_signals, results_signals
from .analysis.game import analyze_player_game, extract_moves, player_color
from .analysis.signals import Report, finalize
from .lichess import LichessClient, LichessError, parse_query

MAX_LOCAL_ENGINE_GAMES = int(os.getenv("MAX_LOCAL_ENGINE_GAMES", "5"))


async def _maybe_engine(games: list[dict[str, Any]], notes: list[str]) -> None:
    missing = [g for g in games if not g.get("analysis")]
    if not missing:
        return
    if not engine.stockfish_path():
        notes.append(f"{len(missing)} game(s) have no Lichess computer analysis, so engine checks were skipped "
                     "for them. Install Stockfish (or set STOCKFISH_PATH) to analyse them locally.")
        return
    todo = missing[:MAX_LOCAL_ENGINE_GAMES]
    for g in todo:  # sequential: Stockfish already uses all cores
        await engine.analyse_game(g)
    notes.append(f"Analysed {len(todo)} game(s) with local Stockfish.")


async def analyze_game(client: LichessClient, game_id: str, focus_user: str | None = None) -> Report:
    game = await client.get_game(game_id)
    report = Report(subject=game_id, kind="game")
    await _maybe_engine([game], report.notes)
    moves = extract_moves(game)

    colors = ["white", "black"]
    if focus_user and (c := player_color(game, focus_user)):
        colors = [c]
    per_player = {}
    for color in colors:
        stats, sigs = analyze_player_game(game, color, moves)
        name = (game["players"][color].get("user") or {}).get("name", color)
        for s in sigs:
            s.title = f"{name}: {s.title}" if len(colors) > 1 else s.title
        per_player[color] = {"name": name, **stats}
        report.signals.extend(sigs)

    report.stats = {"players": per_player, "speed": game.get("speed"), "status": game.get("status"),
                    "opening": (game.get("opening") or {}).get("name")}
    report.games = [{"color": c, **v} for c, v in per_player.items()]
    if not game.get("clocks"):
        report.notes.append("This game has no clock data (correspondence or unlimited), so timing checks were skipped.")
    return finalize(report)


async def analyze_user(client: LichessClient, username: str, max_games: int = 30) -> Report:
    user, history, games = await asyncio.gather(
        client.get_user(username),
        client.get_rating_history(username),
        client.get_user_games(username, max_games=max_games),
    )
    report = Report(subject=user.get("username", username), kind="user")
    acc_stats, acc_sigs = account_signals(user, history)
    report.signals.extend(acc_sigs)

    await _maybe_engine(games, report.notes)
    game_stats = []
    for g in games:
        color = player_color(g, username)
        if not color:
            continue
        stats, sigs = analyze_player_game(g, color)
        game_stats.append(stats)
        report.signals.extend(sigs)

    res_stats, res_sigs = results_signals(game_stats)
    report.signals.extend(res_sigs)
    _collapse_repeats(report)

    report.stats = {**acc_stats, **res_stats,
                    "games_with_analysis": sum(1 for g in game_stats if g.get("has_analysis"))}
    report.games = game_stats
    if not games:
        report.notes.append("No recent rated games found.")
    return finalize(report)


def _collapse_repeats(report: Report) -> None:
    """Merge the same per-game signal across games into one stronger signal, so
    one weird game doesn't look like a pattern and a real pattern stands out."""
    from .analysis.signals import Signal, combine

    groups: dict[tuple[str, str], list[Signal]] = {}
    rest: list[Signal] = []
    for s in report.signals:
        if s.game_id:
            groups.setdefault((s.category, s.title), []).append(s)
        else:
            rest.append(s)
    for (cat, title), sigs in groups.items():
        if len(sigs) == 1:
            rest.append(sigs[0])
            continue
        # Repetition is what matters: scale by how many games show it.
        strength = combine([s.strength for s in sigs]) * min(1.0, 0.5 + len(sigs) / 10)
        ids = ", ".join(s.game_id for s in sigs[:6]) + ("…" if len(sigs) > 6 else "")
        rest.append(Signal(cat, f"{title} (in {len(sigs)} games)",
                           f"Seen in {len(sigs)} games: {ids}. Example: {sigs[0].detail}",
                           round(min(0.85, strength), 3)))
    report.signals = rest


async def analyze(query: str, max_games: int = 30) -> Report:
    kind, value = parse_query(query)
    client = LichessClient()
    try:
        if kind == "game":
            return await analyze_game(client, value)
        return await analyze_user(client, value, max_games=max_games)
    finally:
        await client.aclose()


__all__ = ["analyze", "analyze_game", "analyze_user", "LichessError"]
