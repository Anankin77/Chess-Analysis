from datetime import date, timedelta

import pytest

from chesscoach import db, trainer
from chesscoach.models import LeakFinding, PVLine
from chesscoach.trainer import CardState, accepted_moves, is_correct, sm2

T = date(2025, 1, 1)


def test_sm2_good_sequence_intervals():
    s = sm2(CardState(), 4, T)
    assert (s.repetition, s.interval, s.ease_factor, s.due_date) == (1, 1, 2.5, T + timedelta(days=1))
    s = sm2(s, 4, T)
    assert (s.repetition, s.interval) == (2, 6)
    s = sm2(s, 4, T)
    assert (s.repetition, s.interval, s.ease_factor) == (3, 15, 2.5)       # 6 * 2.5
    s = sm2(s, 4, T)
    assert s.interval == 38                                                # round(15 * 2.5)


def test_sm2_ease_changes():
    assert sm2(CardState(), 5, T).ease_factor == pytest.approx(2.6)
    assert sm2(CardState(), 4, T).ease_factor == pytest.approx(2.5)
    assert sm2(CardState(), 3, T).ease_factor == pytest.approx(2.36)
    assert sm2(CardState(), 1, T).ease_factor == pytest.approx(1.96)


def test_sm2_again_resets_and_floors_ease():
    s = CardState(repetition=5, interval=40, ease_factor=2.5)
    s = sm2(s, 1, T)
    assert (s.repetition, s.interval, s.due_date) == (0, 1, T + timedelta(days=1))
    for _ in range(10):
        s = sm2(s, 1, T)
    assert s.ease_factor == 1.3
    s = sm2(sm2(sm2(s, 4, T), 4, T), 4, T)
    assert s.interval == 8                                                 # round(6 * 1.3)


def test_sm2_easy_grows_faster_than_hard():
    easy = hard = CardState()
    for _ in range(4):
        easy, hard = sm2(easy, 5, T), sm2(hard, 3, T)
    assert easy.interval > hard.interval


def test_sm2_rejects_unknown_grade():
    with pytest.raises(ValueError):
        sm2(CardState(), 2, T)


def lines(*pairs):
    return [PVLine(uci=u, cp=0, mate=None, win=w, pv=[u]) for u, w in pairs]


def test_answer_accepts_any_multipv_move_within_tolerance():
    mpv = lines(("e2e4", 60.0), ("d2d4", 57.5), ("c2c4", 56.9))
    assert [ln.uci for ln in accepted_moves(mpv)] == ["e2e4", "d2d4"]
    assert is_correct("e2e4", mpv) and is_correct("d2d4", mpv)       # not only the single best move
    assert not is_correct("c2c4", mpv)                                # 3.1 worse
    assert not is_correct("a2a3", mpv)                                # not in the stored MultiPV
    assert is_correct("c2c4", mpv, tolerance=5.0)


def test_review_updates_card_and_queue():
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO games (id, source, my_color, pgn, imported_at) VALUES ('g','pgn','white','','x')")
    fen = "4k3/8/8/8/3n4/8/7P/R3K3 w - - 0 1"
    finding = dict(game_id="g", sub_type=None, refutation_ply=None, win_drop=20.0, phase="endgame",
                   time_trouble="no", fen_before=fen, my_move="h2h3", best_move="a1a2")
    db.replace_leaks(conn, None, [LeakFinding(ply=10, leak_type="forcing_bias", **finding),
                                  LeakFinding(ply=10, leak_type="horizon", **{**finding, "sub_type": "material"}),
                                  LeakFinding(ply=20, leak_type="horizon", **{**finding, "sub_type": "mate"})],
                     T.isoformat())
    assert len(trainer.card_queue(conn, today=T)) == 2                     # one card per position
    assert [c["ply"] for c in trainer.card_queue(conn, "forcing_bias", today=T)] == [10]

    card = trainer.card_queue(conn, "forcing_bias", today=T)[0]
    state = trainer.review(conn, card["id"], 4, T)
    assert state.due_date == T + timedelta(days=1)
    assert [c["ply"] for c in trainer.card_queue(conn, today=T)] == [20]   # reviewed card is no longer due
    assert len(trainer.card_queue(conn, today=T + timedelta(days=1))) == 2
    assert len(trainer.card_queue(conn, include_not_due=True, today=T)) == 2

    # Re-detection keeps SM-2 progress.
    db.replace_leaks(conn, None, [LeakFinding(ply=10, leak_type="forcing_bias", **finding)], T.isoformat())
    row = conn.execute("SELECT repetition, reviews FROM srs_cards WHERE ply=10").fetchone()
    assert (row["repetition"], row["reviews"]) == (1, 1)


def test_review_log_and_training_stats():
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO games (id, source, my_color, pgn, imported_at) VALUES ('g','pgn','white','','x')")
    fen = "4k3/8/8/8/3n4/8/7P/R3K3 w - - 0 1"
    finding = dict(game_id="g", sub_type=None, refutation_ply=None, win_drop=20.0, phase="endgame",
                   time_trouble="no", fen_before=fen, my_move="h2h3", best_move="a1a2")
    db.replace_leaks(conn, None, [LeakFinding(ply=10, leak_type="forcing_bias", **finding),
                                  LeakFinding(ply=20, leak_type="horizon", **{**finding, "sub_type": "mate"})],
                     T.isoformat())
    a, b = trainer.card_queue(conn, today=T)
    trainer.review(conn, a["id"], 4, T - timedelta(days=1), correct=True)
    trainer.review(conn, a["id"], 4, T, correct=True)
    trainer.review(conn, b["id"], 1, T, correct=False)
    stats = trainer.training_stats(conn, T)
    assert (stats["today"], stats["today_correct"], stats["streak"]) == (2, 1, 2)
    assert stats["by_type"] == {"forcing_bias": (2, 2), "horizon": (0, 1)}
    assert trainer.training_stats(conn, T + timedelta(days=3))["streak"] == 0


def test_walk_builds_replayable_line():
    import chess
    out = trainer._walk(chess.Board(), ["e2e4", "e7e5", "g1f3", "zzzz"])
    assert out["sans"] == ["1.e4", "e5", "2.Nf3"] and len(out["fens"]) == 3
    assert trainer._walk(chess.Board("4k3/8/8/8/8/8/8/4K3 b - - 0 9"), ["e8e7"])["sans"] == ["9…Ke7"]
