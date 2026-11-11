# chess-coach

Finds two decision leaks in your own games and drills them with spaced repetition.

- **Forcing bias**: you played a capture, check or direct attack when the position needed a quiet move.
- **Horizon error**: your move looks fine at depth 6 and collapses at depth 16 (calculation stopped one move short).

## Quick start

1. Install Python 3.10+ and Stockfish (below).
2. `pip install -r requirements.txt`
3. Import games: `python -m chesscoach.importer --chesscom --lichess YOUR_LICHESS_NAME`
4. Analyze overnight: `python -m chesscoach.engine --batch --workers 4`
5. Open the app: `streamlit run app.py`

## 1. Install

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

(Windows: `.venv\Scripts\activate`)

```bash
pip install -r requirements.txt
```

### Stockfish on Ubuntu

```bash
sudo apt-get update && sudo apt-get install -y stockfish
```

The binary lands in `/usr/games/stockfish`; chess-coach finds it there automatically. To use another
build:

```bash
export STOCKFISH_PATH=/path/to/stockfish
```

### Stockfish on Windows

1. Download the Windows build from <https://stockfishchess.org/download/> (AVX2 for most PCs).
2. Unzip, e.g. to `C:\Tools\stockfish\`.
3. Point chess-coach at the `.exe` (PowerShell, permanent for your user):

```powershell
setx STOCKFISH_PATH "C:\Tools\stockfish\stockfish-windows-x86-64-avx2.exe"
```

4. Open a **new** terminal so the variable is visible.

Lookup order: `STOCKFISH_PATH` → `stockfish` on `PATH` → common install locations.

## 2. Configure

Everything tunable is in `chesscoach/config.py`. Identity can also come from environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `CHESSCOACH_CHESSCOM_USERNAME` | Chess.com username | `who_carezz` |
| `CHESSCOACH_LICHESS_USERNAME` | Lichess username | empty |
| `CHESSCOACH_CONTACT` | Contact shown in the API `User-Agent` | placeholder, please set |
| `CHESSCOACH_DB` | SQLite file | `data/chesscoach.db` |
| `CHESSCOACH_POLYGLOT_BOOK` | Optional Polyglot `.bin` opening book | none |
| `STOCKFISH_PATH` | Stockfish binary | auto-detect |

## 3. Import games

```bash
python -m chesscoach.importer --chesscom --lichess YOUR_LICHESS_NAME
```

```bash
python -m chesscoach.importer --pgn my_games.pgn
```

Or use the **Import** page of the app (same functions, plus PGN upload).

- Incremental: existing game IDs are skipped; re-running only fetches new months / newer games.
- Your colour is taken from the configured usernames; games where none of them plays are skipped.
- Every game is classified from its `TimeControl`: bullet (base < 3 min), blitz, rapid, daily.
  All are stored. Only `ANALYZE_TIME_CONTROLS` (`rapid`, `blitz`) are analyzed; add `"bullet"` in
  `config.py` to include it. Daily games are never analyzed.
- Corrupt PGNs and variants are logged to `data/chesscoach.log` and skipped.

## 4. Overnight batch

```bash
python -m chesscoach.engine --batch --workers 4
```

- Resumable: stop with Ctrl-C at any time, run the same command again to continue.
- Newest games first. Useful flags: `--limit 200`, `--time-controls rapid`, `--retry-errors`.
- Use one worker per physical core. Rough speed: about 1 s per analyzed move per worker at depth 16,
  so around 10 s per game with 4 workers (1000 games ≈ 3 hours).
- Workers never touch SQLite; the main process commits results in batches.

Per non-book move of yours the engine stores: MultiPV 3 at depth 16 and depth 6, your move at both
depths, and the depth-16 line after your move (extended at depth 10 if shorter than 8 plies). The
hash table is cleared between searches so the shallow search is honestly shallow.

## 5. The app

```bash
streamlit run app.py
```

| Page | What it does |
|---|---|
| Leak report | Top 3 patterns with example boards; breakdown by leak type, phase, time control, time trouble; monthly trend. Filters: time control, colour, opening, result, dates. |
| Trainer | Your leak positions as SM-2 cards. Move on the board, see the engine's lines, grade Again / Hard / Good / Easy. |
| Find the quiet move | Same drill, forcing-bias positions only. |
| Calibration | Judge 20 random flagged positions as Valid / False positive; precision per leak type shows which threshold to tune. |
| Import | Fetch from Chess.com / Lichess, upload PGN, see what is waiting for analysis. |

## 6. Tuning loop

1. Calibrate 20 positions.
2. Edit thresholds in `chesscoach/config.py`.
3. Re-detect (no engine needed, takes seconds):

```bash
python -m chesscoach.leaks
```

4. Restart the app. Labels and SRS progress are kept. Changing a *depth* requires re-analysis.

## How leaks are defined

All scores are **your** win %: `50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)`, mate for you = 100,
against you = 0.

**Forcing bias** (all must hold)
- your move is forcing: capture, check, or a new attack on a piece that is undefended or worth at
  least as much as the attacker;
- the best depth-16 move is quiet;
- your move loses ≥ `WIN_PCT_BLUNDER_DROP` (10);
- the best forcing move in the top 3 is ≥ `FORCING_LEAK_MARGIN` (8) worse than the best quiet move,
  or there is none.

**Horizon** (all must hold)
- at depth 6 your move is within `HORIZON_SHALLOW_TOLERANCE` (3) of the best shallow move;
- at depth 16 it loses ≥ `WIN_PCT_BLUNDER_DROP`;
- the refutation lands at ply ≥ `HORIZON_MIN_REFUTATION_PLY` (3), or no concrete refutation exists
  (type `positional`).

**Refutation ply**: ply 1 is the opponent's first reply. Material balance (1/3/3/5/9, relative to
before your move) is sampled only at quiet points, where the side to move has no capture that wins
material by static exchange evaluation. The refutation ply starts the final run of quiet points at
which you are ≥ 2 pawns down. Mate against you counts too (type `mate`); otherwise type `material`.

**Time trouble**: your clock when you started thinking (clock after your previous move + increment)
is below `TIME_TROUBLE_RATIO` × (base + 40 × increment). No clock data = `unknown`, excluded from
time-trouble statistics.

**Phase** (non-pawn material of both sides, max 62): opening = move ≤ 12 and material ≥ 56;
endgame = material ≤ 26; otherwise middlegame.

## Tests

```bash
python -m pytest
```

## Layout

```
app.py                     Streamlit pages
chesscoach/config.py       all settings
chesscoach/db.py           SQLite schema, single-writer save functions
chesscoach/importer.py     Chess.com, Lichess, PGN
chesscoach/book.py         opening book (Lichess chess-openings, optional Polyglot)
chesscoach/engine.py       Stockfish analysis + batch CLI
chesscoach/leaks.py        detectors, refutation ply, SEE
chesscoach/trainer.py      SM-2 and answer checking
chesscoach/report.py       data frames for the leak report
chesscoach/board_component/  client-side training board
```

Opening names and book positions come from the
[Lichess chess-openings](https://github.com/lichess-org/chess-openings) dataset (CC0), bundled in
`chesscoach/data/openings/`.
