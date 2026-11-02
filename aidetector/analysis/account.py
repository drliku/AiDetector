"""Account-level checks: Lichess flags, smurfing, sandbagging, sudden rating jumps."""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

from .signals import Signal

SPEEDS = ("ultraBullet", "bullet", "blitz", "rapid", "classical")


def _now_ms() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def history_points(history: list[dict[str, Any]], perf_name: str) -> list[tuple[dt.date, int]]:
    """Rating history for one perf as [(date, rating)]. Lichess months are 0-based."""
    for h in history:
        if h.get("name", "").lower() == perf_name.lower():
            return [(dt.date(y, m + 1, d), r) for y, m, d, r in h.get("points", [])]
    return []


def account_signals(user: dict[str, Any], history: list[dict[str, Any]], now_ms: int | None = None) -> tuple[dict[str, Any], list[Signal]]:
    now_ms = now_ms or _now_ms()
    signals: list[Signal] = []
    created = user.get("createdAt")
    age_days = (now_ms - created) / 86_400_000 if created else None
    perfs = user.get("perfs") or {}
    count = user.get("count") or {}

    stats: dict[str, Any] = {
        "username": user.get("username"),
        "account_age_days": round(age_days, 1) if age_days is not None else None,
        "total_games": count.get("all"),
        "rated_games": count.get("rated"),
        "title": user.get("title"),
        "ratings": {s: {"rating": perfs[s].get("rating"), "games": perfs[s].get("games"),
                        "provisional": bool(perfs[s].get("prov"))}
                    for s in SPEEDS if s in perfs},
    }

    if user.get("tosViolation"):
        signals.append(Signal("account", "Marked by Lichess for a ToS violation",
                              "Lichess has already flagged this account (engine use, boosting or similar).", 0.95))
    if user.get("disabled"):
        signals.append(Signal("account", "Account is closed",
                              "The account is closed. Lichess often closes accounts after fair-play violations.", 0.3))

    # ---------------------------------------------------------------- smurfing
    best_speed, best = None, None
    for s in SPEEDS:
        p = perfs.get(s)
        if p and p.get("games", 0) >= 10 and (best is None or p["rating"] > best["rating"]):
            best_speed, best = s, p
    if best and age_days is not None:
        rating, games = best["rating"], best["games"]
        if age_days < 60 and rating >= 2000:
            strength = min(0.6, 0.2 + (rating - 2000) / 1000 + (60 - age_days) / 300)
            signals.append(Signal("smurf", "Strong rating on a brand-new account",
                                  f"{best_speed} rating {rating} after {games} games on a {age_days:.0f}-day-old account.",
                                  round(strength, 3)))
        elif games < 100 and rating >= 2200:
            signals.append(Signal("smurf", "Very high rating with few games",
                                  f"{best_speed} rating {rating} after only {games} games.",
                                  round(min(0.4, 0.15 + (rating - 2200) / 1500), 3)))

    for speed in SPEEDS:
        pts = history_points(history, speed)
        if len(pts) < 3:
            continue
        # Climb from the starting rating: smurfs shoot up from 1500 quickly.
        first_date, first_rating = pts[0]
        within = [r for d, r in pts if (d - first_date).days <= 30]
        climb = max(within) - first_rating
        if climb >= 450:
            signals.append(Signal("smurf", f"Rapid climb in the first month ({speed})",
                                  f"Went from {first_rating} to {max(within)} (+{climb}) within 30 days of first playing {speed}.",
                                  round(min(0.5, (climb - 350) / 800), 3)))

        # ---------------------------------------------------------- spikes
        jump, start_d, end_d, lo, hi = _max_window_gain(pts, days=7)
        if jump >= 250:
            signals.append(Signal("performance", f"Sudden rating jump ({speed})",
                                  f"+{jump} in a week ({lo} -> {hi}, {start_d} to {end_d}).",
                                  round(min(0.45, (jump - 200) / 600), 3)))

        # ---------------------------------------------------------- sandbagging
        drop, d_start, d_end, d_hi, d_lo = _max_window_drop(pts, days=7)
        if drop >= 300:
            after = [r for d, r in pts if d > d_end]
            rebound = (max(after) - d_lo) if after else 0
            if rebound >= 250:
                signals.append(Signal("smurf", f"Rating dumped then regained ({speed})",
                                      f"Dropped {drop} points in a week ({d_hi} -> {d_lo}, ending {d_end}) "
                                      f"then climbed back +{rebound}. Typical of sandbagging to farm weaker opponents.",
                                      round(min(0.45, (drop + rebound - 400) / 1000), 3)))
    return stats, signals


def _max_window_gain(pts: list[tuple[dt.date, int]], days: int):
    best = (0, None, None, None, None)
    j = 0
    for i in range(len(pts)):
        while (pts[i][0] - pts[j][0]).days > days:
            j += 1
        lo_idx = min(range(j, i + 1), key=lambda k: pts[k][1])
        gain = pts[i][1] - pts[lo_idx][1]
        if gain > best[0]:
            best = (gain, pts[lo_idx][0], pts[i][0], pts[lo_idx][1], pts[i][1])
    return best


def _max_window_drop(pts: list[tuple[dt.date, int]], days: int):
    best = (0, None, None, None, None)
    j = 0
    for i in range(len(pts)):
        while (pts[i][0] - pts[j][0]).days > days:
            j += 1
        hi_idx = max(range(j, i + 1), key=lambda k: pts[k][1])
        drop = pts[hi_idx][1] - pts[i][1]
        if drop > best[0]:
            best = (drop, pts[hi_idx][0], pts[i][0], pts[hi_idx][1], pts[i][1])
    return best


def results_signals(game_stats: list[dict[str, Any]]) -> tuple[dict[str, Any], list[Signal]]:
    """Look across many games: performance rating vs own rating, accuracy outliers."""
    signals: list[Signal] = []
    rated = [g for g in game_stats if g.get("rating") and g.get("opponent_rating") and g["result"] in ("win", "loss", "draw")]
    stats: dict[str, Any] = {"games_analyzed": len(game_stats)}
    if len(rated) >= 10:
        score = sum(1 if g["result"] == "win" else 0.5 if g["result"] == "draw" else 0 for g in rated)
        n = len(rated)
        avg_opp = statistics.fmean(g["opponent_rating"] for g in rated)
        avg_own = statistics.fmean(g["rating"] for g in rated)
        perf = avg_opp + 400 * (2 * score - n) / n
        stats.update(win_rate=round(score / n, 3), performance_rating=round(perf), avg_rating=round(avg_own),
                     avg_opponent_rating=round(avg_opp))
        if perf - avg_own >= 350:
            signals.append(Signal("performance", "Performing far above own rating",
                                  f"Performance rating {perf:.0f} over {n} recent games vs average rating {avg_own:.0f} "
                                  f"(score {score:g}/{n}).",
                                  round(min(0.5, (perf - avg_own - 250) / 600), 3)))

    accs = [g["accuracy"] for g in game_stats if g.get("accuracy") is not None]
    if len(accs) >= 6:
        med = statistics.median(accs)
        spikes = [g for g in game_stats if g.get("accuracy") is not None and g["accuracy"] >= max(92, med + 15)]
        stats.update(median_accuracy=round(med, 1), accuracy_spikes=len(spikes))
        if spikes:
            ids = ", ".join(g["game_id"] for g in spikes[:5])
            signals.append(Signal("performance", "Accuracy spikes far above their normal level",
                                  f"{len(spikes)} game(s) at 92%+ accuracy while their median is {med:.0f}%: {ids}.",
                                  round(min(0.5, 0.15 * len(spikes)), 3)))
    return stats, signals
