import chess
import pytest

from chesscoach.ideas import describe, plan_ideas


def d(fen, uci, prev=None):
    return describe(chess.Board(fen), chess.Move.from_uci(uci), chess.Move.from_uci(prev) if prev else None)


@pytest.mark.parametrize("fen,uci,expected", [
    (chess.STARTING_FEN, "e2e4", "takes space in the centre"),
    (chess.STARTING_FEN, "g1f3", "brings the knight toward the centre"),
    (chess.STARTING_FEN, "g2g3", "puts the pawn on g3"),
    ("rnbqkbnr/pppppppp/8/8/8/6P1/PPPPPP1P/RNBQKBNR w KQkq - 0 2", "f1g2", "fianchettoes the bishop on the long diagonal"),
    ("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3", "f1b5", "attacks the knight on c6 and develops the bishop"),
    ("r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4", "e1g1", "castles: king to safety, rook into play"),
    ("4k3/8/8/3p4/8/8/8/3RK3 w - - 0 1", "d1d5", "wins a pawn"),
    ("3rk3/8/8/3r4/8/8/8/3RK2R w - - 0 1", "d1d5", "trades rooks"),
    ("4k3/8/4p3/3p4/8/8/8/3RK3 w - - 0 1", "d1d5", "gives up the rook for the pawn on d5"),
    ("r3k3/8/8/8/8/2N5/8/4K3 w - - 0 1", "c3b5", "reroutes the knight via b5"),
    ("r3k3/8/8/8/8/8/3N4/4K3 w - - 0 1", "d2b3", "reroutes the knight via b3"),
    ("r3k2r/8/8/8/3N4/8/8/4K3 w - - 0 1", "d4c6", "reroutes the knight via c6"),
    ("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "b5c7", "gives check and forks king and rook on a8"),
    ("4k3/8/2q5/8/8/8/8/1B2K3 w - - 0 1", "b1e4", "attacks the queen on c6 and develops the bishop"),
    ("4k3/4n3/8/8/8/8/8/4RK2 w - - 0 1", "f1g1", "steps the king to g1"),
    ("4k3/8/8/8/8/8/5PPP/R5K1 w - - 0 1", "a1a7", "invades the seventh rank"),
    ("4k3/p7/8/8/8/8/5PPP/3R2K1 w - - 0 1", "d1d2", "puts the rook on d2"),
    ("4k3/p7/8/8/8/8/P4PPP/3R2K1 w - - 0 1", "d1e1", "gives check and takes the open e-file"),
    ("4k3/pp6/8/8/8/8/PP3PPP/5RK1 w - - 0 1", "f1d1", "takes the open d-file"),
    ("4k3/8/8/3P4/8/8/8/4K3 w - - 0 1", "d5d6", "pushes the passed d-pawn"),
    ("4k3/8/8/8/8/8/8/4K3 w - - 0 1", "e1d2", "activates the king"),
    ("4k3/8/8/3p4/8/8/2P5/4K3 w - - 0 1", "c2c4", "challenges the pawn on d5 (pawn break) and gains space on the queenside"),
    ("4k3/8/8/8/1b6/8/3N4/4K2R w - - 0 1", "d2f3", "brings the knight toward the centre"),
    ("4k3/8/8/8/8/5q2/8/3QK3 w - - 0 1", "d1f3", "wins the queen"),
])
def test_describe(fen, uci, expected):
    assert d(fen, uci) == expected


def test_recapture_pin_defence_and_escape():
    assert d("3rk3/8/8/3R4/8/8/8/3RK3 b - - 0 1", "d8d5", prev="d1d5").startswith("recaptures on d5")
    # Bb5 pins the knight to the king
    assert "pins the knight on c6" in d("r1bqkbnr/ppp2ppp/2np4/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 4", "f1b5")
    # the knight on e5 hangs; Nf3 defends it
    assert d("4k3/8/3p4/4N3/8/8/8/4K1N1 w - - 0 1", "g1f3") == "defends the knight on e5 and brings the knight toward the centre"
    # attacked knight steps away
    assert "moves the attacked knight to safety" in d("4k3/8/3p4/4N3/8/8/8/4K3 w - - 0 1", "e5f3")


def test_knight_outpost():
    fen = "4k3/8/8/8/4P3/8/3N4/4K3 w - - 0 1"       # no black pawns can ever hit d5... knight needs pawn support
    assert d("4k3/8/8/8/2N1P3/8/8/4K3 w - - 0 1", "c4d6") == "gives check"
    assert d("4k3/7p/8/8/4P3/2N5/8/4K3 w - - 0 1", "c3d5") == "plants the knight on the d5 outpost"
    assert "outpost" not in d("4k3/8/2p5/8/4P3/2N5/8/4K3 w - - 0 1", "c3d5")      # ...c6 pawn can hit d5
    assert fen


def test_plan_ideas():
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    plan = plan_ideas(fen, ["f1b5", "a7a6", "b5c6", "d7c6", "e1g1"])
    assert plan == ("**Bb5** attacks the knight on c6 and develops the bishop; then **Bxc6** trades bishop for knight; "
                    "then **O-O** castles: king to safety, rook into play")
    assert plan_ideas(fen, ["f1b5", "a7a6", "b5a4"], skip_first=True) == "**Ba4** moves the attacked bishop to safety"
    assert plan_ideas(fen, ["zzzz"]) == ""


def test_rook_pawn_moves():
    assert d("6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1", "h2h3") == "gives the king an escape square"
    assert d("6k1/5ppp/8/8/8/8/P4PPP/6K1 w - - 0 1", "a2a3") == "guards the b4 square"
    assert d("6k1/p4ppp/8/8/8/8/5PPP/6K1 b - - 0 1", "a7a6") == "guards the b5 square"
