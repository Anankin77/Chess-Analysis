"""Stockfish analysis of my non-book moves.

CLI (resumable; safe to Ctrl-C and restart):
    python -m chesscoach.engine --batch --workers 4

Workers only compute: each returns a GameAnalysisResult dataclass and the
main process commits them to SQLite in batches (see db.py)."""

from __future__ import annotations

import argparse
import atexit
import io
import logging
import math
import os
import signal
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Optional

import chess
import chess.engine
import chess.pgn

from . import config
from .book import OpeningBook, default_book
from .models import GameAnalysisResult, MoveAnalysis, PVLine

log = logging.getLogger(__name__)

_MATE_SCORE = 1_000_000


# --- score conversion ---------------------------------------------------------------

def win_pct(cp: float) -> float:
    """Win % for the side whose perspective `cp` is given from (Lichess formula)."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def score_to_win_pct(score: chess.engine.PovScore, my_color: chess.Color) -> float:
    """MY win %: cp is flipped to my perspective whatever my colour.
    Mate for me -> 100.0, mate against me -> 0.0."""
    pov = score.pov(my_color)
    if pov.is_mate():
        return 100.0 if pov.score(mate_score=_MATE_SCORE) > 0 else 0.0
    return win_pct(pov.score())


def info_to_line(info: chess.engine.InfoDict, my_color: chess.Color) -> PVLine:
    pov = info["score"].pov(my_color)
    pv = [m.uci() for m in info.get("pv", [])]
    return PVLine(uci=pv[0], cp=None if pov.is_mate() else pov.score(), mate=pov.mate(),
                  win=score_to_win_pct(info["score"], my_color), pv=pv)


# --- single-position analysis ---------------------------------------------------------

@dataclass(frozen=True)
class EngineSettings:
    depth: int = config.DEFAULT_DEPTH
    shallow_depth: int = config.SHALLOW_DEPTH
    extend_depth: int = config.EXTEND_DEPTH
    multipv: int = config.MULTIPV_COUNT
    min_pv_length: int = config.MIN_PV_LENGTH
    threads: int = config.ENGINE_THREADS
    hash_mb: int = config.ENGINE_HASH_MB


def open_engine(settings: EngineSettings = EngineSettings()) -> chess.engine.SimpleEngine:
    engine = chess.engine.SimpleEngine.popen_uci(config.stockfish_path())
    engine.configure({"Threads": settings.threads, "Hash": settings.hash_mb})
    return engine


def _analyse(engine: chess.engine.SimpleEngine, board: chess.Board, depth: int, multipv: int,
             my_color: chess.Color, root_moves: Optional[list[chess.Move]] = None) -> list[PVLine]:
    # A fresh `game` token makes python-chess send `ucinewgame`, clearing the hash
    # table. Without it a "shallow" search would reuse deep results from the
    # previous search and the horizon detector would be blind.
    infos = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv,
                           root_moves=root_moves, game=object())
    return [info_to_line(i, my_color) for i in infos if i.get("pv") and "score" in i]


def analyze_move(engine: chess.engine.SimpleEngine, board: chess.Board, move: chess.Move,
                 game_id: str, ply: int, settings: EngineSettings = EngineSettings()) -> Optional[MoveAnalysis]:
    """Steps (a)-(e) of the spec for one of my moves. `board` is the position before it."""
    me = board.turn
    shallow = _analyse(engine, board, settings.shallow_depth, settings.multipv, me)          # (b)
    my_shallow = _analyse(engine, board, settings.shallow_depth, 1, me, [move])              # (d)
    deep = _analyse(engine, board, settings.depth, settings.multipv, me)                     # (a)
    if not (shallow and my_shallow and deep):
        return None
    my_deep = next((line for line in deep if line.uci == move.uci()), None)
    if my_deep is None:                                                                      # (c)
        lines = _analyse(engine, board, settings.depth, 1, me, [move])
        if not lines:
            return None
        my_deep = lines[0]

    pv_after = list(my_deep.pv[1:])                                                          # (e)
    extended = False
    if len(pv_after) < settings.min_pv_length:
        end = board.copy(stack=False)
        try:
            for uci in my_deep.pv:
                end.push_uci(uci)
        except ValueError:
            end = None
        if end is not None and not end.is_game_over():
            more = _analyse(engine, end, settings.extend_depth, 1, me)
            if more:
                pv_after += more[0].pv
                extended = True

    return MoveAnalysis(game_id=game_id, ply=ply, fen_before=board.fen(), my_move=move.uci(),
                        depth=settings.depth, shallow_depth=settings.shallow_depth,
                        deep_lines=deep, shallow_lines=shallow, my_deep=my_deep,
                        my_shallow=my_shallow[0], pv_after=pv_after, pv_extended=extended)


def analyze_game(engine: chess.engine.SimpleEngine, book: OpeningBook, game_id: str, pgn: str,
                 my_color: str, settings: EngineSettings = EngineSettings(),
                 on_move: Optional[Callable[[int, int], None]] = None) -> GameAnalysisResult:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return GameAnalysisResult(game_id, error="empty PGN")
    me = chess.WHITE if my_color == "white" else chess.BLACK
    board = game.board()
    result = GameAnalysisResult(game_id)
    in_book = True
    mainline = list(game.mainline_moves())
    for ply, move in enumerate(mainline):
        if on_move:
            on_move(ply, len(mainline))
        if in_book:
            in_book = book.is_book_move(board, move)
            if in_book:
                result.book_plies = ply + 1
        if board.turn == me and not in_book:
            analysis = analyze_move(engine, board, move, game_id, ply, settings)
            if analysis is not None:
                result.moves.append(analysis)
        board.push(move)
    return result


# --- worker process -------------------------------------------------------------------

_ENGINE: Optional[chess.engine.SimpleEngine] = None
_SETTINGS = EngineSettings()


def _close_engine() -> None:
    global _ENGINE
    if _ENGINE is not None:
        try:
            _ENGINE.quit()
        except Exception:
            pass
        _ENGINE = None


def _terminate_worker(signum, frame) -> None:
    _close_engine()
    os._exit(0)


def _init_worker(settings: EngineSettings) -> None:
    global _SETTINGS
    _SETTINGS = settings
    atexit.register(_close_engine)
    # Ctrl-C is handled by the main process, which saves finished games and
    # then terminates the workers.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, _terminate_worker)


def _work(job: tuple[str, str, str]) -> GameAnalysisResult:
    """Runs in a worker process. No database access here, by design."""
    global _ENGINE
    game_id, pgn, my_color = job
    try:
        if _ENGINE is None:
            _ENGINE = open_engine(_SETTINGS)
        return analyze_game(_ENGINE, default_book(), game_id, pgn, my_color, _SETTINGS)
    except Exception as exc:  # engine crash, bad PGN... report, don't kill the batch
        _close_engine()
        return GameAnalysisResult(game_id, error=f"{type(exc).__name__}: {exc}")


# --- batch runner (main process: the only SQLite writer) --------------------------------

def run_batch(workers: int, limit: Optional[int] = None, time_controls: Optional[list[str]] = None,
              retry_errors: bool = False, settings: EngineSettings = EngineSettings(),
              date_from: Optional[str] = None, date_to: Optional[str] = None) -> int:
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress, TextColumn,
                               TimeElapsedColumn, TimeRemainingColumn)

    from . import db, leaks

    config.stockfish_path()  # fail fast with a helpful message
    conn = db.connect()
    games = db.pending_games(conn, time_controls or config.ANALYZE_TIME_CONTROLS, retry_errors, limit,
                             date_from, date_to)
    if not games:
        print("Nothing to analyze: no pending games in", time_controls or config.ANALYZE_TIME_CONTROLS)
        return 0

    buffer: list[GameAnalysisResult] = []
    done_count = errors = 0

    def flush() -> None:
        if not buffer:
            return
        db.save_analysis_batch(conn, buffer)
        leaks.redetect(conn, [r.game_id for r in buffer if not r.error])
        buffer.clear()

    columns = (TextColumn("[bold]Analyzing"), BarColumn(), MofNCompleteColumn(),
               TimeElapsedColumn(), TextColumn("ETA"), TimeRemainingColumn(), TextColumn("{task.fields[note]}"))
    queue = iter(games)
    pool = ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(settings,))
    try:
        with Progress(*columns) as progress:
            task = progress.add_task("games", total=len(games), note="")
            in_flight: set = set()
            while True:
                while len(in_flight) < workers * 2:
                    row = next(queue, None)
                    if row is None:
                        break
                    in_flight.add(pool.submit(_work, (row["id"], row["pgn"], row["my_color"])))
                if not in_flight:
                    break
                finished, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in finished:
                    res = future.result()
                    if res.error:
                        errors += 1
                        log.warning("Game %s failed: %s", res.game_id, res.error)
                    buffer.append(res)
                    done_count += 1
                    progress.update(task, advance=1, note=f"errors: {errors}" if errors else "")
                if len(buffer) >= config.COMMIT_BATCH_SIZE:
                    flush()
    except KeyboardInterrupt:
        print("\nInterrupted: saving finished games. Re-run the same command to resume.")
    finally:
        flush()
        # Every finished result is saved. Terminate the workers instead of joining
        # them: each holds an engine thread that would keep the process alive.
        # Interrupted in-flight games stay 'pending' for the next run.
        for proc in list((getattr(pool, "_processes", None) or {}).values()):
            proc.terminate()
        pool.shutdown(wait=True, cancel_futures=True)
    print(f"Analyzed {done_count - errors} games ({errors} errors). {len(games) - done_count} left.")
    return done_count


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="python -m chesscoach.engine",
                                description="Analyze imported games with Stockfish (resumable).")
    p.add_argument("--batch", action="store_true", help="analyze all pending games")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p.add_argument("--limit", type=int, help="analyze at most N games (newest first)")
    p.add_argument("--time-controls", nargs="+", choices=["bullet", "blitz", "rapid"],
                   help=f"default: {config.ANALYZE_TIME_CONTROLS}")
    p.add_argument("--since", metavar="YYYY-MM-DD", help="only games played on or after this date")
    p.add_argument("--until", metavar="YYYY-MM-DD", help="only games played on or before this date")
    p.add_argument("--retry-errors", action="store_true", help="also retry games that failed before")
    p.add_argument("--depth", type=int, default=config.DEFAULT_DEPTH)
    args = p.parse_args(argv)
    if not args.batch:
        p.error("pass --batch to start the analysis")
    from .importer import setup_logging
    setup_logging()
    run_batch(args.workers, args.limit, args.time_controls, args.retry_errors,
              EngineSettings(depth=args.depth), args.since, args.until)


if __name__ == "__main__":
    main()
