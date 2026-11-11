"""Leak detection: forcing bias and horizon errors.

Pure functions over stored engine analysis - no engine needed, so after
tuning thresholds in config.py just run:   python -m chesscoach.leaks
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Optional

import chess

from . import config, db
from .models import LeakFinding, MoveAnalysis

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
_KING_AS_ATTACKER = 100


# --- forcing / quiet ---------------------------------------------------------------------

def _attacker_value(piece: chess.Piece) -> int:
    return _KING_AS_ATTACKER if piece.piece_type == chess.KING else PIECE_VALUES[piece.piece_type]


def creates_new_attack(board: chess.Board, move: chess.Move) -> bool:
    """The move makes one of my pieces newly attack (directly or by discovery) an
    opponent piece that is undefended or worth at least as much as the attacker."""
    me, opp = board.turn, not board.turn
    targets = [sq for sq, pc in board.piece_map().items() if pc.color == opp and pc.piece_type != chess.KING]
    before = {sq: set(board.attackers(me, sq)) for sq in targets}
    after = board.copy(stack=False)
    after.push(move)
    for sq in targets:
        target = after.piece_at(sq)
        if target is None or target.color != opp:  # it was just captured
            continue
        old = {move.to_square if s == move.from_square else s for s in before[sq]}
        new = set(after.attackers(me, sq)) - old
        if not new:
            continue
        if not after.attackers(opp, sq):
            return True
        if any(PIECE_VALUES[target.piece_type] >= _attacker_value(after.piece_at(s)) for s in new):
            return True
    return False


def is_forcing(board: chess.Board, move: chess.Move) -> bool:
    """Capture, check, or a new direct attack. `board` is the position before the move."""
    return board.is_capture(move) or board.gives_check(move) or creates_new_attack(board, move)


def is_quiet(board: chess.Board, move: chess.Move) -> bool:
    return not is_forcing(board, move)


# --- static exchange evaluation ------------------------------------------------------------

def see(board: chess.Board, move: chess.Move) -> int:
    """Static exchange evaluation of a capture, in pawns, for the side making it.
    Uses legal moves, so pins and x-rays are handled."""
    if board.is_en_passant(move):
        gain = 1
    else:
        victim = board.piece_at(move.to_square)
        gain = PIECE_VALUES[victim.piece_type] if victim else 0
    if move.promotion:
        gain += PIECE_VALUES[move.promotion] - 1
    board.push(move)
    try:
        replies = [m for m in board.generate_legal_moves(to_mask=chess.BB_SQUARES[move.to_square])
                   if m.promotion in (None, chess.QUEEN)]
        if not replies:
            return gain
        reply = min(replies, key=lambda m: _attacker_value(board.piece_at(m.from_square)))
        return gain - max(0, see(board, reply))
    finally:
        board.pop()


def has_winning_capture(board: chess.Board) -> bool:
    return any(see(board, m) > 0 for m in board.generate_legal_captures())


def material(board: chess.Board, color: chess.Color) -> int:
    return sum(PIECE_VALUES[pt] * len(board.pieces(pt, color)) for pt in PIECE_VALUES)


def balance(board: chess.Board, me: chess.Color) -> int:
    return material(board, me) - material(board, not me)


# --- refutation ply ------------------------------------------------------------------------

def find_refutation(fen_before: str, my_move: str, pv_after: list[str],
                    mate_in_against_me: Optional[int] = None,
                    min_drop: int = config.REFUTATION_MATERIAL_DROP) -> tuple[Optional[int], str]:
    """(refutation_ply, type) with type in material | mate | positional.

    Ply 1 is the opponent's first reply after my move. My material balance
    (relative to before my move) is sampled only at quiet points: positions
    where the side to move has no capture winning material by SEE, so a
    half-finished exchange never counts. The refutation ply is the start of
    the final run of quiet points at which I am >= `min_drop` pawns down.
    `mate_in_against_me`: N when the engine score after my move is "mated in N"."""
    board = chess.Board(fen_before)
    me = board.turn
    baseline = balance(board, me)
    board.push_uci(my_move)

    quiet_points: list[tuple[int, int]] = []
    mate_ply: Optional[int] = None
    for ply in range(0, len(pv_after) + 1):
        if ply > 0:
            try:
                board.push_uci(pv_after[ply - 1])
            except ValueError:
                break
        if board.is_checkmate():
            if board.turn == me:
                mate_ply = ply
            break
        if not has_winning_capture(board):
            quiet_points.append((ply, balance(board, me) - baseline))

    material_ply: Optional[int] = None
    if quiet_points and quiet_points[-1][1] <= -min_drop:
        for ply, delta in reversed(quiet_points):
            if delta > -min_drop:
                break
            material_ply = ply

    if mate_ply is None and mate_in_against_me:
        mate_ply = 2 * mate_in_against_me - 1  # opponent moves on odd plies
    if mate_ply is not None:
        return (min(mate_ply, material_ply) if material_ply is not None else mate_ply), "mate"
    if material_ply is not None:
        return material_ply, "material"
    return None, "positional"


# --- context: phase, time trouble -------------------------------------------------------------

def game_phase(board: chess.Board) -> str:
    """Non-pawn material of both sides on the 1/3/3/5/9 scale (max 62)."""
    npm = sum(PIECE_VALUES[pt] * len(board.pieces(pt, c))
              for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN) for c in chess.COLORS)
    if board.fullmove_number <= config.OPENING_MAX_FULLMOVE and npm >= config.OPENING_MIN_MATERIAL:
        return "opening"
    if npm <= config.ENDGAME_MAX_MATERIAL:
        return "endgame"
    return "middlegame"


def time_trouble(clock: Optional[float], base_seconds: Optional[int], increment_seconds: Optional[int]) -> str:
    """yes | no | unknown. Unknown (no clock) is excluded from time-trouble stats."""
    if clock is None or base_seconds is None:
        return "unknown"
    budget = base_seconds + config.TIME_TROUBLE_MOVES * (increment_seconds or 0)
    return "yes" if clock < config.TIME_TROUBLE_RATIO * budget else "no"


# --- detectors ------------------------------------------------------------------------------------

def is_forcing_bias(a: MoveAnalysis) -> bool:
    if a.win_drop < config.WIN_PCT_BLUNDER_DROP:
        return False
    board = chess.Board(a.fen_before)
    best = a.deep_lines[0]
    if not is_forcing(board, chess.Move.from_uci(a.my_move)):
        return False
    if not is_quiet(board, chess.Move.from_uci(best.uci)):
        return False
    forcing = [ln.win for ln in a.deep_lines if is_forcing(board, chess.Move.from_uci(ln.uci))]
    quiet = [ln.win for ln in a.deep_lines if is_quiet(board, chess.Move.from_uci(ln.uci))]
    return not forcing or max(quiet) - max(forcing) >= config.FORCING_LEAK_MARGIN


def horizon_leak(a: MoveAnalysis) -> Optional[tuple[Optional[int], str]]:
    """(refutation_ply, type) if the move is a horizon leak, else None."""
    if a.win_drop < config.WIN_PCT_BLUNDER_DROP:
        return None
    if a.my_shallow.win < a.best_shallow_win - config.HORIZON_SHALLOW_TOLERANCE:
        return None  # already looked bad at shallow depth: not a horizon problem
    mated = a.my_deep.mate is not None and a.my_deep.win == 0.0
    ply, kind = find_refutation(a.fen_before, a.my_move, a.pv_after,
                                abs(a.my_deep.mate) if mated else None)
    if ply is not None and ply < config.HORIZON_MIN_REFUTATION_PLY:
        return None
    return ply, kind


def detect(a: MoveAnalysis, clock: Optional[float] = None, base_seconds: Optional[int] = None,
           increment_seconds: Optional[int] = None) -> list[LeakFinding]:
    if a.win_drop < config.WIN_PCT_BLUNDER_DROP:
        return []
    board = chess.Board(a.fen_before)
    common = dict(game_id=a.game_id, ply=a.ply, win_drop=round(a.win_drop, 2), phase=game_phase(board),
                  time_trouble=time_trouble(clock, base_seconds, increment_seconds),
                  fen_before=a.fen_before, my_move=a.my_move, best_move=a.deep_lines[0].uci)
    found: list[LeakFinding] = []
    if is_forcing_bias(a):
        found.append(LeakFinding(leak_type="forcing_bias", sub_type=None, refutation_ply=None, **common))
    horizon = horizon_leak(a)
    if horizon is not None:
        found.append(LeakFinding(leak_type="horizon", sub_type=horizon[1], refutation_ply=horizon[0], **common))
    return found


# --- database pass (main process) -----------------------------------------------------------------

def decision_clock(clocks: dict[int, Optional[float]], ply: int, base: Optional[int],
                   inc: Optional[int]) -> Optional[float]:
    """My clock when I started thinking about move `ply`: the clock after my
    previous move plus the increment (the base time for my first move). Falls
    back to the clock recorded on the move itself."""
    if ply < 2:
        return float(base) if base is not None and clocks.get(ply) is not None else clocks.get(ply)
    prev = clocks.get(ply - 2)
    if prev is not None:
        return prev + (inc or 0)
    return clocks.get(ply)


def redetect(conn: sqlite3.Connection, game_ids: Optional[list[str]] = None) -> int:
    """Rebuild leaks (and missing SRS cards) from stored analysis. Returns leak count."""
    if game_ids is not None and not game_ids:
        return 0
    sql = ("SELECT a.*, g.base_seconds, g.increment_seconds FROM analysis a "
           "JOIN games g ON g.id = a.game_id WHERE a.win_drop >= ?")
    params: list = [config.WIN_PCT_BLUNDER_DROP]
    if game_ids is not None:
        sql += f" AND a.game_id IN ({','.join('?' * len(game_ids))})"
        params += game_ids
    rows = conn.execute(sql, params).fetchall()
    clocks: dict[str, dict[int, Optional[float]]] = {}
    findings: list[LeakFinding] = []
    for row in rows:
        gid = row["game_id"]
        if gid not in clocks:
            clocks[gid] = dict(conn.execute(
                "SELECT ply, clock_seconds FROM moves WHERE game_id=?", (gid,)).fetchall())
        clock = decision_clock(clocks[gid], row["ply"], row["base_seconds"], row["increment_seconds"])
        findings += detect(db.row_to_analysis(row), clock, row["base_seconds"], row["increment_seconds"])
    db.replace_leaks(conn, game_ids, findings, date.today().isoformat())
    return len(findings)


def main() -> None:
    conn = db.connect()
    total = redetect(conn)
    print(f"{total} leaks detected with the current thresholds:")
    for row in conn.execute("SELECT leak_type, COALESCE(sub_type,'') AS s, COUNT(*) AS n FROM leaks "
                            "GROUP BY 1, 2 ORDER BY n DESC"):
        print(f"  {row['leak_type']:<13} {row['s']:<11} {row['n']}")


if __name__ == "__main__":
    main()
