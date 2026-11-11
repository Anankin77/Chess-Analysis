"""Opening book: Lichess chess-openings dataset (TSV) and/or a Polyglot .bin.

A move is "book" when the position it leads to appears in a named opening
line of the dataset, or when the Polyglot book lists the move."""

from __future__ import annotations

import csv
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

import chess
import chess.polyglot

from . import config

log = logging.getLogger(__name__)


class OpeningBook:
    def __init__(self, openings_dir: Optional[Path] = None, polyglot_path: Optional[str] = None):
        self.positions: set[str] = set()  # every position on a named line
        self.names: dict[str, tuple[str, str]] = {}  # line end position -> (eco, name)
        self.polyglot: Optional[chess.polyglot.MemoryMappedReader] = None
        if openings_dir and Path(openings_dir).is_dir():
            self._load_tsv(Path(openings_dir))
        if polyglot_path:
            try:
                self.polyglot = chess.polyglot.open_reader(polyglot_path)
            except OSError as exc:
                log.warning("Cannot open Polyglot book %s: %s", polyglot_path, exc)
        if not self.positions and not self.polyglot:
            log.warning("No opening book available: every move will be analyzed.")

    def _load_tsv(self, directory: Path) -> None:
        for path in sorted(directory.glob("*.tsv")):
            with path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    board = chess.Board()
                    try:
                        for token in row["pgn"].split():
                            if token.endswith("."):
                                continue
                            board.push_san(token)
                            self.positions.add(board.epd())
                    except ValueError:
                        log.warning("Bad opening line in %s: %s", path.name, row.get("pgn"))
                        continue
                    self.names[board.epd()] = (row["eco"], row["name"])

    def is_book_move(self, board: chess.Board, move: chess.Move) -> bool:
        """`board` is the position BEFORE `move`."""
        if self.polyglot is not None:
            if any(entry.move == move for entry in self.polyglot.find_all(board)):
                return True
        if self.positions:
            board.push(move)
            try:
                return board.epd() in self.positions
            finally:
                board.pop()
        return False

    def name_for(self, board: chess.Board) -> Optional[tuple[str, str]]:
        return self.names.get(board.epd())

    def classify(self, moves: list[chess.Move]) -> tuple[str, str, int]:
        """(eco, opening name, number of book plies) for a game from the start position."""
        board = chess.Board()
        eco, name, plies = "", "", 0
        for move in moves:
            if not self.is_book_move(board, move):
                break
            board.push(move)
            plies += 1
            hit = self.name_for(board)
            if hit:
                eco, name = hit
        return eco, name, plies


@lru_cache(maxsize=1)
def default_book() -> OpeningBook:
    return OpeningBook(config.OPENINGS_DIR, config.POLYGLOT_BOOK_PATH or None)
