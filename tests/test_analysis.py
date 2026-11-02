import datetime as dt

import chess
import pytest
from fastapi.testclient import TestClient

from aidetector.analysis.account import account_signals, results_signals
from aidetector.analysis.game import analyze_player_game, extract_moves
from aidetector.lichess import LichessClient, LichessError, parse_query
from aidetector.main import app

# A real 60-ply game (Ruy Lopez, Morphy-style) used as the move skeleton.
MOVES = ("e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O h3 Nb8 d4 Nbd7 "
         "c4 c6 cxb5 axb5 Nc3 Bb7 Bg5 b4 Nb1 h6 Bh4 c5 dxe5 Nxe4 Bxe7 Qxe7 exd6 Qf6 Nbd2 Nxd6 "
         "Nc4 Nxc4 Bxc4 Nb6 Ne5 Rae8 Bxf7+ Rxf7 Nxf7 Rxe1+ Qxe1 Kxf7 Qe3 Qg5 Qxg5 hxg5 b3 Ke6 a3 Kd6")


def make_game(*, evals=None, clocks=None, rating_w=1500, rating_b=1500, winner="white", gid="abcdefgh"):
    sans = MOVES.split()
    return {
        "id": gid, "rated": True, "speed": "blitz", "status": "resign", "winner": winner,
        "createdAt": 1_700_000_000_000,
        "players": {
            "white": {"user": {"name": "Suspect", "id": "suspect"}, "rating": rating_w},
            "black": {"user": {"name": "Victim", "id": "victim"}, "rating": rating_b},
        },
        "moves": MOVES,
        "clock": {"initial": 180, "increment": 2},
        "clocks": clocks if clocks is not None else [],
        "analysis": evals if evals is not None else [{"eval": 20} for _ in sans],
    }


def uniform_clocks(n, think_cs=400, inc_cs=200, start=18000):
    clocks, w, b = [], start, start
    for i in range(n):
        if i < 2:
            clocks.append(start)
            continue
        if i % 2 == 0:
            w = w - think_cs + inc_cs
            clocks.append(w)
        else:
            b = b - think_cs + inc_cs
            clocks.append(b)
    return clocks


def test_moves_parse_legally():
    board = chess.Board()
    for san in MOVES.split():
        board.push_san(san)


@pytest.mark.parametrize("q,expected", [
    ("DrNykterstein", ("user", "DrNykterstein")),
    ("https://lichess.org/@/Some_User", ("user", "Some_User")),
    ("https://lichess.org/abcd1234", ("game", "abcd1234")),
    ("https://lichess.org/abcd1234wxyz", ("game", "abcd1234")),
    ("lichess.org/abcd1234/black#32", ("game", "abcd1234")),
])
def test_parse_query(q, expected):
    assert parse_query(q) == expected


def test_parse_query_rejects_garbage():
    with pytest.raises(LichessError):
        parse_query("not a user!!")


def test_extract_moves_times_and_losses():
    n = len(MOVES.split())
    evals = [{"eval": 20} for _ in range(n)]
    for i in range(20, n):
        evals[i] = {"eval": -180}  # white's 11th move drops 200cp, stays there
    g = make_game(evals=evals, clocks=uniform_clocks(n))
    moves = extract_moves(g)
    assert len(moves) == n
    assert moves[20].cp_loss == 200 and moves[20].color == "white"
    assert moves[21].cp_loss == 0  # black gained, no loss
    assert moves[10].time_cs == 400
    recaptures = [m for m in moves if m.is_recapture]
    assert any(m.san == "axb5" for m in recaptures)


def test_perfect_low_rated_game_flags_engine():
    g = make_game(rating_w=1300)
    stats, sigs = analyze_player_game(g, "white")
    assert stats["acpl"] == 0
    cats = {s.category for s in sigs}
    assert "engine" in cats


