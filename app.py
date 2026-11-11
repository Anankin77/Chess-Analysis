"""chess-coach Streamlit app.   Run:  streamlit run app.py"""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path

import chess
import chess.svg
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from chesscoach import config, db, importer, leaks, report, trainer

st.set_page_config(page_title="chess-coach", page_icon="♟", layout="wide")

_board_component = components.declare_component(
    "chesscoach_board", path=str(Path(__file__).parent / "chesscoach" / "board_component"))

RED, GREEN = "#c62828cc", "#2e7d32cc"


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
    return chess.svg.board(board, arrows=arrows, orientation=board.turn, size=size)


def san(fen: str, uci: str) -> str:
    try:
        return chess.Board(fen).san(chess.Move.from_uci(uci))
    except (ValueError, AssertionError):
        return uci


def move_label(fen: str, uci: str) -> str:
    board = chess.Board(fen)
    dots = "." if board.turn == chess.WHITE else "…"
    return f"{board.fullmove_number}{dots}{san(fen, uci)}"


def batch_hint() -> None:
    st.code("python -m chesscoach.engine --batch --workers 4", language="bash")


# =============================================================== Page 1: Leak report

def page_report() -> None:
    st.title("Leak report")
    all_leaks, all_moves = load_frames(data_version())
    if all_moves.empty:
        st.info("No analyzed games yet. Import games on the **Import** page, then run the overnight batch:")
        batch_hint()
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

def page_import() -> None:
    conn = get_conn()
    st.title("Import")
    counts = db.status_counts(conn)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Games", counts["games"])
    c2.metric("Analyzed", counts["by_status"].get("done", 0))
    pending = conn.execute(
        f"SELECT COUNT(*) FROM games WHERE analysis_status='pending' AND tc_class IN "
        f"({','.join('?' * len(config.ANALYZE_TIME_CONTROLS))})", config.ANALYZE_TIME_CONTROLS).fetchone()[0]
    c3.metric("Waiting for analysis", pending)
    c4.metric("Leaks found", counts["leaks"])
    if counts["by_tc"]:
        st.caption("By time control: " + ", ".join(f"{k} {v}" for k, v in sorted(counts["by_tc"].items()))
                   + f". Analyzed by default: {', '.join(config.ANALYZE_TIME_CONTROLS)} (daily never).")

    st.subheader("Chess.com")
    cc_user = st.text_input("Chess.com username", config.CHESSCOM_USERNAME)
    if st.button("Fetch new Chess.com games", disabled=not cc_user.strip()):
        with st.status("Downloading monthly archives…") as status:
            try:
                stats = importer.import_chesscom(conn, cc_user, progress=status.write)
                status.update(label=f"Chess.com: {stats}", state="complete")
            except Exception as exc:
                status.update(label=f"Chess.com import failed: {exc}", state="error")

    st.subheader("Lichess")
    li_user = st.text_input("Lichess username", config.LICHESS_USERNAME,
                            help="Default comes from CHESSCOACH_LICHESS_USERNAME or config.py")
    if st.button("Fetch new Lichess games", disabled=not li_user.strip()):
        with st.status("Downloading from Lichess (waits and retries on rate limits)…") as status:
            try:
                stats = importer.import_lichess(conn, li_user, progress=status.write)
                status.update(label=f"Lichess: {stats}", state="complete")
            except Exception as exc:
                status.update(label=f"Lichess import failed: {exc}", state="error")

    st.subheader("PGN upload")
    files = st.file_uploader("PGN files", type=["pgn"], accept_multiple_files=True)
    if files and st.button("Import PGN"):
        names = importer.my_usernames([cc_user, li_user])
        for f in files:
            stats = importer.import_pgn_text(conn, f.getvalue().decode("utf-8", errors="replace"), names)
            st.write(f"{f.name}: {stats}")
        st.caption("Skipped games (corrupt, variants, or none of your usernames playing) are listed in "
                   f"`{config.LOG_PATH}`.")

    st.subheader("Analyze")
    st.write("Analysis runs outside the app. Start it in a terminal (it resumes where it stopped):")
    batch_hint()


st.navigation([
    st.Page(page_report, title="Leak report", icon="📊", default=True),
    st.Page(page_trainer, title="Trainer", icon="♟", url_path="trainer"),
    st.Page(page_quiet, title="Find the quiet move", icon="🤫", url_path="quiet"),
    st.Page(page_calibration, title="Calibration", icon="🎯", url_path="calibration"),
    st.Page(page_import, title="Import", icon="⬇", url_path="import"),
]).run()
