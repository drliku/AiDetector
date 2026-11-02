"""Common data model for detection signals and the final verdict."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CATEGORIES = {
    "engine": "Engine use",
    "timing": "Suspicious move timing",
    "performance": "Sudden performance spike",
    "smurf": "Smurfing / sandbagging",
    "account": "Account status",
}


@dataclass
class Signal:
    category: str          # one of CATEGORIES
    title: str             # short headline
    detail: str            # human readable explanation with the numbers
    strength: float        # 0..1, how strongly this points to foul play
    game_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Report:
    subject: str                    # username or game id
    kind: str                       # "user" or "game"
    score: int = 0                  # 0..100 overall suspicion
    verdict: str = ""
    category_scores: dict[str, int] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    games: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["signals"] = [s.to_dict() for s in sorted(self.signals, key=lambda s: -s.strength)]
        return d


def combine(strengths: list[float]) -> float:
    """Noisy-OR: several independent weak signals add up, but never past 1."""
    p = 1.0
    for s in strengths:
        p *= 1.0 - max(0.0, min(1.0, s))
    return 1.0 - p


def finalize(report: Report) -> Report:
    by_cat: dict[str, list[float]] = {}
    for s in report.signals:
        by_cat.setdefault(s.category, []).append(s.strength)
    report.category_scores = {c: round(100 * combine(v)) for c, v in by_cat.items()}

    # Timing and performance alone are weak evidence, so they count for less
    # in the overall score than engine-correlation or a Lichess ToS mark.
    weights = {"engine": 1.0, "account": 1.0, "smurf": 0.8, "timing": 0.6, "performance": 0.6}
    report.score = round(100 * combine([weights.get(c, 0.7) * v / 100 for c, v in report.category_scores.items()]))

    if report.score >= 70:
        report.verdict = "Highly suspicious"
    elif report.score >= 40:
        report.verdict = "Suspicious, worth a closer look"
    elif report.score >= 20:
        report.verdict = "A few odd signals, probably fine"
    else:
        report.verdict = "Nothing notable"
    return report
