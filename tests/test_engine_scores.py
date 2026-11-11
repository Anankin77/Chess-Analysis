import chess
import pytest
from chess.engine import Cp, Mate, MateGiven, PovScore

from chesscoach.engine import info_to_line, score_to_win_pct, win_pct


def test_win_pct_formula():
    assert win_pct(0) == pytest.approx(50.0)
    assert win_pct(100) == pytest.approx(59.1, abs=0.05)
    assert win_pct(300) == pytest.approx(75.1, abs=0.05)
    assert win_pct(-300) == pytest.approx(100 - win_pct(300))
    assert 99.9 < win_pct(3000) < 100 and 0 < win_pct(-3000) < 0.1


def test_perspective_flip_for_black():
    plus_three_white = PovScore(Cp(300), chess.WHITE)       # engine: White is +3
    assert score_to_win_pct(plus_three_white, chess.WHITE) == pytest.approx(win_pct(300))
    assert score_to_win_pct(plus_three_white, chess.BLACK) == pytest.approx(win_pct(-300))
    # Stockfish reports from the side to move; a Black-relative +3 is good for me as Black.
    plus_three_black = PovScore(Cp(300), chess.BLACK)
    assert score_to_win_pct(plus_three_black, chess.BLACK) == pytest.approx(win_pct(300))
    assert score_to_win_pct(plus_three_black, chess.WHITE) == pytest.approx(win_pct(-300))


def test_mate_handling():
    assert score_to_win_pct(PovScore(Mate(3), chess.WHITE), chess.WHITE) == 100.0
    assert score_to_win_pct(PovScore(Mate(3), chess.WHITE), chess.BLACK) == 0.0
    assert score_to_win_pct(PovScore(Mate(-2), chess.WHITE), chess.WHITE) == 0.0
    assert score_to_win_pct(PovScore(Mate(-2), chess.WHITE), chess.BLACK) == 100.0
    assert score_to_win_pct(PovScore(Mate(-1), chess.BLACK), chess.BLACK) == 0.0
    assert score_to_win_pct(PovScore(MateGiven, chess.BLACK), chess.BLACK) == 100.0
    assert score_to_win_pct(PovScore(Mate(0), chess.WHITE), chess.WHITE) == 0.0   # side to move is mated


def test_info_to_line_is_from_my_perspective():
    info = {"score": PovScore(Cp(-150), chess.WHITE), "pv": [chess.Move.from_uci("e7e5")]}
    line = info_to_line(info, chess.BLACK)
    assert (line.uci, line.cp, line.mate) == ("e7e5", 150, None)
    assert line.win > 50
    mated = info_to_line({"score": PovScore(Mate(2), chess.WHITE), "pv": [chess.Move.from_uci("e7e5")]}, chess.BLACK)
    assert (mated.cp, mated.mate, mated.win) == (None, -2, 0.0)
