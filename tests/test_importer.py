import pytest

from chesscoach import db, importer
from chesscoach.importer import classify_time_control, parse_clock, parse_time_control

GOOD_PGN = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2024.03.01"]
[White "who_carezz"]
[Black "Opp"]
[Result "1-0"]
[TimeControl "600+5"]
[Link "https://www.chess.com/game/live/123456"]

1. e4 {[%clk 0:10:03.2]} 1... e5 {[%clk 0:09:58]} 2. Nf3 {[%clk 0:09:50.5]} 2... Nc6 3. Bb5 {[%clk 0:09:40]} 1-0
"""

CORRUPT_PGN = """[Event "Broken"]
[White "who_carezz"]
[Black "Opp"]
[Result "1-0"]
[TimeControl "600"]

1. e4 e5 2. Ke5 Nf6 1-0
"""

BLACK_DAILY_PGN = """[Event "Daily"]
[White "Opp"]
[Black "WHO_CAREZZ"]
[Result "1-0"]
[TimeControl "1/86400"]
[Link "https://www.chess.com/game/daily/777"]

1. d4 d5 1-0
"""

FOREIGN_PGN = """[White "A"]
[Black "B"]
[Result "1/2-1/2"]

1. e4 e5 1/2-1/2
"""


# --- [%clk] ---------------------------------------------------------------------
@pytest.mark.parametrize("comment,expected", [
    ("[%clk 0:09:58.3]", 598.3),          # decimal seconds
    ("[%clk 0:00:07.9]", 7.9),
    ("[%clk 0:10:00]", 600.0),            # integer seconds
    ("[%clk 1:30:05]", 5405.0),           # hours
    ("[%eval 0.3] [%clk 0:03:00] [%emt 0:00:02]", 180.0),
    ("{ [%clk 0:00:59.95] }", 59.95),
])
def test_parse_clock(comment, expected):
    assert parse_clock(comment) == pytest.approx(expected)


@pytest.mark.parametrize("comment", [None, "", "good move", "[%eval 1.2]", "[%clk bogus]"])
def test_parse_clock_missing(comment):
    assert parse_clock(comment) is None


# --- TimeControl -------------------------------------------------------------------
@pytest.mark.parametrize("tc,expected", [
    ("60", "bullet"), ("60+1", "bullet"), ("120+1", "bullet"), ("179", "bullet"),
    ("180", "blitz"), ("180+2", "blitz"), ("300", "blitz"), ("300+5", "blitz"),
    ("600", "rapid"), ("600+5", "rapid"), ("900+10", "rapid"), ("1800", "rapid"),
    ("300+10", "rapid"),                 # 5+10: estimated 11:40
    ("1/86400", "daily"), ("1/259200", "daily"), ("-", "daily"),
    ("", "unknown"), ("?", "unknown"), (None, "unknown"), ("garbage", "unknown"),
])
def test_classify_time_control(tc, expected):
    assert classify_time_control(tc) == expected


def test_parse_time_control_values():
    assert parse_time_control("600+5") == (600, 5, False)
    assert parse_time_control("180") == (180, 0, False)
    assert parse_time_control("1/86400") == (86400, 0, True)
    assert parse_time_control("-") == (None, None, True)


# --- import -------------------------------------------------------------------------
def test_import_skips_corrupt_and_is_incremental():
    conn = db.connect(":memory:")
    text = "\n\n".join([GOOD_PGN, CORRUPT_PGN, BLACK_DAILY_PGN, FOREIGN_PGN])
    stats = importer.import_pgn_text(conn, text, {"who_carezz"})
    assert (stats.imported, stats.skipped) == (2, 2)   # corrupt + foreign skipped, rest continues

    again = importer.import_pgn_text(conn, text, {"who_carezz"})
    assert (again.imported, again.existing) == (0, 2)

    g = conn.execute("SELECT * FROM games WHERE id='chesscom:live:123456'").fetchone()
    assert (g["my_color"], g["result"], g["tc_class"]) == ("white", "win", "rapid")
    assert (g["base_seconds"], g["increment_seconds"]) == (600, 5)
    assert g["opening"].startswith("Ruy Lopez")

    d = conn.execute("SELECT * FROM games WHERE id='chesscom:daily:777'").fetchone()
    assert (d["my_color"], d["result"], d["tc_class"]) == ("black", "loss", "daily")

    clocks = [r[0] for r in conn.execute(
        "SELECT clock_seconds FROM moves WHERE game_id='chesscom:live:123456' ORDER BY ply")]
    assert clocks == [pytest.approx(603.2), 598.0, pytest.approx(590.5), None, 580.0]


def test_daily_games_are_never_pending():
    conn = db.connect(":memory:")
    importer.import_pgn_text(conn, GOOD_PGN + "\n\n" + BLACK_DAILY_PGN, {"who_carezz"})
    ids = [r["id"] for r in db.pending_games(conn, ["rapid", "blitz", "daily"])]
    assert ids == ["chesscom:live:123456"]


class FakeResp:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._payload, self.encoding = status, text, payload, "utf-8"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_lichess_backoff_on_429():
    class Session:
        def __init__(self):
            self.responses = [FakeResp(429), FakeResp(429), FakeResp(200, "ok")]

        def get(self, *a, **k):
            return self.responses.pop(0)

    sleeps = []
    resp = importer.lichess_get(Session(), "https://lichess.org/api/x", {}, sleep=sleeps.append)
    assert resp.text == "ok"
    assert sleeps == [60, 120]            # exponential


def test_chesscom_import_sends_user_agent_and_skips_variants():
    conn = db.connect(":memory:")

    class Session:
        def __init__(self):
            self.headers = {"User-Agent": importer.config.USER_AGENT}
            self.urls = []

        def get(self, url, **k):
            self.urls.append(url)
            if url.endswith("/archives"):
                return FakeResp(200, payload={"archives": ["https://api.chess.com/pub/player/who_carezz/games/2024/03"]})
            return FakeResp(200, payload={"games": [
                {"pgn": GOOD_PGN, "rules": "chess"},
                {"pgn": GOOD_PGN.replace("123456", "999"), "rules": "chess960"},
            ]})

    s = Session()
    stats = importer.import_chesscom(conn, "who_carezz", session=s)
    assert stats.imported == 1
    assert s.headers["User-Agent"].startswith("chess-coach/1.0")
    assert importer._session().headers["User-Agent"].startswith("chess-coach/1.0")


def _chesscom_session(archives):
    """archives: {"2024/03": [game dict, ...]}"""
    class Session:
        headers = {}
        fetched = []

        def get(self, url, **k):
            if url.endswith("/archives"):
                return FakeResp(200, payload={"archives": [f"https://api.chess.com/pub/player/x/games/{m}" for m in archives]})
            month = "/".join(url.split("/")[-2:])
            self.fetched.append(month)
            return FakeResp(200, payload={"games": archives[month]})
    return Session()


def _game(game_id, tc, end_time, date):
    pgn = (GOOD_PGN.replace("123456", str(game_id)).replace('"600+5"', f'"{tc}"')
           .replace("2024.03.01", date))
    return {"pgn": pgn, "rules": "chess", "url": f"https://www.chess.com/game/live/{game_id}",
            "time_control": tc, "end_time": end_time}


def test_chesscom_import_filters_by_date_and_time_format():
    import calendar
    ts = lambda y, m, d: calendar.timegm((y, m, d, 12, 0, 0))
    archives = {
        "2023/12": [_game(1, "600", ts(2023, 12, 20), "2023.12.20")],
        "2024/03": [_game(2, "600", ts(2024, 3, 5), "2024.03.05"),      # rapid, in range
                    _game(3, "60", ts(2024, 3, 6), "2024.03.06"),       # bullet: wrong format
                    _game(4, "180+2", ts(2024, 3, 25), "2024.03.25")],  # blitz, after date_to
        "2024/05": [_game(5, "600", ts(2024, 5, 1), "2024.05.01")],
    }
    conn = db.connect(":memory:")
    s = _chesscom_session(archives)
    stats = importer.import_chesscom(conn, "who_carezz", "2024-03-01", "2024-03-20", ["rapid", "blitz"], session=s)
    assert s.fetched == ["2024/03"]                       # other months are not even downloaded
    assert (stats.imported, stats.filtered) == (1, 2)
    assert [r[0] for r in conn.execute("SELECT id FROM games")] == ["chesscom:live:2"]

    # Widening the selection later only adds the missing games.
    s = _chesscom_session(archives)
    stats = importer.import_chesscom(conn, "who_carezz", session=s)
    assert (stats.imported, stats.existing) == (4, 1)


def test_aborted_games_are_counted_not_warned(caplog):
    conn = db.connect(":memory:")
    empty = GOOD_PGN.split("\n\n")[0] + "\n\n1-0\n"
    stats = importer.import_pgn_text(conn, empty, {"who_carezz"})
    assert (stats.imported, stats.aborted, stats.skipped) == (0, 1, 0)
    assert "Skipping" not in caplog.text


def test_delete_games_outside_selection():
    conn = db.connect(":memory:")
    importer.import_pgn_text(conn, GOOD_PGN + "\n\n" + BLACK_DAILY_PGN, {"who_carezz"})
    assert db.delete_games_outside(conn, "2024-01-01", "2024-12-31", ["rapid"], dry_run=True) == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    assert db.delete_games_outside(conn, "2024-01-01", "2024-12-31", ["rapid"]) == 1
    assert [r[0] for r in conn.execute("SELECT id FROM games")] == ["chesscom:live:123456"]
    assert conn.execute("SELECT COUNT(*) FROM moves WHERE game_id LIKE '%daily%'").fetchone()[0] == 0
    assert db.pending_games(conn, ["rapid"], date_from="2024-03-02") == []
    assert len(db.pending_games(conn, ["rapid"], date_from="2024-03-01", date_to="2024-03-01")) == 1
