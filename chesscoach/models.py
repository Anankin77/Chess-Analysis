"""Plain dataclasses passed between modules and processes.

Engine workers return these to the main process; only the main process
touches SQLite (see db.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MoveRecord:
    ply: int  # 0-based half-move index in the game
    fullmove: int
    color: str  # "white" | "black"
    is_mine: bool
    san: str
    uci: str
    fen_before: str
    clock_seconds: Optional[float]  # clock after the move ([%clk]), None if missing


@dataclass
class GameRecord:
    id: str
    source: str  # "chesscom" | "lichess" | "pgn"
    url: str
    white: str
    black: str
    my_color: str  # "white" | "black"
    result: str  # "win" | "loss" | "draw" | "unknown" (from my side)
    date: str  # ISO yyyy-mm-dd
    time_control: str  # raw TimeControl header
    base_seconds: Optional[int]
    increment_seconds: Optional[int]
    tc_class: str  # bullet | blitz | rapid | daily | unknown
    eco: str
    opening: str
    white_elo: Optional[int]
    black_elo: Optional[int]
    pgn: str
    moves: list[MoveRecord] = field(default_factory=list)


@dataclass
class PVLine:
    uci: str  # first move of the line
    cp: Optional[int]  # centipawns from MY perspective (None if mate)
    mate: Optional[int]  # mate distance from MY perspective (+ = I mate), None if no mate
    win: float  # MY win %
    pv: list[str]  # full line in UCI, starting with `uci`


@dataclass
class MoveAnalysis:
    game_id: str
    ply: int
    fen_before: str
    my_move: str  # UCI
    depth: int
    shallow_depth: int
    deep_lines: list[PVLine]  # MultiPV at DEFAULT_DEPTH, best first
    shallow_lines: list[PVLine]  # MultiPV at SHALLOW_DEPTH, best first
    my_deep: PVLine  # my move at DEFAULT_DEPTH
    my_shallow: PVLine  # my move at SHALLOW_DEPTH
    pv_after: list[str]  # full-depth PV after my move (opponent moves first), maybe extended
    pv_extended: bool

    @property
    def best_deep_win(self) -> float:
        return self.deep_lines[0].win

    @property
    def best_shallow_win(self) -> float:
        return self.shallow_lines[0].win

    @property
    def win_drop(self) -> float:
        return max(0.0, self.best_deep_win - self.my_deep.win)


@dataclass
class GameAnalysisResult:
    game_id: str
    moves: list[MoveAnalysis] = field(default_factory=list)
    book_plies: int = 0
    error: Optional[str] = None


@dataclass
class LeakFinding:
    game_id: str
    ply: int
    leak_type: str  # "forcing_bias" | "horizon"
    sub_type: Optional[str]  # horizon: "material" | "mate" | "positional"
    refutation_ply: Optional[int]
    win_drop: float
    phase: str  # opening | middlegame | endgame
    time_trouble: str  # yes | no | unknown
    fen_before: str
    my_move: str
    best_move: str
