# AiDetector

Cheating detector for Lichess players who use engines, smurf, or play with an unfair advantage.

Type a **username** or paste a **game link**. The tool pulls the data from the public Lichess API and
scores the player on five areas:

| Category | What it looks for |
|---|---|
| **Engine use** | Average centipawn loss (ACPL) far below what's normal for the rating, almost every move near-perfect, and engine top-move match rate (needs local Stockfish) |
| **Move timing** | Robot-like uniform think times; thinking as long on obvious moves (recaptures, only moves) as on hard ones; long thinks on obvious moves |
| **Performance spikes** | Rating jumps of 250+ in a week, performance rating far above own rating, single games with accuracy far above the player's normal level |
| **Smurfing / sandbagging** | High rating on a brand-new account, very high rating with few games, rapid climb in the first month, rating dumped then regained |
| **Account status** | Already marked by Lichess for a ToS violation, closed account |

Each check produces a *signal* with a strength from 0 to 1. Signals are combined into a score per category and an
overall 0–100 suspicion score. When the same signal shows up in several games it's merged and weighted higher,
since a pattern across games means much more than one odd game.

> These are statistical hints, not proof. Strong players can look engine-like in a single game.

## Do I need a Lichess API key?

No. Everything here uses public endpoints. A personal token (no scopes needed, create one at
https://lichess.org/account/oauth/token) only raises the rate limit:

```bash
export LICHESS_TOKEN=lip_xxx
```

## Run it

**Easiest:** install Python from https://www.python.org/downloads/ (on Windows tick
**"Add python.exe to PATH"** in the installer), then double-click `run.bat` (Windows) or run `./run.sh`
(Mac/Linux). It sets everything up and opens http://localhost:8000.

Manual setup:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn aidetector.main:app --reload
```

Open http://localhost:8000.

API: `POST /api/analyze` with `{"query": "<username or game link>", "max_games": 30}`.

### Optional: local Stockfish

Lichess only has engine evaluations for games where someone requested computer analysis. Without them the
engine checks are skipped for that game (timing, performance and smurf checks still run). Install Stockfish to
analyse those games yourself and enable the top-move match check:

```bash
sudo apt install stockfish          # or brew install stockfish
export STOCKFISH_PATH=$(which stockfish)
export STOCKFISH_DEPTH=14           # default 14
export MAX_LOCAL_ENGINE_GAMES=5     # default 5 per request
```

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest
```

## Layout

```
aidetector/
  lichess.py            Lichess API client + query parsing
  service.py            fetches data, runs every check, merges signals
  main.py               FastAPI app
  analysis/
    game.py             per-game engine-accuracy and timing checks
    account.py          smurfing, sandbagging, rating spikes, ToS flags
    engine.py           optional local Stockfish
    signals.py          Signal/Report model and scoring
static/index.html       web page
tests/                  tests with synthetic games (no network needed)
```
