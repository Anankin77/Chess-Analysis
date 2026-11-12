"""What a move is for, in a short phrase, from simple board features.

Rule based on purpose: captures, threats, forks, pins, defence, development,
files, outposts, pawn breaks, passed pawns. No text generation model."""

from __future__ import annotations

from typing import Optional

import chess

from .leaks import PIECE_VALUES, _attacker_value, see

NAMES = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
         chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}
CENTRE = chess.SquareSet([chess.D4, chess.E4, chess.D5, chess.E5])
BIG_CENTRE = chess.SquareSet(chess.BB_C3 | chess.BB_D3 | chess.BB_E3 | chess.BB_F3 | chess.BB_C4 | chess.BB_D4
                             | chess.BB_E4 | chess.BB_F4 | chess.BB_C5 | chess.BB_D5 | chess.BB_E5 | chess.BB_F5
                             | chess.BB_C6 | chess.BB_D6 | chess.BB_E6 | chess.BB_F6)


def _sq(square: int) -> str:
    return chess.square_name(square)


def _file(square: int) -> str:
    return chess.FILE_NAMES[chess.square_file(square)]


def _rel_rank(square: int, color: chess.Color) -> int:
    """Rank 1-8 seen from `color`'s side."""
    rank = chess.square_rank(square)
    return rank + 1 if color == chess.WHITE else 8 - rank


def is_passed(board: chess.Board, square: int, color: chess.Color) -> bool:
    f, r = chess.square_file(square), chess.square_rank(square)
    for sq in board.pieces(chess.PAWN, not color):
        if abs(chess.square_file(sq) - f) <= 1:
            if (color == chess.WHITE and chess.square_rank(sq) > r) or (color == chess.BLACK and chess.square_rank(sq) < r):
                return False
    return True


def _pawn_can_ever_attack(board: chess.Board, square: int, color: chess.Color) -> bool:
    """Could an enemy pawn ever attack `square` (a piece of `color` stands there)?"""
    f, r = chess.square_file(square), chess.square_rank(square)
    for sq in board.pieces(chess.PAWN, not color):
        if abs(chess.square_file(sq) - f) == 1:
            if (color == chess.WHITE and chess.square_rank(sq) > r) or (color == chess.BLACK and chess.square_rank(sq) < r):
                return True
    return False


def new_targets(board: chess.Board, move: chess.Move) -> list[tuple[int, chess.Piece, int]]:
    """(target square, target piece, attacker square) for every real new attack:
    target undefended or worth at least the attacker. Most valuable first."""
    me, opp = board.turn, not board.turn
    targets = [sq for sq, pc in board.piece_map().items() if pc.color == opp and pc.piece_type != chess.KING]
    before = {sq: set(board.attackers(me, sq)) for sq in targets}
    after = board.copy(stack=False)
    after.push(move)
    out = []
    for sq in targets:
        target = after.piece_at(sq)
        if target is None or target.color != opp:
            continue
        old = {move.to_square if s == move.from_square else s for s in before[sq]}
        for s in set(after.attackers(me, sq)) - old:
            if not after.attackers(opp, sq) or PIECE_VALUES[target.piece_type] >= _attacker_value(after.piece_at(s)):
                out.append((sq, target, s))
                break
    return sorted(out, key=lambda t: -PIECE_VALUES[t[1].piece_type])


def _hanging(board: chess.Board, color: chess.Color) -> set[int]:
    """Squares of `color`'s pieces that are attacked and not defended (kings excluded)."""
    return {sq for sq, pc in board.piece_map().items()
            if pc.color == color and pc.piece_type != chess.KING
            and board.attackers(not color, sq) and not board.attackers(color, sq)}


