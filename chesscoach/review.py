"""Single-game review: analyze one game now and walk through my decisions.

Everything here is computed from engine numbers with fixed templates: the
"plan" is the engine's main line put into words, not generated prose."""

from __future__ import annotations

import sqlite3
from typing import Callable, Optional

import chess

from . import config, db, leaks
from .models import MoveAnalysis

PIECE_NAMES = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
               chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}

# (minimum win % lost, label, symbol)
GRADES = [(20.0, "Blunder", "??"), (10.0, "Mistake", "?"), (5.0, "Inaccuracy", "?!"),
          (1.0, "Good", ""), (0.0, "Best", "")]


def grade(drop: float) -> tuple[str, str]:
    for threshold, label, symbol in GRADES:
        if drop >= threshold:
            return label, symbol
    return "Best", ""


def move_kind(board: chess.Board, move: chess.Move) -> str:
    if board.is_capture(move):
        return "capture"
    if board.gives_check(move):
        return "check"
    if leaks.creates_new_attack(board, move):
        return "threat"
    return "quiet move"


def plan_text(fen: str, pv: list[str], max_steps: int = 3) -> str:
    """The side to move's next moves in the engine line, in words.
    Consecutive moves of one piece become a route: 'knight d2→f1→e3'."""
    board = chess.Board(fen)
    me = board.turn
    steps: list[str] = []
    route_end: Optional[int] = None
    for uci in pv:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        if board.turn == me:
            piece = board.piece_at(move.from_square)
            name = PIECE_NAMES[piece.piece_type]
            to = chess.square_name(move.to_square)
            if board.is_castling(move):
                text, route_end = ("castle kingside" if chess.square_file(move.to_square) > 4 else "castle queenside"), None
            elif board.is_capture(move):
                victim = board.piece_at(move.to_square)
                who = f"{chess.FILE_NAMES[chess.square_file(move.from_square)]}-pawn" if piece.piece_type == chess.PAWN else name
                text = f"{who} takes {'the ' + PIECE_NAMES[victim.piece_type] + ' on ' if victim else 'on '}{to}"
                route_end = None
            elif route_end == move.from_square and steps:
                steps[-1] += f"→{to}"
                route_end = move.to_square
                board.push(move)
                continue
            else:
                text = (f"{chess.FILE_NAMES[chess.square_file(move.from_square)]}-pawn to {to}"
                        if piece.piece_type == chess.PAWN else f"{name} {chess.square_name(move.from_square)}→{to}")
                route_end = move.to_square
            if move.promotion:
                text += f" promoting to a {PIECE_NAMES[move.promotion]}"
            if board.gives_check(move):
                text += " with check"
            if len(steps) == max_steps:
                break
            steps.append(text)
        board.push(move)
    return ", then ".join(steps)


