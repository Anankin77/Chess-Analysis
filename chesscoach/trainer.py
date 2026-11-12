"""Spaced repetition (SM-2) over positions taken from my own leaks."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import chess

from . import config, db
from .models import PVLine

GRADES = {"Again": 1, "Hard": 3, "Good": 4, "Easy": 5}


@dataclass
class CardState:
    repetition: int = 0
    interval: int = 0  # days
    ease_factor: float = config.SM2_START_EASE
    due_date: Optional[date] = None


def sm2(state: CardState, grade: int, today: Optional[date] = None) -> CardState:
    """One SM-2 review. Grades: Again 1, Hard 3, Good 4, Easy 5."""
    if grade not in GRADES.values():
        raise ValueError(f"grade must be one of {sorted(GRADES.values())}")
    today = today or date.today()
    if grade < 3:
        repetition, interval = 0, 1
    else:
        if state.repetition == 0:
            interval = 1
        elif state.repetition == 1:
            interval = 6
        else:
            interval = round(state.interval * state.ease_factor)
        repetition = state.repetition + 1
    ease = state.ease_factor + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    ease = max(config.SM2_MIN_EASE, ease)
    return CardState(repetition, interval, round(ease, 4), today + timedelta(days=interval))


# --- answer checking -------------------------------------------------------------------

def accepted_moves(lines: list[PVLine], tolerance: float = config.PUZZLE_ACCEPT_TOLERANCE) -> list[PVLine]:
    """Every stored MultiPV move within `tolerance` win % of the best one."""
    if not lines:
        return []
    best = max(line.win for line in lines)
    return [line for line in lines if line.win >= best - tolerance]


def is_correct(uci: str, lines: list[PVLine], tolerance: float = config.PUZZLE_ACCEPT_TOLERANCE) -> bool:
    return any(line.uci == uci for line in accepted_moves(lines, tolerance))


# --- card queue ---------------------------------------------------------------------------

def card_queue(conn: sqlite3.Connection, leak_type: Optional[str] = None,
               include_not_due: bool = False, today: Optional[date] = None) -> list[sqlite3.Row]:
    """Cards whose position is currently flagged as a leak; due cards first."""
    today_s = (today or date.today()).isoformat()
    sql = """SELECT c.*, GROUP_CONCAT(l.leak_type || COALESCE(':' || l.sub_type, ''), ', ') AS leak_labels,
                    MAX(l.win_drop) AS win_drop
             FROM srs_cards c JOIN leaks l ON l.game_id = c.game_id AND l.ply = c.ply
             WHERE (? IS NULL OR EXISTS (SELECT 1 FROM leaks x WHERE x.game_id = c.game_id
                                         AND x.ply = c.ply AND x.leak_type = ?))"""
    params: list = [leak_type, leak_type]
    if not include_not_due:
        sql += " AND c.due_date <= ?"
        params.append(today_s)
    sql += " GROUP BY c.id ORDER BY c.due_date, c.reviews, c.id"
    return conn.execute(sql, params).fetchall()


def review(conn: sqlite3.Connection, card_id: int, grade: int, today: Optional[date] = None,
           correct: Optional[bool] = None) -> CardState:
    today = today or date.today()
    row = conn.execute("SELECT * FROM srs_cards WHERE id=?", (card_id,)).fetchone()
    new = sm2(CardState(row["repetition"], row["interval"], row["ease_factor"]), grade, today)
    with conn:
        conn.execute(
            """UPDATE srs_cards SET repetition=?, interval=?, ease_factor=?, due_date=?, last_grade=?,
               last_reviewed=?, reviews=reviews+1, lapses=lapses+? WHERE id=?""",
            (new.repetition, new.interval, new.ease_factor, new.due_date.isoformat(), grade,
             today.isoformat(), int(grade < 3), card_id))
        conn.execute("INSERT INTO review_log (card_id, game_id, ply, reviewed_on, grade, correct) VALUES (?,?,?,?,?,?)",
                     (card_id, row["game_id"], row["ply"], today.isoformat(), grade,
                      None if correct is None else int(correct)))
    return new


def training_stats(conn: sqlite3.Connection, today: Optional[date] = None) -> dict:
    """Numbers for the trainer header: today's work, day streak, accuracy per leak type (last 30 days)."""
    today = today or date.today()
    t = today.isoformat()
    n, ok = conn.execute("SELECT COUNT(*), COALESCE(SUM(correct), 0) FROM review_log WHERE reviewed_on=?",
                         (t,)).fetchone()
    days = {r[0] for r in conn.execute("SELECT DISTINCT reviewed_on FROM review_log")}
    streak, day = 0, today if t in days else today - timedelta(days=1)
    while day.isoformat() in days:
        streak, day = streak + 1, day - timedelta(days=1)
    since = (today - timedelta(days=30)).isoformat()
    by_type = {}
    for leak_type, total, right in conn.execute(
            """SELECT l.leak_type, COUNT(*), COALESCE(SUM(r.correct), 0) FROM review_log r
               JOIN leaks l ON l.game_id = r.game_id AND l.ply = r.ply
               WHERE r.reviewed_on >= ? AND r.correct IS NOT NULL GROUP BY l.leak_type""", (since,)):
        by_type[leak_type] = (int(right), int(total))
    return {"today": int(n), "today_correct": int(ok), "streak": streak, "by_type": by_type}


# --- puzzle payload for the board component -------------------------------------------------

def _san_line(board: chess.Board, ucis: list[str], limit: int = 8) -> str:
    try:
        return board.variation_san([chess.Move.from_uci(u) for u in ucis[:limit]])
    except (ValueError, AssertionError):
        return ""


def _walk(board: chess.Board, ucis: list[str], limit: int = 14) -> dict:
    """Positions along a line so the client can step through it without chess logic."""
    b = board.copy(stack=False)
    out: dict = {"ucis": [], "sans": [], "fens": []}
    for uci in ucis[:limit]:
        try:
            move = chess.Move.from_uci(uci)
            if move not in b.legal_moves:
                break
            dots = f"{b.fullmove_number}." if b.turn == chess.WHITE else (f"{b.fullmove_number}…" if not out["sans"] else "")
            out["sans"].append(dots + b.san(move))
            b.push(move)
        except ValueError:
            break
        out["ucis"].append(uci)
        out["fens"].append(b.fen())
    return out


def puzzle_payload(conn: sqlite3.Connection, game_id: str, ply: int) -> Optional[dict]:
    """Everything the client-side board needs to check an answer and replay the
    lines without a server round trip."""
    a = db.get_analysis(conn, game_id, ply)
    if a is None:
        return None
    board = chess.Board(a.fen_before)
    accepted = {line.uci for line in accepted_moves(a.deep_lines)}
    return {
        "fen": a.fen_before,
        "orientation": "white" if board.turn == chess.WHITE else "black",
        "legal": [{"uci": m.uci(), "san": board.san(m)} for m in board.legal_moves],
        "accepted": sorted(accepted),
        "lines": [{"uci": ln.uci, "san": board.san(chess.Move.from_uci(ln.uci)), "win": round(ln.win, 1),
                   "ok": ln.uci in accepted, **_walk(board, ln.pv)} for ln in a.deep_lines],
        "played": {"uci": a.my_move, "san": board.san(chess.Move.from_uci(a.my_move)),
                   "win": round(a.my_deep.win, 1), **_walk(board, [a.my_move] + a.pv_after)},
    }
