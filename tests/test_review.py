import chess

from chesscoach import db, importer, leaks, review
from chesscoach.models import GameAnalysisResult, MoveAnalysis, PVLine
from tests.test_importer import GOOD_PGN


def test_grade_boundaries():
    assert [review.grade(d)[0] for d in (0, 0.9, 1, 4.9, 5, 9.9, 10, 19.9, 20, 60)] == \
        ["Best", "Best", "Good", "Good", "Inaccuracy", "Inaccuracy", "Mistake", "Mistake", "Blunder", "Blunder"]


def test_plan_text_routes_captures_and_castling():
    # Knight tour is merged into one route; opponent moves are skipped.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    tour = "4k3/8/8/8/8/8/8/1N2K3 w - - 0 1"
    assert review.plan_text(tour, ["b1d2", "e8e7", "d2f1", "e7e6", "f1e3"]) == "knight b1→d2→f1→e3"
    assert review.plan_text(fen, ["f1b5", "a7a6", "b5c6", "d7c6", "e1g1"]) == \
        "bishop f1→b5, then bishop takes the knight on c6, then castle kingside"
    assert review.plan_text(fen, ["d2d4", "e5d4", "f3d4"]) == "d-pawn to d4, then knight takes the pawn on d4"
    assert review.plan_text(fen, ["zzzz"]) == ""
    black = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
    assert review.plan_text(black, ["b8c6", "f1b5", "a7a6"]) == "knight b8→c6, then a-pawn to a6"


def test_move_kind():
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    kinds = [review.move_kind(board, chess.Move.from_uci(u)) for u in ("e4d5", "f1b5", "e4e5")]
    assert kinds == ["capture", "check", "quiet move"]


def _line(uci, win, pv=None):
    return PVLine(uci=uci, cp=0, mate=None, win=win, pv=pv or [uci])


def test_game_review_and_lessons():
    conn = db.connect(":memory:")
    importer.import_pgn_text(conn, GOOD_PGN, {"who_carezz"})
    gid = "chesscom:live:123456"
    assert review.game_review(conn, gid) is None                     # not analyzed yet
    fens = dict(conn.execute("SELECT ply, fen_before FROM moves WHERE game_id=?", (gid,)).fetchall())
    good = MoveAnalysis(gid, 2, fens[2], "g1f3", 16, 6, [_line("g1f3", 55), _line("b1c3", 40)],
                        [_line("g1f3", 55)], _line("g1f3", 55), _line("g1f3", 55), ["b8c6"], False)
    bad = MoveAnalysis(gid, 4, fens[4], "f1b5", 16, 6,
                       [_line("d2d3", 60, ["d2d3", "g8f6", "b1d2"]), _line("h2h3", 58), _line("a2a3", 57)],
                       [_line("d2d3", 55)], _line("f1b5", 38), _line("f1b5", 30), ["a7a6", "b5a4"], False)
    db.save_analysis_batch(conn, [GameAnalysisResult(gid, [good, bad], book_plies=2)])
    leaks.redetect(conn, [gid])

    r = review.game_review(conn, gid)
    first, second = r["moves"]
    assert (first["grade"], first["label"]) == ("Best", "2.Nf3")
    assert "The only move that holds the evaluation" in first["comment"]
    assert (second["grade"], second["label"], second["leaks"]) == ("Blunder", "3.Bb5??", ["forcing_bias"])
    assert "Best was the quiet move **d3**" in second["comment"]
    assert "You chose a threat where the position asked for a quiet move" in second["comment"]
    assert "Plan after d3: d-pawn to d3, then knight b1→d2." in second["comment"]
    s = r["summary"]
    assert (s["quiet_needed"], s["quiet_found"], s["forced_instead"], s["counts"]["Blunder"]) == (1, 0, 1, 1)
    notes = review.lessons(r)
    assert notes[0].startswith("**Turning point: 3.Bb5??**")
    assert any(n.startswith("**Quiet moves:**") for n in notes)


def test_walkthrough_covers_every_move_with_explanations():
    conn = db.connect(":memory:")
    importer.import_pgn_text(conn, GOOD_PGN, {"who_carezz"})
    gid = "chesscom:live:123456"
    assert review.walkthrough(conn, gid) is None
    fens = dict(conn.execute("SELECT ply, fen_before FROM moves WHERE game_id=?", (gid,)).fetchall())
    bad = MoveAnalysis(gid, 4, fens[4], "f1b5", 16, 6,
                       [_line("d2d3", 60, ["d2d3", "g8f6", "b1d2", "f8c5", "d2f1"]), _line("h2h3", 58), _line("a2a3", 57)],
                       [_line("d2d3", 55)], _line("f1b5", 38, ["f1b5", "a7a6", "b5a4"]), _line("f1b5", 30),
                       ["a7a6", "b5a4"], False)
    db.save_analysis_batch(conn, [GameAnalysisResult(gid, [bad], book_plies=4)])
    leaks.redetect(conn, [gid])

    w = review.walkthrough(conn, gid)
    assert [p["label"] for p in w["plies"]] == ["1.e4", "1…e5", "2.Nf3", "2…Nc6", "3.Bb5??"]
    assert [p["who"] for p in w["plies"]] == ["me", "opp", "me", "opp", "me"]
    assert w["orientation"] == "white" and w["start"] == 3
    assert w["plies"][0]["grade"] == "Book" and "Opening theory." in w["plies"][0]["text"]
    assert any(t.startswith("End of theory:") for t in w["plies"][3]["text"])

    last = w["plies"][-1]
    text = " ".join(last["text"])
    assert last["grade"] == "Blunder" and last["key"] and last["eval"] == 38.0
    assert "**3.Bb5??** attacks the knight on c6 and develops the bishop." in text
    assert "gives away 22 win % (your chances 60% → 38%)" in text
    assert "**Better: d3**, a quiet move." in text
    assert "**The plan behind it:** **Nbd2** develops the knight" in text
    assert "**Why yours falls short:** the reply is **a6**, which attacks the bishop on b5." in text
    assert "forcing bias" in text
    assert last["best"]["sans"][:2] == ["3.d3", "Nf6"] and len(last["best"]["fens"]) == 5
    assert last["cont"]["sans"] == ["3.Bb5", "a6", "4.Ba4"]
    assert w["plies"][1]["best"] is None and w["plies"][1]["cont"] is None