def analyze_now(conn: sqlite3.Connection, game_id: str,
                progress: Callable[[int, int], None] = lambda done, total: None) -> int:
    """Analyze one game in this process (about a minute). Returns analyzed move count."""
    from . import engine as eng
    from .book import default_book

    row = conn.execute("SELECT pgn, my_color FROM games WHERE id=?", (game_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown game {game_id}")
    engine = eng.open_engine()
    try:
        result = eng.analyze_game(engine, default_book(), game_id, row["pgn"], row["my_color"], on_move=progress)
    finally:
        engine.quit()
    db.save_analysis_batch(conn, [result])
    if result.error:
        raise RuntimeError(result.error)
    leaks.redetect(conn, [game_id])
    return len(result.moves)


def _comment(a: MoveAnalysis, board: chess.Board, label: str, my_kind: str, best_kind: str,
             best_san: str, second_gap: float, plan: str, leak_rows: list[sqlite3.Row]) -> str:
    drop = a.win_drop
    parts: list[str] = []
    if drop < 1.0:
        if best_kind == "quiet move" and second_gap >= config.FORCING_LEAK_MARGIN:
            parts.append(f"Only good move, and a quiet one: well found (next best is {second_gap:.0f} win % worse).")
        elif second_gap >= config.WIN_PCT_BLUNDER_DROP:
            parts.append(f"The only move that holds the evaluation (next best is {second_gap:.0f} win % worse).")
        else:
            parts.append("Engine's first choice.")
    elif drop < 5.0:
        parts.append(f"Fine. The engine prefers {best_san} by {drop:.1f} win %.")
    else:
        parts.append(f"{label}: costs {drop:.0f} win %. Best was the {best_kind} **{best_san}**.")
        if my_kind != "quiet move" and best_kind == "quiet move":
            parts.append(f"You chose a {my_kind} where the position asked for a quiet move.")
        if plan:
            parts.append(f"Plan after {best_san}: {plan}.")
        for lk in leak_rows:
            if lk["leak_type"] == "horizon" and lk["sub_type"] in ("material", "mate"):
                what = "you get mated" if lk["sub_type"] == "mate" else "material goes"
                parts.append(f"Horizon error: it looked fine at depth {a.shallow_depth}, but {what} on ply "
                             f"{lk['refutation_ply']} of the reply. Replay the game line.")
            elif lk["leak_type"] == "horizon":
                parts.append(f"Horizon error (positional): fine at depth {a.shallow_depth}, worse deeper, no single tactic.")
    return " ".join(parts)


def game_review(conn: sqlite3.Connection, game_id: str) -> Optional[dict]:
    """Per-move review rows plus a summary, or None if the game is not analyzed."""
    game = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    rows = conn.execute("SELECT * FROM analysis WHERE game_id=? ORDER BY ply", (game_id,)).fetchall()
    if game is None or game["analysis_status"] != "done":
        return None
    clocks = dict(conn.execute("SELECT ply, clock_seconds FROM moves WHERE game_id=?", (game_id,)).fetchall())
    leak_map: dict[int, list[sqlite3.Row]] = {}
    for lk in conn.execute("SELECT * FROM leaks WHERE game_id=?", (game_id,)):
        leak_map.setdefault(lk["ply"], []).append(lk)

    moves = []
    for row in rows:
        a = db.row_to_analysis(row)
        board = chess.Board(a.fen_before)
        my_move, best = chess.Move.from_uci(a.my_move), a.deep_lines[0]
        best_move = chess.Move.from_uci(best.uci)
        label, symbol = grade(a.win_drop)
        my_kind, best_kind = move_kind(board, my_move), move_kind(board, best_move)
        second_gap = best.win - a.deep_lines[1].win if len(a.deep_lines) > 1 else 0.0
        plan = plan_text(a.fen_before, best.pv)
        clock = leaks.decision_clock(clocks, a.ply, game["base_seconds"], game["increment_seconds"])
        dots = "." if board.turn == chess.WHITE else "…"
        best_san = board.san(best_move)
        moves.append({
            "ply": a.ply, "move_no": board.fullmove_number,
            "label": f"{board.fullmove_number}{dots}{board.san(my_move)}{symbol}",
            "san": board.san(my_move), "best_san": best_san, "grade": label,
            "my_win": round(a.my_deep.win, 1), "best_win": round(a.best_deep_win, 1), "drop": round(a.win_drop, 1),
            "my_kind": my_kind, "best_kind": best_kind, "plan": plan,
            "time_trouble": leaks.time_trouble(clock, game["base_seconds"], game["increment_seconds"]),
            "leaks": [lk["leak_type"] + (f":{lk['sub_type']}" if lk["sub_type"] else "") for lk in leak_map.get(a.ply, [])],
            "comment": _comment(a, board, label, my_kind, best_kind, best_san, second_gap, plan, leak_map.get(a.ply, [])),
        })

    quiet_needed = [m for m in moves if m["best_kind"] == "quiet move"]
    counts = {g[1]: sum(1 for m in moves if m["grade"] == g[1]) for g in GRADES}
    summary = {
        "analyzed": len(moves), "book_plies": game["book_plies"] or 0, "counts": counts,
        "avg_loss": round(sum(m["drop"] for m in moves) / len(moves), 2) if moves else 0.0,
        "quiet_needed": len(quiet_needed),
        "quiet_found": sum(1 for m in quiet_needed if m["drop"] < 5.0),
        "forced_instead": sum(1 for m in quiet_needed if m["drop"] >= 5.0 and m["my_kind"] != "quiet move"),
        "leaks": sum(len(m["leaks"]) for m in moves),
        "worst": max(moves, key=lambda m: m["drop"]) if moves else None,
        "tt_loss": round(sum(m["drop"] for m in moves if m["time_trouble"] == "yes"), 1),
    }
    return {"game": dict(game), "moves": moves, "summary": summary}


def lessons(review: dict) -> list[str]:
    """Up to four takeaways for this game, ordered by what cost most."""
    s, moves = review["summary"], review["moves"]
    out: list[str] = []
    if not moves:
        return ["The whole game stayed in the opening book: nothing to review."]
    worst = s["worst"]
    if worst and worst["drop"] >= 5:
        out.append(f"**Turning point: {worst['label']}** (−{worst['drop']:.0f} win %). {worst['comment']}")
    if s["quiet_needed"]:
        line = (f"**Quiet moves:** the best move was quiet {s['quiet_needed']} times; you kept the evaluation "
                f"{s['quiet_found']} times.")
        if s["forced_instead"]:
            line += f" {s['forced_instead']} time(s) you forced matters instead and paid for it."
        out.append(line)
    horizon = [m for m in moves if any(x.startswith("horizon") for x in m["leaks"])]
    if horizon:
        out.append("**Calculation depth:** " + ", ".join(m["label"] for m in horizon) +
                   " looked fine a few moves deep and failed further on. Calculate until the position is quiet.")
    total = sum(m["drop"] for m in moves)
    if s["tt_loss"] >= 10 and total > 0:
        out.append(f"**Clock:** {100 * s['tt_loss'] / total:.0f}% of the win % you lost came in time trouble.")
    if not out:
        out.append(f"**Clean game:** no move lost 5 win % or more (average loss {s['avg_loss']:.1f} per move).")
    return out
