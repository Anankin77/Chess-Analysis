import chess
import pytest

from chesscoach import leaks
from chesscoach.leaks import find_refutation, game_phase, is_forcing, is_quiet, see, time_trouble
from chesscoach.models import MoveAnalysis, PVLine


def mv(uci):
    return chess.Move.from_uci(uci)


# --- forcing vs quiet ---------------------------------------------------------------------
@pytest.mark.parametrize("fen,uci,forcing,why", [
    (chess.STARTING_FEN, "e2e4", False, "plain pawn push"),
    (chess.STARTING_FEN, "g1f3", False, "development"),
    ("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "e4d5", True, "capture"),
    ("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "f1b5", True, "check"),
    ("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "b1c3", False,
     "attacks a defended pawn with a knight"),
    ("8/4k3/3q4/8/8/2N5/8/4K3 w - - 0 1", "c3e4", True, "attacks a defended but more valuable piece"),
    ("4k3/4p3/8/8/8/8/8/R3K3 w - - 0 1", "a1a7", False, "rook attacks a defended pawn"),
    ("4k3/8/8/8/7p/8/8/R3K3 w - - 0 1", "a1a4", True, "rook attacks an undefended pawn"),
    ("4k3/8/8/3q4/8/8/3N4/3RK3 w - - 0 1", "d2b3", True, "discovered attack on the queen"),
    ("4k3/8/8/3q4/8/8/8/3RK3 w - - 0 1", "d1d2", False, "queen was already attacked: nothing new"),
    ("4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", "e1g1", False, "castling"),
    ("4k3/3n4/8/4P3/8/8/8/4K3 b - - 0 1", "d7f6", False, "black: knight walks into a pawn attack"),
    ("4k3/8/8/4P3/8/5n2/8/4K3 b - - 0 1", "f3e5", True, "black capture"),
])
def test_is_forcing(fen, uci, forcing, why):
    board = chess.Board(fen)
    assert is_forcing(board, mv(uci)) is forcing, why
    assert is_quiet(board, mv(uci)) is (not forcing)


# --- SEE ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("fen,uci,expected", [
    ("4k3/8/8/3p4/8/8/8/3RK3 w - - 0 1", "d1d5", 1),            # free pawn
    ("4k3/8/4p3/3p4/8/8/8/3RK3 w - - 0 1", "d1d5", -4),         # rook for a defended pawn
    ("3rk3/8/8/3n4/8/8/8/3RK3 w - - 0 1", "d1d5", -2),          # rook takes knight, rook is lost
    ("3rk3/8/8/3r4/8/8/3R4/3RK3 w - - 0 1", "d2d5", 5),         # x-ray battery wins the exchange race
    ("4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1", "e4d5", 9),
])
def test_see(fen, uci, expected):
    assert see(chess.Board(fen), mv(uci)) == expected


# --- refutation ply -----------------------------------------------------------------------------
FORK_FEN = "4k3/8/8/8/3n4/8/7P/R3K3 w - - 0 1"
FORK_PV = ["d4c2", "e1d2", "c2a1", "d2c3", "e8e7"]          # Nc2+ forks; the rook falls on ply 3


def test_refutation_material_at_ply_3():
    assert find_refutation(FORK_FEN, "h2h3", FORK_PV) == (3, "material")


def test_refutation_immediate_hang_is_ply_1():
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
    pv = ["f3h4", "d7d6", "b1c3", "c8e6", "c3b5", "e8c8"]
    assert find_refutation(fen, "d8h4", pv) == (1, "material")


def test_recapture_restoring_balance_does_not_count():
    # Queen trade: after ...Qxd1+ I am briefly 9 down, but Kxd1 restores the balance.
    fen = "3qk3/8/8/8/8/8/8/3QK3 w - - 0 1"
    assert find_refutation(fen, "e1e2", ["d8d1", "e2d1", "e8e7"]) == (None, "positional")
    # Bxc6 dxc6: piece trade started by my own capture.
    fen = "r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"
    assert find_refutation(fen, "b5c6", ["d7c6", "e1g1", "f7f6", "d2d4"]) == (None, "positional")


