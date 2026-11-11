"""chess-coach Streamlit app.   Run:  streamlit run app.py"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import chess
import chess.svg
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from chesscoach import config, db, importer, leaks, report, theme, trainer

st.set_page_config(page_title="chess-coach", page_icon="♟", layout="wide")

_board_component = components.declare_component(
    "chesscoach_board", path=str(Path(__file__).parent / "chesscoach" / "board_component"))

RED, GREEN, BOARD_COLORS = theme.RED, theme.GREEN, theme.BOARD_COLORS
TIME_FORMATS = ["bullet", "blitz", "rapid", "daily"]

st.markdown(theme.css(), unsafe_allow_html=True)


_SELECTION_FILE = config.DB_PATH.parent / "ui_selection.json"


def selection() -> tuple[date, date, list[str]]:
    """Dates and time formats chosen on the Import page (remembered between visits)."""
    ss = st.session_state
    if "sel_from" not in ss:
        today = date.today()
        saved = {}
        try:
            saved = json.loads(_SELECTION_FILE.read_text())
        except (OSError, ValueError):
            pass
        try:
            ss["sel_from"] = date.fromisoformat(saved["from"])
            ss["sel_to"] = min(today, date.fromisoformat(saved["to"])) if not saved.get("to_is_today") else today
            ss["sel_formats"] = [f for f in saved["formats"] if f in TIME_FORMATS]
        except (KeyError, ValueError, TypeError):
            ss["sel_from"], ss["sel_to"] = today - timedelta(days=90), today
            ss["sel_formats"] = list(config.ANALYZE_TIME_CONTROLS)
    return ss["sel_from"], ss["sel_to"], ss["sel_formats"]


def save_selection(d_from: date, d_to: date, formats: list[str]) -> None:
    if (d_from, d_to, formats) == selection():
        return
    st.session_state.update(sel_from=d_from, sel_to=d_to, sel_formats=formats)
    try:
        _SELECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SELECTION_FILE.write_text(json.dumps({"from": d_from.isoformat(), "to": d_to.isoformat(),
                                               "to_is_today": d_to == date.today(), "formats": formats}))
    except OSError:
        pass


def selection_counts(d_from: date, d_to: date, formats: list[str]) -> tuple[int, int]:
    """(analyzed, total) analyzable games inside the selection."""
    tcs = [f for f in formats if f != "daily"]
    if not tcs:
        return 0, 0
    row = get_conn().execute(
        f"""SELECT COALESCE(SUM(analysis_status = 'done'), 0), COUNT(*) FROM games
            WHERE date BETWEEN ? AND ? AND tc_class IN ({','.join('?' * len(tcs))})""",
        [d_from.isoformat(), d_to.isoformat(), *tcs]).fetchone()
    return int(row[0]), int(row[1])


def batch_command(d_from: date, d_to: date, formats: list[str]) -> str:
    tcs = [f for f in formats if f != "daily"] or list(config.ANALYZE_TIME_CONTROLS)
    return (f"python -m chesscoach.engine --batch --workers 4 --since {d_from} --until {d_to} "
            f"--time-controls {' '.join(tcs)}")


STEPS = [("", "Import", "pick dates & formats"), ("#analyze", "Analyze", "one terminal command"),
         ("report", "Report", "see your leaks"), ("trainer", "Train", "drill your mistakes")]


def step_bar(active: int) -> None:
    """Import -> Analyze -> Report -> Train, always one click away."""
    conn = get_conn()
    done = [conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] > 0,
            conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] > 0, False, False]
    html = ""
    for i, (path, name, hint) in enumerate(STEPS):
        cls = "on" if i == active else ("done" if done[i] else "")
        mark = "✓" if done[i] and i != active else str(i + 1)
        html += (f'<a class="{cls}" href="/{path}" target="_self"><span class="n">{mark}</span>'
                 f'<span>{name}<small>{hint}</small></span></a>')
    st.markdown(f'<div class="steps">{html}</div>', unsafe_allow_html=True)


@st.cache_resource
def get_conn():
    return db.connect()


def data_version() -> tuple:
    """Changes whenever analysis or leaks change, to invalidate cached frames."""
    conn = get_conn()
    return (conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0],
            conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM leaks").fetchone()[:2])


@st.cache_data(show_spinner="Loading analysis…")
def load_frames(version: tuple) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = get_conn()
    return report.load_leaks(conn), report.load_moves(conn)


def board_svg(fen: str, my_move: str | None = None, best_move: str | None = None, size: int = 340) -> str:
    board = chess.Board(fen)
    arrows = []
    if my_move:
        m = chess.Move.from_uci(my_move)
        arrows.append(chess.svg.Arrow(m.from_square, m.to_square, color=RED))
    if best_move:
        m = chess.Move.from_uci(best_move)
        arrows.append(chess.svg.Arrow(m.from_square, m.to_square, color=GREEN))
    return chess.svg.board(board, arrows=arrows, orientation=board.turn, size=size, colors=BOARD_COLORS)


def san(fen: str, uci: str) -> str:
    try:
        return chess.Board(fen).san(chess.Move.from_uci(uci))
    except (ValueError, AssertionError):
        return uci


def move_label(fen: str, uci: str) -> str:
    board = chess.Board(fen)
    dots = "." if board.turn == chess.WHITE else "…"
    return f"{board.fullmove_number}{dots}{san(fen, uci)}"


# =============================================================== Page 1: Leak report

def page_report() -> None:
    step_bar(2)
    st.title("Leak report")
    all_leaks, all_moves = load_frames(data_version())
    if all_moves.empty:
        st.info("No analyzed games yet. Run this in a terminal, then refresh (results appear while it runs):")
        st.code(batch_command(*selection()), language="bash")
        return

    with st.sidebar:
        st.header("Filters")
        tcs = st.multiselect("Time control", sorted(all_moves["tc_class"].dropna().unique()))
        colours = st.multiselect("Colour", ["white", "black"])
        openings = st.multiselect("Opening", sorted(all_moves["opening_family"].unique()))
        results = st.multiselect("Result", ["win", "draw", "loss"])
        dates = all_moves["date"].replace("", pd.NA).dropna()
        date_range = None
        if not dates.empty:
            lo, hi = date.fromisoformat(dates.min()), date.fromisoformat(dates.max())
            date_range = st.date_input("Date range", (lo, hi), min_value=lo, max_value=hi)
    d_from, d_to = (date_range if date_range and len(date_range) == 2 else (None, None))
    flt = dict(tcs=tcs, colours=colours, openings=openings, results=results, date_from=d_from, date_to=d_to)
    moves = report.apply_filters(all_moves, **flt)
    lk = report.apply_filters(all_leaks, **flt)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Games analyzed", moves["game_id"].nunique())
    c2.metric("My moves analyzed", len(moves))
    c3.metric("Leaks", len(lk))
    c4.metric("Leaks per 100 moves", f"{100 * len(lk) / len(moves):.2f}" if len(moves) else "–")
    if lk.empty:
        st.success("No leaks match these filters.")
        return

    st.subheader("Top 3 leak patterns")
    for rank, pat in enumerate(report.top_patterns(lk).itertuples(), start=1):
        st.markdown(f"**{rank}. {pat.label} · {pat.phase}** — {pat.count} leaks, "
                    f"average loss {pat.avg_drop:.0f} win %")
        examples = (lk[(lk["label"] == pat.label) & (lk["phase"] == pat.phase)]
                    .sort_values("win_drop", ascending=False).head(3))
        for col, ex in zip(st.columns(3), examples.itertuples()):
            with col:
                st.image(board_svg(ex.fen_before, ex.my_move, ex.best_move), use_container_width=True)
                caption = (f"Played {move_label(ex.fen_before, ex.my_move)} (red), best "
                           f"{san(ex.fen_before, ex.best_move)} (green) · −{ex.win_drop:.0f} win %")
                if pd.notna(ex.refutation_ply):
                    caption += f" · refuted at ply {int(ex.refutation_ply)}"
                st.caption(caption + f" · {ex.date}" + (f" · [game]({ex.url})" if ex.url else ""))

    st.subheader("Breakdown")
    by_type = lk.groupby("label").agg(Leaks=("ply", "size"), avg=("win_drop", "mean")).rename(
        columns={"avg": "Avg win % lost"}).round(1).sort_values("Leaks", ascending=False)
    left, right = st.columns([3, 2])
    left.bar_chart(by_type["Leaks"], horizontal=True, y_label="", x_label="Leaks by type")
    right.dataframe(by_type, use_container_width=True)

    tt_leaks, tt_moves = lk[lk["time_trouble"] != "unknown"], moves[moves["time_trouble"] != "unknown"]
    tabs = st.tabs(["Phase", "Time control", "Time trouble"])
    for tab, (column, l_df, m_df, note) in zip(tabs, [
            ("phase", lk, moves, ""),
            ("tc_class", lk, moves, ""),
            ("time_trouble", tt_leaks, tt_moves,
             f"Time trouble = clock below {config.TIME_TROUBLE_RATIO:.0%} of base + "
             f"{config.TIME_TROUBLE_MOVES} × increment. Moves without clock data are excluded "
             f"({len(lk) - len(tt_leaks)} leaks).")]):
        with tab:
            if l_df.empty:
                st.caption("No data.")
                continue
            table = report.breakdown(l_df, m_df, column)
            a, b = st.columns([2, 3])
            a.bar_chart(table["Leaks per 100 moves"], y_label="Leaks per 100 moves", x_label="")
            b.dataframe(table, use_container_width=True)
            if note:
                st.caption(note)

    st.subheader("Monthly trend")
    trend = report.monthly_trend(lk, moves)
    if len(trend) >= 2:
        st.line_chart(trend, y_label="Leaks per 100 moves", x_label="")
    else:
        st.caption("Needs analyzed games from at least two months.")
        st.dataframe(trend)


# =============================================================== Pages 2 + 3: training

def _training_ui(mode: str, leak_type: str | None, prompt: str, allow_ahead_default: bool) -> None:
    conn = get_conn()
    ahead = st.toggle("Include cards that are not due yet", value=allow_ahead_default, key=f"{mode}_ahead")
    due = trainer.card_queue(conn, leak_type)
    queue = trainer.card_queue(conn, leak_type, include_not_due=True) if ahead else due
    st.caption(f"{len(due)} due today · {len(queue)} in this session's pool")
    if not queue:
        if conn.execute("SELECT COUNT(*) FROM leaks").fetchone()[0] == 0:
            st.info("No leak positions yet. Import games and run the batch first.")
        else:
            st.success("Nothing due. Come back tomorrow, or include cards that are not due yet.")
        return

    state_key = f"{mode}_card"
    by_id = {row["id"]: row for row in queue}
    if st.session_state.get(state_key) not in by_id:
        st.session_state[state_key] = queue[0]["id"]
    card = by_id[st.session_state[state_key]]
    payload = trainer.puzzle_payload(conn, card["game_id"], card["ply"])
    if payload is None:
        st.session_state.pop(state_key, None)
        st.warning("Analysis for this card is missing; skipping.")
        return

    board_col, side = st.columns([3, 2])
    with board_col:
        # Moves are handled and checked inside the component (client side); it
        # reports back once, after the answer, which reruns only this fragment.
        answer = _board_component(puzzle_id=card["id"], prompt=prompt, pieces=chess.svg.PIECES,
                                  key=f"{mode}_board_{card['id']}", default=None, **payload)
    with side:
        st.markdown(f"**Card {card['id']}** · seen {card['reviews']}× · "
                    f"interval {card['interval']} d · ease {card['ease_factor']:.2f}")
        answered = bool(answer) and answer.get("puzzle") == card["id"]
        if not answered:
            st.caption("Make your move on the board. The leak type is revealed after you answer.")
            if st.button("Skip", key=f"{mode}_skip"):
                ids = list(by_id)
                st.session_state[state_key] = ids[(ids.index(card["id"]) + 1) % len(ids)]
                st.rerun(scope="fragment")
            return
        correct = bool(answer.get("correct"))
        st.markdown(("✅ Correct." if correct else "❌ Incorrect.") + f" Leak: `{card['leak_labels']}` "
                    f"(cost {card['win_drop']:.0f} win % in the game).")
        game = conn.execute("SELECT url, white, black, date FROM games WHERE id=?", (card["game_id"],)).fetchone()
        st.caption(f"{game['white']} – {game['black']}, {game['date']}" +
                   (f" · [open game]({game['url']})" if game["url"] else ""))
        st.write("How did it go?")
        cols = st.columns(4)
        for col, (name, grade) in zip(cols, trainer.GRADES.items()):
            # A wrong answer can only be graded Again or Hard.
            if col.button(name, key=f"{mode}_grade_{grade}", use_container_width=True,
                          disabled=(not correct and grade > 3),
                          type="primary" if (grade == 4 if correct else grade == 1) else "secondary"):
                new = trainer.review(conn, card["id"], grade)
                st.session_state.pop(state_key, None)
                st.toast(f"{name}: next review in {new.interval} day(s)")
                st.rerun(scope="fragment")


@st.fragment
def trainer_fragment() -> None:
    _training_ui("trainer", None, "Find the best move.", allow_ahead_default=False)


@st.fragment
def quiet_fragment() -> None:
    _training_ui("quiet", "forcing_bias", "No capture, check or threat works here: find the quiet move.",
                 allow_ahead_default=True)


def page_trainer() -> None:
    step_bar(3)
    st.title("Trainer")
    st.caption("Positions from your own leaks, scheduled with SM-2. An answer counts if it is within "
               f"{config.PUZZLE_ACCEPT_TOLERANCE:g} win % of the best move.")
    trainer_fragment()


def page_quiet() -> None:
    st.title("Find the quiet move")
    st.caption("Only positions where you played a capture, check or attack and a quiet move was called for.")
    quiet_fragment()


# =============================================================== Page 4: Calibration

TUNING_HINTS = {
    "Forcing bias": "FORCING_LEAK_MARGIN ↑, WIN_PCT_BLUNDER_DROP ↑",
    "Horizon (material)": "HORIZON_MIN_REFUTATION_PLY ↑, HORIZON_SHALLOW_TOLERANCE ↓",
    "Horizon (mate)": "HORIZON_MIN_REFUTATION_PLY ↑, HORIZON_SHALLOW_TOLERANCE ↓",
    "Horizon (positional)": "WIN_PCT_BLUNDER_DROP ↑, HORIZON_SHALLOW_TOLERANCE ↓, SHALLOW_DEPTH ↑ (needs re-analysis)",
}


def _new_calibration_sample(conn) -> list[int]:
    rows = conn.execute(
        """SELECT l.id, c.verdict IS NOT NULL AS labelled FROM leaks l
           LEFT JOIN calibration_labels c ON c.game_id = l.game_id AND c.ply = l.ply
                AND c.leak_type = l.leak_type""").fetchall()
    fresh = [r["id"] for r in rows if not r["labelled"]]
    done = [r["id"] for r in rows if r["labelled"]]
    random.shuffle(fresh)
    random.shuffle(done)
    return (fresh + done)[:config.CALIBRATION_SAMPLE_SIZE]   # unlabelled positions first


def page_calibration() -> None:
    conn = get_conn()
    st.title("Calibration")
    st.caption(f"Judge {config.CALIBRATION_SAMPLE_SIZE} random flagged positions. Precision per leak type "
               "tells you which threshold in `chesscoach/config.py` to tune.")

    st.subheader("Precision per leak type")
    table = report.precision_table(conn)
    table["If precision is low, try"] = table["Leak type"].map(TUNING_HINTS)
    st.dataframe(table, hide_index=True, use_container_width=True,
                 column_config={"Precision": st.column_config.NumberColumn(format="%.1f %%")})
    a, b, _ = st.columns([1, 1, 2])
    if a.button("Re-run detection", help="Re-detect leaks from stored analysis with the current config.py"):
        n = leaks.redetect(conn)
        st.session_state.pop("calib_sample", None)
        st.toast(f"{n} leaks with the current thresholds")
        st.rerun()
    if b.button("New sample of 20"):
        st.session_state.pop("calib_sample", None)
        st.rerun()
    st.caption("Thresholds are read at start-up: after editing config.py restart the app (or run "
               "`python -m chesscoach.leaks`). Labels are kept; precision only counts labelled "
               "positions that are still flagged.")

    if "calib_sample" not in st.session_state:
        st.session_state["calib_sample"] = _new_calibration_sample(conn)
        st.session_state["calib_idx"] = 0
    sample: list[int] = st.session_state["calib_sample"]
    if not sample:
        st.info("No flagged positions yet. Import games and run the batch first.")
        return
    idx = st.session_state.get("calib_idx", 0)
    st.subheader("Positions")
    if idx >= len(sample):
        st.success(f"All {len(sample)} positions judged. Check the precision table above, or draw a new sample.")
        return
    st.progress(idx / len(sample), text=f"Position {idx + 1} of {len(sample)}")

    leak = conn.execute("SELECT * FROM leaks WHERE id=?", (sample[idx],)).fetchone()
    analysis = db.get_analysis(conn, leak["game_id"], leak["ply"]) if leak else None
    if leak is None or analysis is None:
        st.session_state["calib_idx"] = idx + 1
        st.rerun()
    game = conn.execute("SELECT * FROM games WHERE id=?", (leak["game_id"],)).fetchone()
    existing = conn.execute("SELECT verdict FROM calibration_labels WHERE game_id=? AND ply=? AND leak_type=?",
                            (leak["game_id"], leak["ply"], leak["leak_type"])).fetchone()

    board_col, info = st.columns([2, 3])
    fen = leak["fen_before"]
    board_col.image(board_svg(fen, leak["my_move"], leak["best_move"], size=420), use_container_width=True)
    with info:
        key = leak["leak_type"] + (f":{leak['sub_type']}" if leak["sub_type"] else "")
        st.markdown(f"### {report.LEAK_LABELS.get(key, key)}")
        board = chess.Board(fen)
        st.markdown(
            f"**Played** {move_label(fen, leak['my_move'])} (red) · **best** {san(fen, leak['best_move'])} (green)\n\n"
            f"- Depth {analysis.depth}: mine **{analysis.my_deep.win:.1f}%** vs best "
            f"**{analysis.best_deep_win:.1f}%** (−{analysis.win_drop:.1f})\n"
            f"- Depth {analysis.shallow_depth}: mine {analysis.my_shallow.win:.1f}% vs best "
            f"{analysis.best_shallow_win:.1f}%\n"
            f"- Refutation ply: {leak['refutation_ply'] if leak['refutation_ply'] is not None else '–'}"
            f" · phase: {leak['phase']} · time trouble: {leak['time_trouble']}")
        lines = [{"Move": san(fen, ln.uci), "Win %": round(ln.win, 1),
                  "Forcing": "yes" if leaks.is_forcing(board, chess.Move.from_uci(ln.uci)) else "quiet",
                  "Line": trainer._san_line(board, ln.pv)} for ln in analysis.deep_lines]
        lines.append({"Move": san(fen, leak["my_move"]) + " (played)", "Win %": round(analysis.my_deep.win, 1),
                      "Forcing": "yes" if leaks.is_forcing(board, chess.Move.from_uci(leak["my_move"])) else "quiet",
                      "Line": trainer._san_line(board, [leak["my_move"]] + analysis.pv_after, 12)})
        st.dataframe(pd.DataFrame(lines), hide_index=True, use_container_width=True)
        st.caption(f"{game['white']} – {game['black']} · {game['date']} · {game['tc_class']}" +
                   (f" · [open game]({game['url']})" if game["url"] else "") +
                   (f" · already labelled: {existing['verdict']}" if existing else ""))

        def label(verdict: str) -> None:
            db.save_label(conn, leak["game_id"], leak["ply"], leak["leak_type"], verdict)
            st.session_state["calib_idx"] = idx + 1

        v, f, s = st.columns(3)
        v.button("Valid", type="primary", use_container_width=True, on_click=label, args=("valid",))
        f.button("False positive", use_container_width=True, on_click=label, args=("false_positive",))
        if s.button("Skip", use_container_width=True):
            st.session_state["calib_idx"] = idx + 1
            st.rerun()


# =============================================================== Import & status

@st.fragment(run_every=5)
def analysis_progress() -> None:
    """Live view of the terminal batch; refreshes itself every 5 seconds."""
    d_from, d_to, formats = selection()
    done, total = selection_counts(d_from, d_to, formats)
    if total == 0:
        st.caption("No stored games in this selection yet. Import first.")
        return
    leaks_found = get_conn().execute("SELECT COUNT(*) FROM leaks").fetchone()[0]
    st.progress(done / total, text=f"Analyzed {done} of {total} games in the selection · {leaks_found} leaks found")
    hours = (total - done) * 10 / 3600
    if done < total:
        st.caption(f"About {hours:.1f} h left with 4 workers. Ctrl-C stops it; the same command resumes. "
                   "This bar updates by itself.")
    if done:
        st.page_link(PAGES["report"], label="Open the leak report →", icon="📊")


def page_import() -> None:
    conn = get_conn()
    step_bar(0)
    st.title("Import")

    st.subheader("1 · Choose your games")
    d0, t0, f0 = selection()
    today = date.today()
    u, f, t = st.columns([2, 1, 1])
    user = u.text_input("Chess.com username", config.CHESSCOM_USERNAME)
    d_from = f.date_input("From", d0, min_value=date(2007, 1, 1), max_value=today)
    d_to = t.date_input("To", t0, min_value=date(2007, 1, 1), max_value=today)
    formats = st.multiselect("Time formats", TIME_FORMATS, default=f0,
                             help="bullet: base under 3 min · blitz: under 10 min · rapid: 10 min and more · "
                                  "daily is stored but never analyzed")
    save_selection(d_from, d_to, formats)
    ready = bool(user.strip()) and bool(formats) and d_from <= d_to
    if d_from > d_to:
        st.warning("'From' is after 'To'.")
    done, total = selection_counts(d_from, d_to, formats)
    b, info = st.columns([1, 3])
    clicked = b.button("Import games", type="primary", disabled=not ready, use_container_width=True)
    info.caption(f"**{total}** games of this selection are already stored. Stored games are never downloaded "
                 "twice, so pressing Import only fetches what is missing.")
    if clicked:
        bar = st.progress(0.0, text="Contacting Chess.com…")
        try:
            stats = importer.import_chesscom(conn, user, d_from.isoformat(), d_to.isoformat(), formats,
                                             progress=lambda msg, frac: bar.progress(min(frac, 1.0), text=msg))
            bar.empty()
            st.success(f"Imported {stats.imported} new games. Already stored: {stats.existing} · "
                       f"outside your selection: {stats.filtered} · aborted with no moves: {stats.aborted} · "
                       f"unreadable or variants: {stats.skipped}")
        except Exception as exc:
            bar.empty()
            st.error(f"Chess.com import failed: {exc}")

    st.markdown('<div id="analyze"></div>', unsafe_allow_html=True)
    st.subheader("2 · Analyze them")
    st.write("Copy this into a terminal (project folder, `.venv` active). It matches the selection above:")
    st.code(batch_command(d_from, d_to, formats), language="bash")
    analysis_progress()

    st.subheader("Stored games")
    stored = pd.read_sql_query(
        """SELECT tc_class AS "Time format", COUNT(*) AS Games, MIN(NULLIF(date, '')) AS "First", MAX(date) AS "Last",
                  SUM(analysis_status = 'done') AS Analyzed
           FROM games GROUP BY tc_class ORDER BY Games DESC""", conn)
    if stored.empty:
        st.caption("Nothing stored yet.")
    else:
        st.dataframe(stored, hide_index=True, use_container_width=True)
        with st.expander("Remove stored games outside the selection"):
            n = db.delete_games_outside(conn, d_from.isoformat(), d_to.isoformat(), formats, dry_run=True)
            st.write(f"**{n}** stored games are outside {d_from} … {d_to} / {', '.join(formats) or 'no format'}. "
                     "Removing them also deletes their analysis, leaks and cards.")
            sure = st.checkbox("Yes, delete them permanently", key="confirm_delete")
            if st.button("Remove", disabled=not (sure and n and ready)):
                removed = db.delete_games_outside(conn, d_from.isoformat(), d_to.isoformat(), formats)
                st.session_state.pop("confirm_delete", None)
                st.toast(f"Removed {removed} games")
                st.rerun()

    with st.expander("Upload a PGN file instead"):
        files = st.file_uploader("PGN files", type=["pgn"], accept_multiple_files=True)
        if files and st.button("Import PGN"):
            names = importer.my_usernames([user])
            for file in files:
                stats = importer.import_pgn_text(conn, file.getvalue().decode("utf-8", errors="replace"), names)
                st.write(f"{file.name}: {stats}")


PAGES = {
    "import": st.Page(page_import, title="1 · Import & analyze", icon="⬇", default=True),
    "report": st.Page(page_report, title="2 · Leak report", icon="📊", url_path="report"),
    "trainer": st.Page(page_trainer, title="3 · Trainer", icon="♟", url_path="trainer"),
    "quiet": st.Page(page_quiet, title="Find the quiet move", icon="🤫", url_path="quiet"),
    "calibration": st.Page(page_calibration, title="Calibration", icon="🎯", url_path="calibration"),
}
st.navigation({"Workflow": [PAGES["import"], PAGES["report"], PAGES["trainer"]],
               "Extras": [PAGES["quiet"], PAGES["calibration"]]}).run()

st.sidebar.markdown("> *I don't believe in psychology. I believe in good moves.*\n>\n> — Bobby Fischer")