def move_ideas(board: chess.Board, move: chess.Move, prev_move: Optional[chess.Move] = None,
               limit: int = 2) -> list[str]:
    """Up to `limit` phrases, most concrete first. `board` is the position before the move;
    `prev_move` is the opponent's last move ONLY if it was a capture (to spot recaptures)."""
    me, opp = board.turn, not board.turn
    piece = board.piece_at(move.from_square)
    if piece is None:
        return []
    name, to = NAMES[piece.piece_type], _sq(move.to_square)
    after = board.copy(stack=False)
    after.push(move)
    ideas: list[str] = []

    if board.is_castling(move):
        ideas.append("castles: king to safety, rook into play")
    if move.promotion:
        ideas.append(f"promotes to a {NAMES[move.promotion]}")
    if board.is_capture(move):
        victim = board.piece_at(move.to_square)
        vname = NAMES[victim.piece_type] if victim else "pawn"
        gain = see(board, move)
        if prev_move is not None and prev_move.to_square == move.to_square and board.is_capture(move):
            ideas.append(f"recaptures on {to}")
        elif gain > 0:
            ideas.append(f"wins {'a' if vname != 'queen' else 'the'} {vname}" if gain >= PIECE_VALUES[victim.piece_type if victim else chess.PAWN]
                         else f"wins material on {to}")
        elif gain == 0 and victim and PIECE_VALUES[victim.piece_type] == PIECE_VALUES[piece.piece_type] and piece.piece_type != chess.PAWN:
            ideas.append(f"trades {name}s" if victim.piece_type == piece.piece_type else f"trades {name} for {vname}")
        elif gain < 0:
            ideas.append(f"gives up the {name} for the {vname} on {to}")
        else:
            ideas.append(f"takes the {vname} on {to}")
    if after.is_checkmate():
        return ["checkmate"]
    if board.gives_check(move):
        ideas.append("gives check")

    targets = new_targets(board, move)
    if board.is_capture(move) and see(board, move) <= 0:
        targets = []                                    # a trade or sacrifice: the capture is the story
    by_mover = [t for t in targets if t[2] == move.to_square and PIECE_VALUES[t[1].piece_type] >= 3]
    if len(by_mover) >= 2 or (len(by_mover) == 1 and board.gives_check(move)):
        names = [f"{NAMES[t[1].piece_type]} on {_sq(t[0])}" for t in by_mover[:2]]
        ideas.append("forks " + " and ".join((["king"] if board.gives_check(move) else []) + names))
    elif targets:
        sq, target, attacker = targets[0]
        tname = NAMES[target.piece_type]
        if piece.piece_type == chess.PAWN and target.piece_type == chess.PAWN and attacker == move.to_square:
            ideas.append(f"challenges the pawn on {_sq(sq)} (pawn break)")
        elif attacker != move.to_square:
            ideas.append(f"uncovers an attack on the {tname} on {_sq(sq)}")
        else:
            ideas.append(f"attacks the {tname} on {_sq(sq)}")

    for sq, pc in after.piece_map().items():                       # new pins against the king
        if pc.color == opp and pc.piece_type not in (chess.KING, chess.PAWN) and after.is_pinned(opp, sq) \
                and not board.is_pinned(opp, sq):
            ideas.append(f"pins the {NAMES[pc.piece_type]} on {_sq(sq)}")
            break

    hanging_before, hanging_after = _hanging(board, me), _hanging(after, me)
    lower_attackers = [a for a in board.attackers(opp, move.from_square)
                       if _attacker_value(board.piece_at(a)) < PIECE_VALUES[piece.piece_type]]
    if (move.from_square in hanging_before or lower_attackers) and move.to_square not in hanging_after \
            and not board.is_capture(move) and piece.piece_type != chess.PAWN:
        ideas.append(f"moves the attacked {name} to safety")
    saved = [sq for sq in hanging_before - hanging_after if sq != move.from_square and after.piece_at(sq)]
    if saved:
        best = max(saved, key=lambda s: PIECE_VALUES[after.piece_at(s).piece_type])
        ideas.append(f"defends the {NAMES[after.piece_at(best).piece_type]} on {_sq(best)}")

    # ---- positional purpose (only if nothing concrete filled the list) ----
    positional: list[str] = []
    pt, rel = piece.piece_type, _rel_rank(move.to_square, me)
    if pt == chess.PAWN and not board.is_capture(move):
        if is_passed(after, move.to_square, me) and rel >= 5:
            positional.append(f"pushes the passed {_file(move.to_square)}-pawn")
        elif move.to_square in CENTRE:
            positional.append("takes space in the centre")
        elif rel >= 4 and chess.square_file(move.to_square) in (0, 1, 2, 5, 6, 7):
            side = "kingside" if chess.square_file(move.to_square) >= 5 else "queenside"
            positional.append(f"gains space on the {side}")
        elif rel == 3 and chess.square_file(move.to_square) in (0, 7):
            king = after.king(me)
            if king is not None and abs(chess.square_file(king) - chess.square_file(move.to_square)) <= 2:
                positional.append("gives the king an escape square")
            else:
                inward = chess.square(1 if chess.square_file(move.to_square) == 0 else 6,
                                      chess.square_rank(move.to_square) + (1 if me == chess.WHITE else -1))
                positional.append(f"guards the {_sq(inward)} square")
    elif pt == chess.ROOK:
        f = chess.square_file(move.to_square)
        own = [s for s in after.pieces(chess.PAWN, me) if chess.square_file(s) == f]
        theirs = [s for s in after.pieces(chess.PAWN, opp) if chess.square_file(s) == f]
        partners = [s for s in (after.pieces(chess.ROOK, me) | after.pieces(chess.QUEEN, me))
                    if chess.square_file(s) == f and s != move.to_square]
        if rel == 7:
            positional.append("invades the seventh rank")
        elif partners and chess.square_file(move.from_square) != f:
            positional.append(f"doubles on the {_file(move.to_square)}-file")
        elif chess.square_file(move.from_square) == f:
            pass                                        # stays on its file: nothing to add
        elif not own and not theirs:
            positional.append(f"takes the open {_file(move.to_square)}-file")
        elif not own:
            positional.append(f"puts the rook on the half-open {_file(move.to_square)}-file")
        else:
            positional.append(f"brings the rook to the {_file(move.to_square)}-file")
    elif pt == chess.KNIGHT:
        supported = any(after.piece_at(s).piece_type == chess.PAWN for s in after.attackers(me, move.to_square))
        if rel >= 5 and supported and not _pawn_can_ever_attack(after, move.to_square, me):
            positional.append(f"plants the knight on the {to} outpost")
        elif move.to_square in BIG_CENTRE and move.from_square not in BIG_CENTRE:
            positional.append("brings the knight toward the centre")
        elif _rel_rank(move.from_square, me) == 1:
            positional.append("develops the knight")
        else:
            positional.append(f"reroutes the knight via {to}")
    elif pt == chess.BISHOP:
        if to in ("g2", "b2", "g7", "b7") and _rel_rank(move.from_square, me) == 1:
            positional.append("fianchettoes the bishop on the long diagonal")
        elif _rel_rank(move.from_square, me) == 1:
            positional.append("develops the bishop")
        else:
            positional.append(f"repositions the bishop to {to}")
    elif pt == chess.QUEEN:
        positional.append(f"brings the queen to {to}")
    elif pt == chess.KING and not board.is_castling(move):
        heavy = sum(len(after.pieces(p, c)) for p in (chess.QUEEN, chess.ROOK) for c in chess.COLORS)
        closer = (min(chess.square_distance(move.to_square, c) for c in CENTRE)
                  < min(chess.square_distance(move.from_square, c) for c in CENTRE))
        positional.append("activates the king" if heavy <= 2 and closer else f"steps the king to {to}")
    weak = ("reroutes", "repositions", "brings the queen", "steps the king", "brings the rook",
            "guards the", "gives the king an escape")
    if ideas:                                           # filler adds nothing next to a concrete idea
        positional = [p for p in positional if not p.startswith(weak)]
    if len(ideas) < limit and not board.is_capture(move):
        ideas += positional
    return ideas[:limit] or [f"puts the {name} on {to}"]


def describe(board: chess.Board, move: chess.Move, prev_move: Optional[chess.Move] = None) -> str:
    return " and ".join(move_ideas(board, move, prev_move))


def plan_ideas(fen: str, pv: list[str], max_steps: int = 3, skip_first: bool = False) -> str:
    """The side to move's next moves of an engine line with what each is for:
    '**Nc3** brings the knight toward the centre; then **Rfd1** takes the open d-file'."""
    board = chess.Board(fen)
    me = board.turn
    steps: list[str] = []
    prev: Optional[chess.Move] = None                  # last move, only if it was a capture
    for i, uci in enumerate(pv):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        if board.turn == me and not (skip_first and i == 0):
            steps.append(f"**{board.san(move)}** {describe(board, move, prev)}")
            if len(steps) == max_steps:
                break
        prev = move if board.is_capture(move) else None
        board.push(move)
    return "; then ".join(steps)