def test_pv_ending_mid_exchange_is_ignored():
    # The PV stops right after ...Qxd1+ (I still have Kxd1): not a quiet point.
    fen = "3qk3/8/8/8/8/8/8/3QK3 w - - 0 1"
    assert find_refutation(fen, "e1e2", ["d8d1"]) == (None, "positional")
    # Mirror case: the PV stops right after my Rxd5 grabs a defended pawn (...exd5 is coming).
    fen = "4k3/8/4p3/3p4/8/8/7P/3RK3 w - - 0 1"
    assert find_refutation(fen, "h2h3", ["e8e7", "d1d5"]) == (None, "positional")


def test_material_must_stay_down_until_last_quiet_point():
    fen = "4k3/8/8/8/3n4/8/7P/B3K3 w - - 0 1"
    pv = ["d4c2", "e1d2", "c2a1", "d2c1", "e8e7", "c1b1", "e7e6", "b1a1", "e6e5"]
    assert find_refutation(fen, "h2h3", pv) == (None, "positional")     # piece is won back
    # ...but if the PV stops while I am still down at a quiet point, it counts from ply 3.
    assert find_refutation(fen, "h2h3", pv[:5]) == (3, "material")


def test_refutation_mate_delivered_in_pv():
    fen = "rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w KQkq - 0 2"
    assert find_refutation(fen, "g2g4", ["d8h4"]) == (1, "mate")


def test_refutation_forced_mate_beyond_pv():
    # Engine says "mated in 3" but the stored PV is short: mate lands on ply 2*3-1.
    assert find_refutation(chess.STARTING_FEN, "g1f3", ["g8f6", "b1c3"], mate_in_against_me=3) == (5, "mate")


def test_mate_with_earlier_material_loss_uses_earlier_ply():
    assert find_refutation(FORK_FEN, "h2h3", FORK_PV, mate_in_against_me=9) == (3, "mate")


def test_my_own_mate_is_not_a_refutation():
    fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"
    assert find_refutation(fen, "d8h4", []) == (None, "positional")


def test_refutation_positional():
    pv = ["e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4"]
    assert find_refutation(chess.STARTING_FEN, "e2e4", pv) == (None, "positional")


# --- detectors -------------------------------------------------------------------------------------
def line(uci, win, pv=None, mate=None):
    return PVLine(uci=uci, cp=None if mate is not None else 0, mate=mate, win=win, pv=pv or [uci])


def analysis(fen, my_move, deep, my_deep_win, shallow_best=50.0, my_shallow_win=50.0, pv_after=None, mate=None):
    my_deep = next((ln for ln in deep if ln.uci == my_move), None) or line(my_move, my_deep_win, mate=mate)
    return MoveAnalysis(game_id="g", ply=10, fen_before=fen, my_move=my_move, depth=16, shallow_depth=6,
                        deep_lines=deep, shallow_lines=[line(deep[0].uci, shallow_best)], my_deep=my_deep,
                        my_shallow=line(my_move, my_shallow_win), pv_after=pv_after or [], pv_extended=False)


FB_FEN = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"   # exd5 forcing, e5/Nc3 quiet


def test_forcing_bias_detected():
    a = analysis(FB_FEN, "e4d5", [line("e4e5", 60), line("b1c3", 58), line("e4d5", 45)], 45)
    assert leaks.is_forcing_bias(a)


def test_forcing_bias_when_no_forcing_move_in_top3():
    a = analysis(FB_FEN, "e4d5", [line("e4e5", 60), line("b1c3", 58), line("d2d3", 57)], 45)
    assert leaks.is_forcing_bias(a)


@pytest.mark.parametrize("my_move,deep,my_win,why", [
    ("e4d5", [line("e4e5", 60), line("f1b5", 55), line("b1c3", 54)], 45, "a forcing move is nearly as good"),
    ("e4d5", [line("e4e5", 60), line("b1c3", 58), line("d2d3", 57)], 51, "drop below blunder threshold"),
    ("e4d5", [line("f1b5", 60), line("b1c3", 58), line("d2d3", 57)], 45, "best move is itself forcing"),
    ("d2d3", [line("e4e5", 60), line("b1c3", 58), line("g1f3", 57)], 45, "my move was quiet"),
])
def test_forcing_bias_not_detected(my_move, deep, my_win, why):
    assert not leaks.is_forcing_bias(analysis(FB_FEN, my_move, deep, my_win)), why


