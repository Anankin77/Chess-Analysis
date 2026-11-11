"""SQLite storage.

Concurrency rule: engine worker processes NEVER open the database. They
return dataclasses (models.py) to the main process, which commits them in
batches from a single thread via the save_* functions below."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from . import config
from .models import GameAnalysisResult, GameRecord, LeakFinding, MoveAnalysis, PVLine

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT,
    white TEXT, black TEXT,
    my_color TEXT NOT NULL,
    result TEXT,
    date TEXT,
    time_control TEXT,
    base_seconds INTEGER,
    increment_seconds INTEGER,
    tc_class TEXT,
    eco TEXT, opening TEXT,
    white_elo INTEGER, black_elo INTEGER,
    pgn TEXT NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | error
    analysis_error TEXT,
    book_plies INTEGER,
    analyzed_at TEXT,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS moves (
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER NOT NULL,
    fullmove INTEGER NOT NULL,
    color TEXT NOT NULL,
    is_mine INTEGER NOT NULL,
    san TEXT NOT NULL,
    uci TEXT NOT NULL,
    fen_before TEXT NOT NULL,
    clock_seconds REAL,
    PRIMARY KEY (game_id, ply)
);
CREATE TABLE IF NOT EXISTS analysis (
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER NOT NULL,
    fen_before TEXT NOT NULL,
    my_move TEXT NOT NULL,
    depth INTEGER NOT NULL,
    shallow_depth INTEGER NOT NULL,
    deep_lines TEXT NOT NULL,      -- JSON list of PVLine
    shallow_lines TEXT NOT NULL,   -- JSON list of PVLine
    my_deep TEXT NOT NULL,         -- JSON PVLine
    my_shallow TEXT NOT NULL,      -- JSON PVLine
    pv_after TEXT NOT NULL,        -- JSON list of UCI
    pv_extended INTEGER NOT NULL,
    best_win REAL NOT NULL,
    my_win REAL NOT NULL,
    win_drop REAL NOT NULL,
    PRIMARY KEY (game_id, ply)
);
CREATE TABLE IF NOT EXISTS leaks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER NOT NULL,
    leak_type TEXT NOT NULL,       -- forcing_bias | horizon
    sub_type TEXT,                 -- horizon: material | mate | positional
    refutation_ply INTEGER,
    win_drop REAL NOT NULL,
    phase TEXT NOT NULL,
    time_trouble TEXT NOT NULL,    -- yes | no | unknown
    fen_before TEXT NOT NULL,
    my_move TEXT NOT NULL,
    best_move TEXT NOT NULL,
    UNIQUE (game_id, ply, leak_type)
);
CREATE TABLE IF NOT EXISTS srs_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER NOT NULL,
    repetition INTEGER NOT NULL DEFAULT 0,
    interval INTEGER NOT NULL DEFAULT 0,       -- days
    ease_factor REAL NOT NULL DEFAULT 2.5,
    due_date TEXT NOT NULL,                    -- ISO date
    last_grade INTEGER,
    last_reviewed TEXT,
    reviews INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    UNIQUE (game_id, ply)
);
CREATE TABLE IF NOT EXISTS calibration_labels (
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER NOT NULL,
    leak_type TEXT NOT NULL,
    verdict TEXT NOT NULL,         -- valid | false_positive
    labeled_at TEXT NOT NULL,
    PRIMARY KEY (game_id, ply, leak_type)
);
CREATE INDEX IF NOT EXISTS idx_games_status ON games(analysis_status, tc_class);
CREATE INDEX IF NOT EXISTS idx_leaks_type ON leaks(leak_type);
CREATE INDEX IF NOT EXISTS idx_cards_due ON srs_cards(due_date);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Optional[Path | str] = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # readers (the app) never block the batch writer
    conn.executescript(SCHEMA)
    return conn


# --- games / moves ------------------------------------------------------------

def existing_game_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM games")}


def save_games(conn: sqlite3.Connection, games: Iterable[GameRecord]) -> int:
    """Insert new games (existing IDs are skipped). Returns number inserted."""
    inserted = 0
    with conn:
        for g in games:
            cur = conn.execute(
                """INSERT OR IGNORE INTO games
                   (id, source, url, white, black, my_color, result, date, time_control,
                    base_seconds, increment_seconds, tc_class, eco, opening,
                    white_elo, black_elo, pgn, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (g.id, g.source, g.url, g.white, g.black, g.my_color, g.result, g.date,
                 g.time_control, g.base_seconds, g.increment_seconds, g.tc_class, g.eco,
                 g.opening, g.white_elo, g.black_elo, g.pgn, now_iso()),
            )
            if cur.rowcount == 0:
                continue
            inserted += 1
            conn.executemany(
                "INSERT INTO moves VALUES (?,?,?,?,?,?,?,?,?)",
                [(g.id, m.ply, m.fullmove, m.color, int(m.is_mine), m.san, m.uci,
                  m.fen_before, m.clock_seconds) for m in g.moves],
            )
    return inserted


