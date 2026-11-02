"""Per-game analysis: engine correlation and move-timing patterns for one player."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

import chess

from .signals import Signal

BOOK_PLIES = 16          # ignore the first 8 moves per side (opening theory)
EVAL_CAP = 1000          # centipawns; mates and crushing evals get clamped
MIN_MOVES = 12           # minimum analysed moves before we judge a game


@dataclass
class MoveInfo:
    ply: int                    # 0-based ply index
    color: str                  # "white" / "black"
    san: str
    legal_moves: int            # legal moves in the position before this move
    is_capture: bool
    is_recapture: bool
    gives_check: bool
    in_check: bool
    eval_before: int | None     # centipawns, white's point of view, clamped
    eval_after: int | None
    cp_loss: int | None         # from the mover's point of view, >= 0
    judgment: str | None        # Lichess label: Inaccuracy / Mistake / Blunder
    engine_best: str | None     # best move in SAN if known (local engine)
    time_cs: int | None         # think time in centiseconds
    clock_before_cs: int | None

    @property
    def is_obvious(self) -> bool:
        """Moves almost any player makes quickly: the only legal move, or a
        recapture of a piece that was just taken."""
        return self.legal_moves == 1 or self.is_recapture


def _eval_to_cp(entry: dict[str, Any] | None) -> int | None:
    if not entry:
        return None
    if "mate" in entry:
        return EVAL_CAP if entry["mate"] > 0 else -EVAL_CAP
    if "eval" in entry:
        return max(-EVAL_CAP, min(EVAL_CAP, int(entry["eval"])))
    return None


def win_percent(cp: int) -> float:
    """Lichess' centipawn -> win chance curve."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(cp_before_mover: int, cp_after_mover: int) -> float:
    """Lichess' per-move accuracy formula (0..100), evals from the mover's POV."""
    diff = max(0.0, win_percent(cp_before_mover) - win_percent(cp_after_mover))
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * diff) - 3.1669))


def extract_moves(game: dict[str, Any]) -> list[MoveInfo]:
    sans = (game.get("moves") or "").split()
    analysis = game.get("analysis") or []
    clocks = game.get("clocks") or []
    clock = game.get("clock") or {}
    inc_cs = int(clock.get("increment", 0)) * 100

    board = chess.Board(game["initialFen"]) if game.get("initialFen") else chess.Board()
    moves: list[MoveInfo] = []
    last_capture_sq: int | None = None
    prev_eval: int | None = 20 if analysis else None  # typical start eval

    for ply, san in enumerate(sans):
        try:
            move = board.parse_san(san)
        except ValueError:
            break
        color = "white" if board.turn == chess.WHITE else "black"
        legal = board.legal_moves.count()
        is_capture = board.is_capture(move)
        is_recapture = is_capture and last_capture_sq is not None and move.to_square == last_capture_sq
        in_check = board.is_check()
        gives_check = board.gives_check(move)

        entry = analysis[ply] if ply < len(analysis) else None
        after = _eval_to_cp(entry)
        cp_loss = None
        if prev_eval is not None and after is not None:
            delta = prev_eval - after if color == "white" else after - prev_eval
            cp_loss = max(0, delta)

        best = None
        if entry and entry.get("best"):
            try:
                best = board.san(chess.Move.from_uci(entry["best"]))
            except (ValueError, AssertionError):
                best = None

        time_cs = clock_before = None
        if ply >= 2 and ply < len(clocks):
            clock_before = clocks[ply - 2]
            time_cs = max(0, clocks[ply - 2] - clocks[ply] + inc_cs)

        moves.append(MoveInfo(
            ply=ply, color=color, san=san, legal_moves=legal, is_capture=is_capture,
            is_recapture=is_recapture, gives_check=gives_check, in_check=in_check,
            eval_before=prev_eval, eval_after=after, cp_loss=cp_loss,
            judgment=(entry or {}).get("judgment", {}).get("name"),
            engine_best=best, time_cs=time_cs, clock_before_cs=clock_before,
        ))
        last_capture_sq = move.to_square if is_capture else None
        board.push(move)
        prev_eval = after
    return moves