def test_normal_game_not_flagged_for_engine():
    n = len(MOVES.split())
    # alternate evals so both sides lose ~60cp per move on average
    evals = [{"eval": -60 if i % 2 == 0 else 60} for i in range(n)]
    g = make_game(evals=evals, rating_w=1500)
    stats, sigs = analyze_player_game(g, "white")
    assert stats["acpl"] > 50
    assert not [s for s in sigs if s.category == "engine"]


def test_uniform_timing_flagged():
    n = len(MOVES.split())
    g = make_game(clocks=uniform_clocks(n, think_cs=500))
    stats, sigs = analyze_player_game(g, "black")  # black has 4 obvious recaptures after move 8
    titles = {s.title for s in sigs}
    assert stats["time_cv"] == 0
    assert "Unnaturally consistent move times" in titles
    assert "Thinks as long on obvious moves as on hard ones" in titles


def test_smurf_and_tos():
    now = dt.datetime(2026, 1, 31, tzinfo=dt.timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    user = {"username": "Fresh", "createdAt": now_ms - 10 * 86_400_000, "tosViolation": True,
            "perfs": {"blitz": {"rating": 2350, "games": 40}}, "count": {"all": 40, "rated": 40}}
    hist = [{"name": "Blitz", "points": [[2026, 0, 21, 1500], [2026, 0, 23, 1800], [2026, 0, 27, 2100], [2026, 0, 30, 2350]]}]
    stats, sigs = account_signals(user, hist, now_ms=now_ms)
    titles = " | ".join(s.title for s in sigs)
    assert "ToS" in titles
    assert "brand-new account" in titles
    assert "Rapid climb" in titles
    assert "Sudden rating jump" in titles


def test_sandbagging_detected():
    user = {"username": "Bagger", "createdAt": 0, "perfs": {"blitz": {"rating": 1900, "games": 900}}}
    pts = [[2025, 0, 1, 1900], [2025, 0, 3, 1500], [2025, 0, 5, 1400], [2025, 1, 1, 1750], [2025, 2, 1, 1900]]
    _, sigs = account_signals(user, [{"name": "Blitz", "points": pts}])
    assert any("dumped" in s.title for s in sigs)


def test_results_performance_spike():
    games = [{"game_id": f"g{i}", "rating": 1500, "opponent_rating": 1900, "result": "win", "accuracy": 80}
             for i in range(12)]
    stats, sigs = results_signals(games)
    assert stats["performance_rating"] == 2300
    assert any(s.title == "Performing far above own rating" for s in sigs)


def test_api_end_to_end(monkeypatch):
    n = len(MOVES.split())
    games = [make_game(gid=f"game{i:04d}", rating_w=1400, clocks=uniform_clocks(n)) for i in range(6)]
    user = {"username": "Suspect", "createdAt": 1_600_000_000_000,
            "perfs": {"blitz": {"rating": 1400, "games": 500}}, "count": {"all": 500, "rated": 500}}

    async def get_user(self, u): return user
    async def get_hist(self, u): return [{"name": "Blitz", "points": [[2024, 0, 1, 1400]]}]
    async def get_games(self, u, max_games=30, rated_only=True): return games
    async def get_game(self, gid): return games[0]
    monkeypatch.setattr(LichessClient, "get_user", get_user)
    monkeypatch.setattr(LichessClient, "get_rating_history", get_hist)
    monkeypatch.setattr(LichessClient, "get_user_games", get_games)
    monkeypatch.setattr(LichessClient, "get_game", get_game)

    client = TestClient(app)
    r = client.post("/api/analyze", json={"query": "Suspect"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "user" and d["score"] >= 40
    assert any("(in 6 games)" in s["title"] for s in d["signals"])

    r = client.post("/api/analyze", json={"query": "https://lichess.org/game0000"})
    assert r.status_code == 200
    assert r.json()["kind"] == "game"

    assert client.post("/api/analyze", json={"query": "bad query!!"}).status_code == 400
    assert client.get("/").status_code == 200
