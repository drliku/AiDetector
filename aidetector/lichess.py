"""Thin async client for the public Lichess API.

No token is required for anything used here. Setting LICHESS_TOKEN (a personal
token with no scopes) just gives you higher rate limits.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

BASE_URL = "https://lichess.org"

GAME_ID_RE = re.compile(r"^[a-zA-Z0-9]{8}$")
GAME_URL_RE = re.compile(r"lichess\.org/([a-zA-Z0-9]{8})(?:[a-zA-Z0-9]{4})?(?:/(?:white|black))?(?:[/?#].*)?$")
USER_URL_RE = re.compile(r"lichess\.org/@/([A-Za-z0-9_-]{2,30})")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,30}$")


class LichessError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def parse_query(query: str) -> tuple[str, str]:
    """Return ("game", id) or ("user", username) for whatever the user typed."""
    q = query.strip()
    if m := USER_URL_RE.search(q):
        return "user", m.group(1)
    if m := GAME_URL_RE.search(q):
        return "game", m.group(1)
    # A bare 8 or 12 char string could be a game id, but it could also be a
    # username. We treat it as a username; game IDs should be pasted as links.
    if USERNAME_RE.match(q):
        return "user", q
    raise LichessError("Enter a Lichess username or a game link (https://lichess.org/xxxxxxxx).", 400)


class LichessClient:
    def __init__(self, token: str | None = None, timeout: float = 30.0):
        token = token or os.getenv("LICHESS_TOKEN")
        headers = {"User-Agent": "AiDetector (cheat-analysis research tool)"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, *, params: dict | None = None, accept: str = "application/json") -> httpx.Response:
        try:
            r = await self._client.get(path, params=params, headers={"Accept": accept})
        except httpx.HTTPError as e:
            raise LichessError(f"Could not reach Lichess: {e}") from e
        if r.status_code == 404:
            raise LichessError("Not found on Lichess.", 404)
        if r.status_code == 429:
            raise LichessError("Lichess rate limit hit. Wait a minute and try again.", 429)
        if r.status_code >= 400:
            raise LichessError(f"Lichess returned HTTP {r.status_code}.")
        return r

    async def get_user(self, username: str) -> dict[str, Any]:
        return (await self._get(f"/api/user/{username}")).json()

    async def get_rating_history(self, username: str) -> list[dict[str, Any]]:
        return (await self._get(f"/api/user/{username}/rating-history")).json()

    async def get_game(self, game_id: str) -> dict[str, Any]:
        params = {"moves": "true", "clocks": "true", "evals": "true", "accuracy": "true", "opening": "true"}
        return (await self._get(f"/game/export/{game_id}", params=params)).json()

    async def get_user_games(self, username: str, max_games: int = 30, rated_only: bool = True) -> list[dict[str, Any]]:
        params = {
            "max": max_games,
            "moves": "true",
            "clocks": "true",
            "evals": "true",
            "accuracy": "true",
            "opening": "true",
            "perfType": "ultraBullet,bullet,blitz,rapid,classical",
        }
        if rated_only:
            params["rated"] = "true"
        r = await self._get(f"/api/games/user/{username}", params=params, accept="application/x-ndjson")
        return [json.loads(line) for line in r.text.splitlines() if line.strip()]