def expected_acpl(rating: int | None) -> float:
    """Rough average centipawn loss for a human at a given rating (blitz-ish).
    ~60 at 1500, ~40 at 2000, ~20 at 2500. Deliberately generous."""
    r = rating or 1500
    return max(15.0, 120.0 - 0.04 * r)


def analyze_player_game(game: dict[str, Any], color: str, moves: list[MoveInfo] | None = None) -> tuple[dict[str, Any], list[Signal]]:
    moves = moves if moves is not None else extract_moves(game)
    gid = game.get("id")
    player = game["players"][color]
    opp = game["players"]["black" if color == "white" else "white"]
    rating = player.get("rating")
    has_evals = any(m.eval_after is not None for m in moves)

    mine = [m for m in moves if m.color == color]
    middle = [m for m in mine if m.ply >= BOOK_PLIES]

    winner = game.get("winner")
    result = "draw" if not winner and game.get("status") in ("draw", "stalemate") else (
        "win" if winner == color else "loss" if winner else "other")

    stats: dict[str, Any] = {
        "game_id": gid,
        "color": color,
        "speed": game.get("speed"),
        "rated": game.get("rated"),
        "rating": rating,
        "opponent": (opp.get("user") or {}).get("name") or ("AI level %s" % opp.get("aiLevel") if opp.get("aiLevel") else "Anonymous"),
        "opponent_rating": opp.get("rating"),
        "result": result,
        "status": game.get("status"),
        "moves": len(mine),
        "has_analysis": has_evals,
        "created_at": game.get("createdAt"),
    }
    signals: list[Signal] = []

    # ---------------------------------------------------------------- engine
    lichess_an = player.get("analysis") or {}
    losses = [m.cp_loss for m in middle if m.cp_loss is not None]
    if has_evals and len(losses) >= MIN_MOVES:
        acpl = statistics.fmean(losses)
        accs = [move_accuracy(*_mover_pov(m)) for m in middle if m.cp_loss is not None]
        accuracy = lichess_an.get("accuracy") or round(statistics.fmean(accs), 1)
        perfect = sum(1 for l in losses if l <= 10) / len(losses)
        errors = sum(1 for m in middle if m.judgment in ("Mistake", "Blunder"))
        stats.update(acpl=round(acpl, 1), accuracy=accuracy, near_perfect_rate=round(perfect, 3),
                     mistakes_and_blunders=errors, lichess_acpl=lichess_an.get("acpl"))

        exp = expected_acpl(rating)
        ratio = acpl / exp
        if ratio < 0.5 and errors == 0:
            strength = min(0.6, (0.5 - ratio) * 1.2 + 0.15) * min(1.0, len(losses) / 25)
            signals.append(Signal(
                "engine", "Engine-like accuracy",
                f"ACPL {acpl:.1f} over {len(losses)} non-opening moves with no mistakes; "
                f"a {rating or '?'}-rated player typically averages ~{exp:.0f}.",
                round(strength, 3), gid))
        if perfect >= 0.85 and len(losses) >= 20 and (rating or 0) < 2400:
            signals.append(Signal(
                "engine", "Almost every move is near-perfect",
                f"{perfect:.0%} of {len(losses)} non-opening moves lost 10 centipawns or less.",
                round(min(0.5, (perfect - 0.8) * 2.5), 3), gid))

    engine_known = [m for m in middle if m.engine_best is not None]
    if len(engine_known) >= MIN_MOVES and game.get("_local_engine"):
        match = sum(1 for m in engine_known if m.engine_best == m.san) / len(engine_known)
        stats["engine_match_rate"] = round(match, 3)
        if match >= 0.8 and (rating or 0) < 2500:
            signals.append(Signal(
                "engine", "High engine top-move match",
                f"Played the engine's first choice in {match:.0%} of {len(engine_known)} non-opening moves.",
                round(min(0.6, (match - 0.7) * 2), 3), gid))

    # ---------------------------------------------------------------- timing
    clock = game.get("clock") or {}
    initial_cs = int(clock.get("initial", 0)) * 100
    timed = [m for m in middle if m.time_cs is not None
             and (not initial_cs or (m.clock_before_cs or 0) > 0.1 * initial_cs)]  # skip time scrambles
    if len(timed) >= MIN_MOVES:
        times = [m.time_cs / 100 for m in timed]
        mean_t = statistics.fmean(times)
        median_t = statistics.median(times)
        cv = statistics.pstdev(times) / mean_t if mean_t > 0 else 0.0
        stats.update(avg_move_time=round(mean_t, 2), median_move_time=round(median_t, 2), time_cv=round(cv, 3))

        # 1) Robot-like uniform think times. Humans are spiky (cv typically > 0.6).
        if cv < 0.35 and mean_t >= 1.0:
            signals.append(Signal(
                "timing", "Unnaturally consistent move times",
                f"Move times barely vary (coefficient of variation {cv:.2f}, avg {mean_t:.1f}s). "
                "Humans play some moves instantly and think hard on others.",
                round(min(0.45, (0.35 - cv) * 1.5 + 0.1), 3), gid))

        # 2) Spending as long on obvious moves (recaptures, only moves) as on hard ones.
        obvious = [m for m in timed if m.is_obvious]
        if len(obvious) >= 4 and median_t > 0:
            obv_med = statistics.median(m.time_cs / 100 for m in obvious)
            ratio = obv_med / median_t
            stats["obvious_move_time_ratio"] = round(ratio, 2)
            long_thinks = [m for m in obvious if m.time_cs / 100 >= max(5.0, 2.5 * median_t)]
            stats["obvious_long_thinks"] = len(long_thinks)
            if ratio >= 0.9 and obv_med >= 2.0:
                signals.append(Signal(
                    "timing", "Thinks as long on obvious moves as on hard ones",
                    f"Median {obv_med:.1f}s on {len(obvious)} obvious moves (recaptures / only moves) vs "
                    f"{median_t:.1f}s overall. Someone copying an engine pays the same delay every move.",
                    round(min(0.4, (ratio - 0.8) * 0.8 + 0.1), 3), gid))
            if len(long_thinks) >= 2:
                examples = ", ".join(f"{m.ply // 2 + 1}{'.' if m.color == 'white' else '...'}{m.san} ({m.time_cs / 100:.0f}s)"
                                     for m in long_thinks[:4])
                signals.append(Signal(
                    "timing", "Long thinks on obvious moves",
                    f"{len(long_thinks)} obvious moves took unusually long: {examples}.",
                    round(min(0.35, 0.1 * len(long_thinks)), 3), gid))

        # 3) Accurate when slow AND fast: humans blunder more when rushing.
        if has_evals:
            fast = [m.cp_loss for m in timed if m.cp_loss is not None and m.time_cs / 100 <= max(1.0, 0.5 * median_t)]
            if len(fast) >= 8:
                fast_acpl = statistics.fmean(fast)
                stats["fast_move_acpl"] = round(fast_acpl, 1)

    stats["signal_count"] = len(signals)
    return stats, signals


def _mover_pov(m: MoveInfo) -> tuple[int, int]:
    sign = 1 if m.color == "white" else -1
    return sign * (m.eval_before or 0), sign * (m.eval_after or 0)


def player_color(game: dict[str, Any], username: str) -> str | None:
    u = username.lower()
    for color in ("white", "black"):
        user = game["players"][color].get("user") or {}
        if (user.get("id") or "").lower() == u or (user.get("name") or "").lower() == u:
            return color
    return None
