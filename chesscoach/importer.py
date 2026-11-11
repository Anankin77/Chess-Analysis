"""Game import: Chess.com monthly archives, Lichess API, manual PGN.

CLI:  python -m chesscoach.importer --chesscom --lichess --pgn games.pgn
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import re
import sqlite3
import time
from typing import Callable, Iterable, Iterator, Optional

import chess
import chess.pgn
import requests

from . import config, db
from .book import default_book
from .models import GameRecord, MoveRecord

log = logging.getLogger(__name__)

CLK_RE = re.compile(r"\[%clk\s+(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)\]")
CHESSCOM_URL_RE = re.compile(r"chess\.com/(?:analysis/)?game/(live|daily)/(\d+)")
LICHESS_URL_RE = re.compile(r"lichess\.org/([A-Za-z0-9]{8})")


# --- pure parsing helpers -------------------------------------------------------

def parse_clock(comment: Optional[str]) -> Optional[float]:
    """Seconds from a `[%clk h:mm:ss(.s)]` comment; None when missing."""
    if not comment:
        return None
    m = CLK_RE.search(comment)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def parse_time_control(tc: Optional[str]) -> tuple[Optional[int], Optional[int], bool]:
    """(base_seconds, increment_seconds, is_daily) from a PGN TimeControl header.

    Handles "600", "600+5", "1/86400" (Chess.com daily: days per move) and
    "-" (Lichess correspondence / unlimited)."""
    tc = (tc or "").strip()
    if not tc or tc == "?":
        return None, None, False
    if tc == "-":
        return None, None, True
    if "/" in tc:  # moves/seconds -> daily
        try:
            return int(tc.split("/")[1].split("+")[0]), 0, True
        except ValueError:
            return None, None, True
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\+(\d+(?:\.\d+)?))?", tc)
    if not m:
        return None, None, False
    return int(float(m.group(1))), int(float(m.group(2) or 0)), False


def classify_time_control(tc: Optional[str]) -> str:
    """bullet (base < 3 min) | blitz | rapid | daily | unknown."""
    base, inc, daily = parse_time_control(tc)
    if daily:
        return "daily"
    if base is None:
        return "unknown"
    if base >= 86400:
        return "daily"
    if base < config.BULLET_MAX_BASE_SECONDS:
        return "bullet"
    if base + 40 * (inc or 0) < config.BLITZ_MAX_ESTIMATED_SECONDS:
        return "blitz"
    return "rapid"


def my_usernames(extra: Iterable[str] = ()) -> set[str]:
    names = {config.CHESSCOM_USERNAME, config.LICHESS_USERNAME, *extra}
    return {n.strip().lower() for n in names if n and n.strip()}


def determine_color(white: str, black: str, usernames: set[str]) -> Optional[str]:
    w, b = white.strip().lower() in usernames, black.strip().lower() in usernames
    if w == b:  # neither (or, absurdly, both)
        return None
    return "white" if w else "black"


def game_id_for(headers: chess.pgn.Headers, moves_uci: list[str]) -> tuple[str, str, str]:
    """(id, source, url). The same game gets the same id from API and PGN upload."""
    link = " ".join(headers.get(k, "") for k in ("Link", "Site", "GameUrl"))
    m = CHESSCOM_URL_RE.search(link)
    if m:
        return f"chesscom:{m.group(1)}:{m.group(2)}", "chesscom", f"https://www.chess.com/game/{m.group(1)}/{m.group(2)}"
    m = LICHESS_URL_RE.search(link)
    if m:
        return f"lichess:{m.group(1)}", "lichess", f"https://lichess.org/{m.group(1)}"
    key = "|".join([headers.get("White", ""), headers.get("Black", ""), headers.get("Date", ""),
                    headers.get("UTCTime", headers.get("StartTime", "")), " ".join(moves_uci)])
    return "pgn:" + hashlib.sha1(key.encode()).hexdigest()[:16], "pgn", ""


def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def game_to_record(game: chess.pgn.Game, usernames: set[str]) -> GameRecord:
    """Convert a parsed PGN game. Raises ValueError for anything we skip."""
    if game.errors:
        raise ValueError(f"corrupt PGN: {game.errors[0]}")
    h = game.headers
    variant = h.get("Variant", "Standard").lower()
    if variant not in ("standard", "chess", "from position") or "FEN" in h or h.get("SetUp") == "1":
        raise ValueError(f"non-standard game (variant={h.get('Variant')!r}, custom FEN={'FEN' in h})")
    white, black = h.get("White", "?"), h.get("Black", "?")
    my_color = determine_color(white, black, usernames)
    if my_color is None:
        raise ValueError(f"none of {sorted(usernames)} is playing ({white} vs {black})")

    board = game.board()
    moves: list[MoveRecord] = []
    for node in game.mainline():
        move = node.move
        color = "white" if board.turn == chess.WHITE else "black"
        moves.append(MoveRecord(
            ply=len(moves), fullmove=board.fullmove_number, color=color,
            is_mine=(color == my_color), san=board.san(move), uci=move.uci(),
            fen_before=board.fen(), clock_seconds=parse_clock(node.comment)))
        board.push(move)
    if not moves:
        raise NoMoves("game has no moves")

    gid, source, url = game_id_for(h, [m.uci for m in moves])
    tc = h.get("TimeControl", "")
    base, inc, _ = parse_time_control(tc)
    raw = h.get("Result", "*")
    if raw == "1/2-1/2":
        result = "draw"
    elif raw in ("1-0", "0-1"):
        result = "win" if (raw == "1-0") == (my_color == "white") else "loss"
    else:
        result = "unknown"

    eco, opening, _ = default_book().classify([chess.Move.from_uci(m.uci) for m in moves])
    eco = eco or h.get("ECO", "")
    if not opening:
        opening = h.get("Opening", "")
        if not opening or opening == "?":
            tail = h.get("ECOUrl", "").rstrip("/").rsplit("/", 1)[-1]
            opening = tail.replace("-", " ") or "Unknown"

    date = (h.get("UTCDate") or h.get("Date") or "").replace(".", "-")
    if "?" in date:
        date = ""
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=True)
    return GameRecord(
        id=gid, source=source, url=url, white=white, black=black, my_color=my_color,
        result=result, date=date, time_control=tc, base_seconds=base, increment_seconds=inc,
        tc_class=classify_time_control(tc), eco=eco, opening=opening,
        white_elo=_int_or_none(h.get("WhiteElo")), black_elo=_int_or_none(h.get("BlackElo")),
        pgn=game.accept(exporter), moves=moves)


class NoMoves(ValueError):
    """Aborted game: nothing to analyze. Counted, not reported as a problem."""


def iter_pgn_games(text: str) -> Iterator[chess.pgn.Game]:
    stream = io.StringIO(text)
    while True:
        try:
            game = chess.pgn.read_game(stream)
        except Exception as exc:  # python-chess is tolerant, but never let one game stop us
            log.warning("Skipping unreadable PGN chunk: %s", exc)
            continue
        if game is None:
            return
        yield game


class ImportStats:
    def __init__(self) -> None:
        self.imported = self.existing = self.skipped = self.aborted = self.filtered = 0

    def __repr__(self) -> str:
        return (f"imported={self.imported} already_present={self.existing} "
                f"outside_selection={self.filtered} aborted_no_moves={self.aborted} skipped={self.skipped}")


def import_pgn_text(conn: sqlite3.Connection, text: str, usernames: Optional[set[str]] = None,
                    stats: Optional[ImportStats] = None, known: Optional[set[str]] = None) -> ImportStats:
    """Import every game in `text`. Corrupt or foreign games are logged and skipped."""
    usernames = usernames or my_usernames()
    stats = stats or ImportStats()
    known = db.existing_game_ids(conn) if known is None else known
    records: list[GameRecord] = []
    for game in iter_pgn_games(text):
        try:
            rec = game_to_record(game, usernames)
        except NoMoves:
            stats.aborted += 1
            continue
        except Exception as exc:
            stats.skipped += 1
            log.warning("Skipping game %s: %s", game.headers.get("Link") or game.headers.get("Site", "?"), exc)
            continue
        if rec.id in known:
            stats.existing += 1
            continue
        known.add(rec.id)
        records.append(rec)
    stats.imported += db.save_games(conn, records)
    return stats


# --- Chess.com --------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    return s


def import_chesscom(conn: sqlite3.Connection, username: Optional[str] = None,
                    date_from: Optional[str] = None, date_to: Optional[str] = None,
                    time_classes: Optional[Iterable[str]] = None,
                    session: Optional[requests.Session] = None,
                    progress: Callable[[str, float], None] = lambda msg, fraction: None) -> ImportStats:
    """Import from the public monthly archives.

    `date_from` / `date_to` are ISO dates (inclusive, UTC); `time_classes` limits
    the import to e.g. {"rapid", "blitz"}. None means no restriction. Games
    already stored are skipped before their PGN is even parsed, so re-running
    is cheap."""
    username = (username or config.CHESSCOM_USERNAME).strip()
    session = session or _session()
    stats = ImportStats()
    wanted = set(time_classes) if time_classes else None
    date_from, date_to = (str(date_from) if date_from else None), (str(date_to) if date_to else None)
    resp = session.get(f"https://api.chess.com/pub/player/{username.lower()}/games/archives",
                       timeout=config.HTTP_TIMEOUT)
    resp.raise_for_status()
    archives = []
    for url in resp.json().get("archives", []):
        month = "-".join(url.rstrip("/").split("/")[-2:])  # yyyy-mm
        if (date_from and month < date_from[:7]) or (date_to and month > date_to[:7]):
            continue
        archives.append((month, url))
    known = db.existing_game_ids(conn)
    usernames = my_usernames([username])
    for i, (month, url) in enumerate(archives):
        progress(f"Chess.com {month}", i / len(archives))
        try:
            r = session.get(url, timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            games = r.json().get("games", [])
        except (requests.RequestException, ValueError) as exc:
            log.warning("Skipping Chess.com archive %s: %s", url, exc)
            continue
        pgns = []
        for g in games:
            if not g.get("pgn") or g.get("rules", "chess") != "chess":
                continue
            m = CHESSCOM_URL_RE.search(g.get("url", ""))
            if m and f"chesscom:{m.group(1)}:{m.group(2)}" in known:
                stats.existing += 1
                continue
            if wanted is not None and classify_time_control(g.get("time_control")) not in wanted:
                stats.filtered += 1
                continue
            if g.get("end_time") and (date_from or date_to):
                day = time.strftime("%Y-%m-%d", time.gmtime(g["end_time"]))
                if (date_from and day < date_from) or (date_to and day > date_to):
                    stats.filtered += 1
                    continue
            pgns.append(g["pgn"])
        import_pgn_text(conn, "\n\n".join(pgns), usernames, stats, known)
    progress("Chess.com done", 1.0)
    return stats


# --- Lichess ----------------------------------------------------------------------

def lichess_get(session: requests.Session, url: str, params: dict,
                sleep: Callable[[float], None] = time.sleep) -> requests.Response:
    """GET with exponential backoff on HTTP 429 (60s, 120s, 240s, ...)."""
    delay = config.LICHESS_BACKOFF_BASE_SECONDS
    for attempt in range(config.LICHESS_MAX_RETRIES + 1):
        resp = session.get(url, params=params, headers={"Accept": "application/x-chess-pgn"},
                           timeout=config.HTTP_TIMEOUT)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        if attempt == config.LICHESS_MAX_RETRIES:
            break
        log.warning("Lichess rate limit (429); waiting %ss", delay)
        sleep(delay)
        delay *= 2
    raise RuntimeError("Lichess kept answering 429; try again later.")


def import_lichess(conn: sqlite3.Connection, username: Optional[str] = None, full: bool = False,
                   session: Optional[requests.Session] = None,
                   progress: Callable[[str, float], None] = lambda msg, fraction: None) -> ImportStats:
    username = (username or config.LICHESS_USERNAME).strip()
    if not username:
        raise ValueError("No Lichess username configured (config.LICHESS_USERNAME).")
    session = session or _session()
    params: dict = {"clocks": "true", "opening": "true", "evals": "false"}
    latest = conn.execute("SELECT MAX(date) FROM games WHERE source='lichess'").fetchone()[0]
    if latest and not full:  # re-fetch from the start of the newest stored day; duplicates are skipped
        params["since"] = int(time.mktime(time.strptime(latest, "%Y-%m-%d")) * 1000) - 86_400_000
    progress(f"Lichess {username}", 0.0)
    resp = lichess_get(session, f"https://lichess.org/api/games/user/{username}", params)
    resp.encoding = "utf-8"
    return import_pgn_text(conn, resp.text, my_usernames([username]))


# --- CLI ------------------------------------------------------------------------------

def setup_logging() -> None:
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
                                  logging.StreamHandler()])


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Import games into chess-coach.")
    p.add_argument("--chesscom", nargs="?", const=config.CHESSCOM_USERNAME, metavar="USER")
    p.add_argument("--lichess", nargs="?", const=config.LICHESS_USERNAME, metavar="USER")
    p.add_argument("--pgn", nargs="*", default=[], metavar="FILE")
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="Chess.com: only games from this date")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="Chess.com: only games up to this date")
    p.add_argument("--time-controls", nargs="+", choices=["bullet", "blitz", "rapid", "daily"],
                   help="Chess.com: only these time formats (default: all)")
    p.add_argument("--full", action="store_true", help="Lichess: re-download everything")
    args = p.parse_args(argv)
    if not (args.chesscom or args.lichess or args.pgn):
        p.error("nothing to do: pass --chesscom, --lichess and/or --pgn FILE")
    setup_logging()
    conn = db.connect()
    if args.chesscom:
        print("Chess.com:", import_chesscom(conn, args.chesscom, args.date_from, args.date_to, args.time_controls,
                                            progress=lambda msg, fraction: print(msg)))
    if args.lichess is not None and (args.lichess or config.LICHESS_USERNAME):
        print("Lichess:", import_lichess(conn, args.lichess, args.full))
    for path in args.pgn:
        with open(path, encoding="utf-8", errors="replace") as fh:
            print(f"{path}:", import_pgn_text(conn, fh.read(), my_usernames(
                [u for u in (args.chesscom, args.lichess) if u])))
    print(db.status_counts(conn))


if __name__ == "__main__":
    main()
