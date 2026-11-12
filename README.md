# chess-coach

Finds two decision leaks in your own games and drills them with spaced repetition.

- **Forcing bias**: you played a capture, check or direct attack when the position needed a quiet move.
- **Horizon error**: your move looks fine at depth 6 and collapses at depth 16 (calculation stopped one move short).

## Quick start

1. Install Python 3.10+ and Stockfish (below).
2. `pip install -r requirements.txt`
3. Open the app: `streamlit run app.py`
4. Import page: choose dates and time formats, press **Import games**
5. Same page: press **Start analysis** (runs in the background; stop and resume any time)
6. Read the report, then train

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

Open the app (`streamlit run app.py`) and go to **Import**:

1. Pick **From** / **To** dates and the **time formats** (bullet, blitz, rapid, daily).
2. Press **Import games**.

Games already stored are never downloaded twice, so you can widen the selection later. The same page
can remove stored games that fall outside the selection, and prints the matching analysis command.

Command-line equivalent:

```bash
python -m chesscoach.importer --chesscom --from 2026-01-01 --to 2026-11-11 --time-controls rapid blitz
```

```bash
python -m chesscoach.importer --pgn my_games.pgn
```

- Your colour is taken from the configured username; games where it does not play are skipped.
- Time format comes from `TimeControl`: bullet (base < 3 min), blitz, rapid, daily. Only
  `ANALYZE_TIME_CONTROLS` (`rapid`, `blitz`) are analyzed; add `"bullet"` in `config.py` to include
  it. Daily games are never analyzed.
- Aborted games with no moves are counted and ignored. Corrupt PGNs and variants are logged to
  `data/chesscoach.log` and skipped.
- A Lichess importer exists for the command line only (`--lichess NAME`).

## 4. Overnight batch

```bash
python -m chesscoach.engine --batch --workers 4
```

- Resumable: stop with Ctrl-C at any time, run the same command again to continue.
- Newest games first. Useful flags: `--since 2026-01-01`, `--until 2026-11-11`, `--time-controls rapid`,
  `--limit 200`, `--retry-errors`.
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

A step bar on top follows the workflow: **Import → Analyze → Report → Train**.

| Page | What it does |
|---|---|
| 1 · Import & analyze | Choose dates and time formats, import from Chess.com, **Start / Stop analysis** with a live progress bar. Also: remove unwanted games, upload PGN, terminal command. |
| 2 · Leak report | Headline findings, top 3 patterns with boards, breakdown by leak type / phase / time control / time trouble, monthly trend, and a browser to replay every leak. Filters: time control, colour, opening, result, dates. |
| 3 · Trainer | Your leak positions as SM-2 cards. Move, then replay the engine lines and the refutation of your game move with ◀ ▶. Shows today's count, day streak and 30-day accuracy per leak type. |
| Game review | One game start to end: fetch your newest games or paste a PGN, analyze it in about a minute, then step through it move by move: what each move is for, your verdict, the better move, the plan behind it and why yours fell short. Also lessons, a win % chart and a table of key moments. |
| Find the quiet move | Same drill, forcing-bias positions only. |
| Calibration | Judge 20 random flagged positions as Valid / False positive; precision per leak type shows which threshold to tune. |

### On your phone

```bash
streamlit run app.py --server.address 0.0.0.0
```

Open `http://YOUR-COMPUTER-IP:8501` on the phone, on the same Wi-Fi (the Import page shows the exact
address). The layout adapts to small screens; pieces move by tap-tap or drag. Anyone on that Wi-Fi can
open the app while it runs this way, so use it on a network you trust.

### Hosted (no laptop needed)

GitHub cannot run the app itself, but [Streamlit Community Cloud](https://share.streamlit.io) runs it
for free straight from this repository:

1. Sign in at share.streamlit.io with GitHub.
2. **Create app** → repository `Chess-Analysis`, branch `main`, main file `app.py` → **Deploy**.
3. Open the app's URL on any device. In the app's sharing settings, keep it private to your account.

`packages.txt` installs Stockfish there automatically. Limits of the free server:

- It is slow (about one shared core): fine for **Game review** of single games and for training,
  not for analyzing thousands of games. Do the big batch on your own computer.
- Its disk is wiped when the app restarts or sleeps. Use **Import → Backup and restore** to download
  your data after a session and restore it later. A backup from your computer (`data/chesscoach.db`)
  can be restored there too, which is the way to bring a big analysis to your phone.

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
chesscoach/theme.py        walnut look (CSS, generated wood grain)
chesscoach/runner.py       start / stop the batch from the app
chesscoach/review.py       single-game review (grades, lessons, move-by-move walkthrough)
chesscoach/ideas.py        what a move is for, from board features
chesscoach/board_component/  client-side training board
```

Opening names and book positions come from the
[Lichess chess-openings](https://github.com/lichess-org/chess-openings) dataset (CC0), bundled in
`chesscoach/data/openings/`.
