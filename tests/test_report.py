from chesscoach import db, importer, leaks, report
from chesscoach.models import GameAnalysisResult, MoveAnalysis, PVLine
from tests.test_importer import GOOD_PGN


def _line(uci, win):
    return PVLine(uci=uci, cp=0, mate=None, win=win, pv=[uci])


def test_pipeline_from_analysis_to_report_frames():
    conn = db.connect(":memory:")
    importer.import_pgn_text(conn, GOOD_PGN, {"who_carezz"})
    gid = "chesscom:live:123456"
    # White's 3rd move (ply 4), pretend Bb5 was a forcing-bias blunder: quiet d3 was best.
    fen = conn.execute("SELECT fen_before FROM moves WHERE game_id=? AND ply=4", (gid,)).fetchone()[0]
    a = MoveAnalysis(game_id=gid, ply=4, fen_before=fen, my_move="f1b5", depth=16, shallow_depth=6,
                     deep_lines=[_line("d2d3", 60), _line("b1a3", 58), _line("h2h3", 57)],
                     shallow_lines=[_line("d2d3", 55)], my_deep=_line("f1b5", 40),
                     my_shallow=_line("f1b5", 30), pv_after=["a7a6", "b5a4"], pv_extended=False)
    db.save_analysis_batch(conn, [GameAnalysisResult(gid, [a], book_plies=4)])
    assert leaks.redetect(conn, [gid]) == 1

    lk, moves = report.load_leaks(conn), report.load_moves(conn)
    assert list(lk["label"]) == ["Forcing bias"]
    assert lk.iloc[0]["time_trouble"] == "no" and moves.iloc[0]["time_trouble"] == "no"
    assert moves.iloc[0]["phase"] == lk.iloc[0]["phase"] == "opening"
    assert lk.iloc[0]["opening_family"] == "Ruy Lopez"

    table = report.breakdown(lk, moves, "phase")
    assert table.loc["opening", "Leaks per 100 moves"] == 100.0
    assert report.top_patterns(lk).iloc[0]["count"] == 1
    assert report.apply_filters(lk, colours=["black"]).empty
    assert len(report.apply_filters(lk, tcs=["rapid"], results=["win"], date_from="2024-01-01")) == 1

    # calibration: precision counts only labels whose leak is still flagged
    db.save_label(conn, gid, 4, "forcing_bias", "valid")
    prec = report.precision_table(conn).set_index("Leak type")
    assert prec.loc["Forcing bias", "Precision"] == 100.0

    # one SRS card for the position, and the game is no longer pending
    assert conn.execute("SELECT COUNT(*) FROM srs_cards").fetchone()[0] == 1
    assert db.pending_games(conn, ["rapid"]) == []