def test_horizon_material_leak():
    a = analysis(FORK_FEN, "h2h3", [line("a1a2", 50)], 20, shallow_best=51, my_shallow_win=49, pv_after=FORK_PV)
    assert leaks.horizon_leak(a) == (3, "material")
    found = leaks.detect(a, clock=30.0, base_seconds=600, increment_seconds=0)
    assert [(f.leak_type, f.sub_type, f.refutation_ply, f.time_trouble, f.phase) for f in found] == \
        [("horizon", "material", 3, "yes", "endgame")]


def test_horizon_requires_move_to_look_fine_at_shallow_depth():
    a = analysis(FORK_FEN, "h2h3", [line("a1a2", 50)], 20, shallow_best=51, my_shallow_win=47.9, pv_after=FORK_PV)
    assert leaks.horizon_leak(a) is None


def test_horizon_rejects_refutation_before_min_ply():
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
    a = analysis(fen, "d8h4", [line("f8c5", 48)], 6, shallow_best=48, my_shallow_win=47,
                 pv_after=["f3h4", "d7d6", "b1c3", "c8e6"])
    assert leaks.horizon_leak(a) is None


def test_horizon_positional_and_mate():
    pv = ["e7e5", "g1f3", "b8c6", "f1b5"]
    a = analysis(chess.STARTING_FEN, "e2e4", [line("d2d4", 60)], 45, 60, 59, pv_after=pv)
    assert leaks.horizon_leak(a) == (None, "positional")
    m = analysis(chess.STARTING_FEN, "e2e4", [line("d2d4", 60)], 0.0, 60, 59, pv_after=pv, mate=-4)
    assert leaks.horizon_leak(m) == (7, "mate")


def test_no_leak_below_blunder_drop():
    a = analysis(FORK_FEN, "h2h3", [line("a1a2", 50)], 41, pv_after=FORK_PV)
    assert leaks.detect(a) == []


# --- time trouble / phase ------------------------------------------------------------------------------
def test_time_trouble():
    assert time_trouble(59.9, 600, 0) == "yes"
    assert time_trouble(60.0, 600, 0) == "no"
    assert time_trouble(79.0, 600, 5) == "yes"       # budget 600 + 40*5 = 800
    assert time_trouble(81.0, 600, 5) == "no"
    assert time_trouble(None, 600, 0) == "unknown"


def test_decision_clock_uses_previous_move_plus_increment():
    clocks = {0: 598.0, 2: 500.0, 4: 30.0, 6: None}
    assert leaks.decision_clock(clocks, 0, 600, 5) == 600.0
    assert leaks.decision_clock(clocks, 4, 600, 5) == 505.0
    assert leaks.decision_clock(clocks, 6, 600, 5) == 35.0
    assert leaks.decision_clock({}, 4, 600, 5) is None


@pytest.mark.parametrize("fen,expected", [
    (chess.STARTING_FEN, "opening"),
    ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 13", "middlegame"),      # move 13
    ("r1bqkbnr/pppppppp/8/8/8/8/PPPPPPPP/R1BQKBNR w KQkq - 0 8", "opening"),         # material 56
    ("r2qkbnr/pppppppp/8/8/8/8/PPPPPPPP/R1BQKBNR w KQkq - 0 8", "middlegame"),       # material 53
    ("1r2k2r/pppb1ppp/8/8/8/8/PPPB1PPP/1R2K2R w - - 0 30", "endgame"),               # material 26
    ("1r2k2r/pppb1ppp/8/8/8/8/PPPBNPPP/1R2K2R w - - 0 30", "middlegame"),            # material 29
    ("4k3/8/8/8/8/8/8/4K2R w - - 0 60", "endgame"),
])
def test_game_phase(fen, expected):
    assert game_phase(chess.Board(fen)) == expected
