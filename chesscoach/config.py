"""All tunable settings live here. Edit values, then re-run
`python -m chesscoach.leaks` (thresholds) or the engine batch (depths)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# --- Identity ---------------------------------------------------------------
CHESSCOM_USERNAME = os.environ.get("CHESSCOACH_CHESSCOM_USERNAME", "who_carezz")
LICHESS_USERNAME = os.environ.get("CHESSCOACH_LICHESS_USERNAME", "")  # set me
# Sent in the User-Agent so the API operators can reach you. Replace the placeholder.
CONTACT = os.environ.get("CHESSCOACH_CONTACT", "your-email@example.com")
USER_AGENT = f"chess-coach/1.0 (contact: {CONTACT})"

# --- Storage ----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("CHESSCOACH_DB", PROJECT_ROOT / "data" / "chesscoach.db"))
LOG_PATH = PROJECT_ROOT / "data" / "chesscoach.log"

# --- Engine -----------------------------------------------------------------
DEFAULT_DEPTH = 16
SHALLOW_DEPTH = 6
EXTEND_DEPTH = 10
MULTIPV_COUNT = 3
MIN_PV_LENGTH = 8
ENGINE_THREADS = 1  # per worker process
ENGINE_HASH_MB = 64  # per worker process
COMMIT_BATCH_SIZE = 5  # games per SQLite commit in the batch runner

# --- Leak thresholds (win % points) -----------------------------------------
WIN_PCT_BLUNDER_DROP = 10.0
FORCING_LEAK_MARGIN = 8.0
HORIZON_SHALLOW_TOLERANCE = 3.0
HORIZON_MIN_REFUTATION_PLY = 3
REFUTATION_MATERIAL_DROP = 2  # pawns down that count as "refuted"
TIME_TROUBLE_RATIO = 0.10
TIME_TROUBLE_MOVES = 40  # clock < ratio * (base + TIME_TROUBLE_MOVES * increment)

# --- Phase ------------------------------------------------------------------
OPENING_MAX_FULLMOVE = 12
OPENING_MIN_MATERIAL = 56
ENDGAME_MAX_MATERIAL = 26

# --- Trainer ----------------------------------------------------------------
PUZZLE_ACCEPT_TOLERANCE = 3.0
SM2_START_EASE = 2.5
SM2_MIN_EASE = 1.3
CALIBRATION_SAMPLE_SIZE = 20

# --- Time controls ----------------------------------------------------------
# Classes: bullet, blitz, rapid, daily. Daily is stored but never analyzed.
# Add "bullet" here to analyze bullet games too.
ANALYZE_TIME_CONTROLS = ["rapid", "blitz"]
BULLET_MAX_BASE_SECONDS = 180  # base < 3 min -> bullet
BLITZ_MAX_ESTIMATED_SECONDS = 600  # base + 40 * increment < 10 min -> blitz, else rapid

# --- Importer ---------------------------------------------------------------
HTTP_TIMEOUT = 60
LICHESS_BACKOFF_BASE_SECONDS = 60  # doubled on every consecutive HTTP 429
LICHESS_MAX_RETRIES = 6

# --- Opening book -----------------------------------------------------------
OPENINGS_DIR = Path(__file__).resolve().parent / "data" / "openings"  # Lichess chess-openings TSVs
POLYGLOT_BOOK_PATH = os.environ.get("CHESSCOACH_POLYGLOT_BOOK", "")  # optional .bin


def stockfish_path() -> str:
    """STOCKFISH_PATH env var, else `stockfish` on PATH, else common install locations."""
    env = os.environ.get("STOCKFISH_PATH")
    if env:
        return env
    found = shutil.which("stockfish")
    if found:
        return found
    for candidate in ("/usr/games/stockfish", "/usr/local/bin/stockfish", "/opt/homebrew/bin/stockfish"):
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "Stockfish not found. Set the STOCKFISH_PATH environment variable or put "
        "`stockfish` on your PATH (see README.md)."
    )