def pending_games(conn: sqlite3.Connection, time_controls: list[str],
                  include_errors: bool = False, limit: Optional[int] = None) -> list[sqlite3.Row]:
    """Games still to analyze, newest first. Daily games are never returned."""
    tcs = [t for t in time_controls if t != "daily"]
    if not tcs:
        return []
    statuses = ["pending", "error"] if include_errors else ["pending"]
    sql = (f"SELECT id, pgn, my_color FROM games WHERE tc_class IN ({','.join('?' * len(tcs))}) "
           f"AND analysis_status IN ({','.join('?' * len(statuses))}) ORDER BY date DESC, id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, tcs + statuses).fetchall()


# --- analysis -----------------------------------------------------------------

def _lines_json(lines: list[PVLine]) -> str:
    return json.dumps([asdict(line) for line in lines])


def save_analysis_batch(conn: sqlite3.Connection, results: Iterable[GameAnalysisResult]) -> None:
    """Commit a batch of worker results in ONE transaction (main process only)."""
    with conn:
        for res in results:
            if res.error:
                conn.execute(
                    "UPDATE games SET analysis_status='error', analysis_error=?, analyzed_at=? WHERE id=?",
                    (res.error, now_iso(), res.game_id))
                continue
            conn.execute("DELETE FROM analysis WHERE game_id=?", (res.game_id,))
            conn.executemany(
                "INSERT INTO analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(a.game_id, a.ply, a.fen_before, a.my_move, a.depth, a.shallow_depth,
                  _lines_json(a.deep_lines), _lines_json(a.shallow_lines),
                  json.dumps(asdict(a.my_deep)), json.dumps(asdict(a.my_shallow)),
                  json.dumps(a.pv_after), int(a.pv_extended),
                  a.best_deep_win, a.my_deep.win, a.win_drop) for a in res.moves],
            )
            conn.execute(
                "UPDATE games SET analysis_status='done', analysis_error=NULL, book_plies=?, analyzed_at=? WHERE id=?",
                (res.book_plies, now_iso(), res.game_id))


def row_to_analysis(row: sqlite3.Row) -> MoveAnalysis:
    return MoveAnalysis(
        game_id=row["game_id"], ply=row["ply"], fen_before=row["fen_before"],
        my_move=row["my_move"], depth=row["depth"], shallow_depth=row["shallow_depth"],
        deep_lines=[PVLine(**d) for d in json.loads(row["deep_lines"])],
        shallow_lines=[PVLine(**d) for d in json.loads(row["shallow_lines"])],
        my_deep=PVLine(**json.loads(row["my_deep"])),
        my_shallow=PVLine(**json.loads(row["my_shallow"])),
        pv_after=json.loads(row["pv_after"]), pv_extended=bool(row["pv_extended"]),
    )


def get_analysis(conn: sqlite3.Connection, game_id: str, ply: int) -> Optional[MoveAnalysis]:
    row = conn.execute("SELECT * FROM analysis WHERE game_id=? AND ply=?", (game_id, ply)).fetchone()
    return row_to_analysis(row) if row else None


# --- leaks / cards --------------------------------------------------------------

def replace_leaks(conn: sqlite3.Connection, game_ids: Optional[list[str]],
                  findings: list[LeakFinding], today: str) -> None:
    """Replace the leaks of `game_ids` (None = all games) and make sure every
    leak position has an SRS card. Cards and calibration labels are keyed by
    (game_id, ply), so they survive re-detection after threshold tuning."""
    with conn:
        if game_ids is None:
            conn.execute("DELETE FROM leaks")
        else:
            conn.executemany("DELETE FROM leaks WHERE game_id=?", [(g,) for g in game_ids])
        conn.executemany(
            """INSERT INTO leaks (game_id, ply, leak_type, sub_type, refutation_ply, win_drop,
                                  phase, time_trouble, fen_before, my_move, best_move)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [(f.game_id, f.ply, f.leak_type, f.sub_type, f.refutation_ply, f.win_drop, f.phase,
              f.time_trouble, f.fen_before, f.my_move, f.best_move) for f in findings],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO srs_cards (game_id, ply, ease_factor, due_date) VALUES (?,?,?,?)",
            [(f.game_id, f.ply, config.SM2_START_EASE, today) for f in findings],
        )


def save_label(conn: sqlite3.Connection, game_id: str, ply: int, leak_type: str, verdict: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO calibration_labels VALUES (?,?,?,?,?)",
                     (game_id, ply, leak_type, verdict, now_iso()))


def status_counts(conn: sqlite3.Connection) -> dict:
    games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    by_status = dict(conn.execute(
        "SELECT analysis_status, COUNT(*) FROM games GROUP BY analysis_status").fetchall())
    by_tc = dict(conn.execute("SELECT tc_class, COUNT(*) FROM games GROUP BY tc_class").fetchall())
    return {
        "games": games, "by_status": by_status, "by_tc": by_tc,
        "analyzed_moves": conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0],
        "leaks": conn.execute("SELECT COUNT(*) FROM leaks").fetchone()[0],
        "cards": conn.execute("SELECT COUNT(*) FROM srs_cards").fetchone()[0],
    }
