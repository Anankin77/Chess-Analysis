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

import chesscoach


def _reload_package_if_updated() -> None:
    """A hosted app pulls new code and re-runs this file, but keeps the old
    `chesscoach` modules in memory. Reload them whenever their files changed."""
    import importlib
    import sys
    stamp = max(f.stat().st_mtime for f in Path(chesscoach.__file__).parent.glob("*.py"))
    if getattr(chesscoach, "_loaded_stamp", None) != stamp:
        for name in ("config", "models", "db", "book", "importer", "engine", "leaks", "trainer",
                     "report", "review", "runner", "theme"):
            module = sys.modules.get(f"chesscoach.{name}")
            if module is not None:
                importlib.reload(module)
        chesscoach._loaded_stamp = stamp


_reload_package_if_updated()
from chesscoach import config, db, importer, leaks, report, review, runner, theme, trainer  # noqa: E402

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


def lan_ip() -> str:
    """This computer's address on the local network (no packets are sent)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
    except OSError:
        return "YOUR-COMPUTER-IP"


STEPS = [("", "Import", "pick dates & formats"), ("#analyze", "Analyze", "one click, runs in background"),
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

@st.fragment
def leak_browser(lk: pd.DataFrame) -> None:
    """Pick any flagged move and replay it on the board (reruns only this block)."""
    conn = get_conn()
    order = st.radio("Order", ["Most costly", "Newest"], horizontal=True, label_visibility="collapsed")
    view = lk.sort_values("win_drop" if order == "Most costly" else "date", ascending=False).head(300)
    table = pd.DataFrame({
        "Date": view["date"],
        "Opponent": view["black"].where(view["my_color"] == "white", view["white"]),
        "Leak": view["label"],
        "Played": [move_label(f, m) for f, m in zip(view["fen_before"], view["my_move"])],
        "Best": [san(f, m) for f, m in zip(view["fen_before"], view["best_move"])],
        "Win % lost": view["win_drop"].round(0),
        "Phase": view["phase"],
        "Format": view["tc_class"],
        "Time trouble": view["time_trouble"],
    })
    left, right = st.columns([3, 2])
    with left:
        event = st.dataframe(table, hide_index=True, width="stretch", height=420,
                             on_select="rerun", selection_mode="single-row", key="leak_table")
        st.caption(f"Showing {len(view)} of {len(lk)} leaks. Select a row to replay it.")
    rows = event.selection.rows if event and event.selection else []
    chosen = view.iloc[rows[0] if rows else 0]
    with right:
        payload = trainer.puzzle_payload(conn, chosen["game_id"], int(chosen["ply"]))
        if payload:
            _board_component(puzzle_id=f"view-{chosen['id']}", mode="view", pieces=chess.svg.PIECES,
                             prompt=f"{chosen['label']} · −{chosen['win_drop']:.0f} win %. Replay the lines below.",
                             key="leak_viewer", default=None, **payload)
            for line in explain_leak(conn, chosen["game_id"], int(chosen["ply"])):
                st.markdown(line)
            if chosen["url"]:
                st.caption(f"[Open the game on Chess.com]({chosen['url']})")



def page_report() -> None:
    step_bar(2)
    st.title("Leak report")
    all_leaks, all_moves = load_frames(data_version())
    if all_moves.empty:
        st.info("No analyzed games yet. Start the analysis on the Import page; results appear here while it runs.")
        st.page_link(PAGES["import"], label="Go to Import & analyze →", icon="⬇")
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

    notes = report.headlines(lk, moves)
    if notes:
        st.subheader("What the numbers say")
        with st.container(border=True):
            for note in notes:
                st.markdown("- " + note)

    st.subheader("Top 3 leak patterns")
    for rank, pat in enumerate(report.top_patterns(lk).itertuples(), start=1):
        st.markdown(f"**{rank}. {pat.label} · {pat.phase}** — {pat.count} leaks, "
                    f"average loss {pat.avg_drop:.0f} win %")
        examples = (lk[(lk["label"] == pat.label) & (lk["phase"] == pat.phase)]
                    .sort_values("win_drop", ascending=False).head(3))
        for col, ex in zip(st.columns(3), examples.itertuples()):
            with col:
                st.image(board_svg(ex.fen_before, ex.my_move, ex.best_move), width="stretch")
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
    right.dataframe(by_type, width="stretch")

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
            b.dataframe(table, width="stretch")
            if note:
                st.caption(note)

    st.subheader("Monthly trend")
    trend = report.monthly_trend(lk, moves)
    if len(trend) >= 2:
        st.line_chart(trend, y_label="Leaks per 100 moves", x_label="")
    else:
        st.caption("Needs analyzed games from at least two months.")
        st.dataframe(trend)

    st.subheader("Browse every leak")
    leak_browser(lk)


# =============================================================== Pages 2 + 3: training

def explain_leak(conn, game_id: str, ply: int) -> list[str]:
    """Why this position is on a card, in one line per leak (templated from the stored numbers)."""
    out = []
    for lk in conn.execute("SELECT * FROM leaks WHERE game_id=? AND ply=? ORDER BY leak_type", (game_id, ply)):
        fen = lk["fen_before"]
        board, move = chess.Board(fen), chess.Move.from_uci(lk["my_move"])
        mine, best = san(fen, lk["my_move"]), san(fen, lk["best_move"])
        if lk["leak_type"] == "forcing_bias":
            kind = ("a capture" if board.is_capture(move) else "a check" if board.gives_check(move) else "a threat")
            out.append(f"**Forcing bias** · you played {kind} ({mine}); the position needed the quiet **{best}**.")
        elif lk["sub_type"] == "positional":
            out.append(f"**Horizon (positional)** · {mine} holds at depth {config.SHALLOW_DEPTH} but drifts "
                       f"{lk['win_drop']:.0f} win % deeper, with no single tactic. Compare the lines move by move.")
        else:
            what = "you get mated" if lk["sub_type"] == "mate" else "material goes"
            out.append(f"**Horizon ({lk['sub_type']})** · {mine} looked fine at depth {config.SHALLOW_DEPTH}; "
                       f"{what} on ply {lk['refutation_ply']} of the reply. Replay the game line with ▶ and "
                       "find the move you stopped calculating at.")
    return out


def training_header(conn) -> None:
    stats = trainer.training_stats(conn)
    names = {"forcing_bias": "Quiet-move accuracy (30 d)", "horizon": "Horizon accuracy (30 d)"}
    cols = st.columns(4)
    acc = f"{100 * stats['today_correct'] / stats['today']:.0f}%" if stats["today"] else "–"
    cols[0].metric("Solved today", f"{stats['today']} ({acc})" if stats["today"] else 0)
    cols[1].metric("Day streak", stats["streak"])
    for col, key in zip(cols[2:], names):
        right, total = stats["by_type"].get(key, (0, 0))
        col.metric(names[key], f"{100 * right / total:.0f}% ({right}/{total})" if total else "–")


def _training_ui(mode: str, leak_type: str | None, prompt: str, allow_ahead_default: bool) -> None:
    conn = get_conn()
    training_header(conn)
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
            st.caption("Make your move on the board (tap or drag). Afterwards you can replay every line.")
            if st.button("Skip", key=f"{mode}_skip"):
                ids = list(by_id)
                st.session_state[state_key] = ids[(ids.index(card["id"]) + 1) % len(ids)]
                st.rerun(scope="fragment")
            return
        correct = bool(answer.get("correct"))
        st.markdown(("✅ **Correct.**" if correct else "❌ **Incorrect.**") +
                    f" This cost {card['win_drop']:.0f} win % in the game.")
        for line in explain_leak(conn, card["game_id"], card["ply"]):
            st.markdown(line)
        game = conn.execute("SELECT url, white, black, date FROM games WHERE id=?", (card["game_id"],)).fetchone()
        st.caption(f"{game['white']} – {game['black']}, {game['date']}" +
                   (f" · [open game]({game['url']})" if game["url"] else ""))
        st.write("How did it go?")
        cols = st.columns(4)
        for col, (name, grade) in zip(cols, trainer.GRADES.items()):
            # A wrong answer can only be graded Again or Hard.
            if col.button(name, key=f"{mode}_grade_{grade}", width="stretch",
                          disabled=(not correct and grade > 3),
                          type="primary" if (grade == 4 if correct else grade == 1) else "secondary"):
                new = trainer.review(conn, card["id"], grade, correct=correct)
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


# =============================================================== Game review (one game, start to end)

GRADE_ICONS = {"Best": "★", "Good": "✓", "Inaccuracy": "?!", "Mistake": "?", "Blunder": "??"}


@st.fragment
def game_moments(game_id: str) -> None:
    """Move list on the left, replay board and coach comment on the right."""
    conn = get_conn()
    data = review.game_review(conn, game_id)
    moves = data["moves"]
    key_only = st.radio("Show", ["Key moments", "All my moves"], horizontal=True,
                        label_visibility="collapsed", key="game_show") == "Key moments"
    shown = [m for m in moves if m["grade"] in ("Inaccuracy", "Mistake", "Blunder")
             or "Only" in m["comment"] or "only move" in m["comment"]] if key_only else moves
    if not shown:
        st.success("No key moments: nothing lost 5 win % or more. Switch to 'All my moves' to browse.")
        return
    table = pd.DataFrame({
        "Move": [m["label"] for m in shown],
        "Verdict": [f"{GRADE_ICONS[m['grade']]} {m['grade']}" for m in shown],
        "Win % lost": [m["drop"] for m in shown],
        "Best": [m["best_san"] for m in shown],
        "Best is": [m["best_kind"] for m in shown],
        "Tags": [", ".join(m["leaks"] + (["time trouble"] if m["time_trouble"] == "yes" else [])) for m in shown],
    })
    left, right = st.columns([3, 2])
    with left:
        event = st.dataframe(table, hide_index=True, width="stretch", height=min(460, 38 * len(shown) + 40),
                             on_select="rerun", selection_mode="single-row", key=f"moves_{game_id}_{key_only}")
        st.caption("Select a move to see it on the board.")
    rows = event.selection.rows if event and event.selection else []
    default = max(range(len(shown)), key=lambda i: shown[i]["drop"])
    m = shown[rows[0] if rows else default]
    with right:
        payload = trainer.puzzle_payload(conn, game_id, m["ply"])
        if payload:
            _board_component(puzzle_id=f"game-{game_id}-{m['ply']}", mode="view", pieces=chess.svg.PIECES,
                             prompt=f"{m['label']} · {m['grade']}" + (f" · −{m['drop']:.0f} win %" if m["drop"] >= 1 else ""),
                             key="game_viewer", default=None, **payload)
        st.markdown(m["comment"])
        if m["drop"] < 5 and m["plan"]:
            st.caption(f"Engine's plan from here: {m['plan']}.")


def _paste_pgn() -> None:
    """Button callback: store the pasted game and select it for review."""
    ss = st.session_state
    text = (ss.get("paste_pgn") or "").strip()
    if not text:
        ss["paste_msg"] = ("error", "Paste a PGN first.")
        return
    side = {"White": "white", "Black": "black"}.get(ss.get("paste_side"))
    stats = importer.import_pgn_text(get_conn(), text, importer.my_usernames(), force_color=side)
    if stats.ids:
        ss["game_fmt"], ss["game_pick"], ss["paste_pgn"] = "any", stats.ids[-1], ""
        extra = f" ({len(stats.ids)} games in the paste; showing the last)" if len(stats.ids) > 1 else ""
        ss["paste_msg"] = ("ok", ("Game added." if stats.imported else "That game was already stored.") + extra)
    elif stats.aborted:
        ss["paste_msg"] = ("error", "That PGN has no moves.")
    else:
        why = stats.problems[0] if stats.problems else "no game found in the text"
        hint = " Choose the side you played and try again." if "is playing" in why else ""
        ss["paste_msg"] = ("error", f"Could not read it: {why}.{hint}")


def page_game() -> None:
    conn = get_conn()
    st.title("Game review")
    st.caption("One game from start to end: every decision of yours graded, the engine's plan in words, "
               "your mistakes, and the moments that called for a quiet move.")

    c1, c2, c3 = st.columns([2, 2, 3])
    fmt = c1.selectbox("Format", ["rapid", "blitz", "bullet", "any"], key="game_fmt")
    if c2.button("⬇ Fetch my newest games", width="stretch",
                 help="Downloads the last 35 days from Chess.com (all formats except daily)."):
        today = date.today()
        with st.spinner("Asking Chess.com…"):
            try:
                stats = importer.import_chesscom(conn, config.CHESSCOM_USERNAME, (today - timedelta(days=35)).isoformat(),
                                                 today.isoformat(), ["rapid", "blitz", "bullet"])
                st.toast(f"{stats.imported} new games")
            except Exception as exc:
                st.error(f"Chess.com import failed: {exc}")
    with st.expander("📋 Paste a PGN", expanded=bool(st.session_state.get("paste_pgn"))):
        st.text_area("PGN", key="paste_pgn", height=160, label_visibility="collapsed",
                     placeholder="Paste the PGN here (Chess.com: Share → PGN → copy)")
        a, b = st.columns([2, 1])
        a.radio("I played", ["Detect from username", "White", "Black"], horizontal=True, key="paste_side",
                help=f"Detection looks for {config.CHESSCOM_USERNAME} in the White/Black tags.")
        b.button("Add this game", type="primary", width="stretch", on_click=_paste_pgn)
    kind, msg = st.session_state.pop("paste_msg", (None, None))
    if kind:
        (st.success if kind == "ok" else st.error)(msg)

    sql = "SELECT * FROM games WHERE tc_class != 'daily'" + ("" if fmt == "any" else " AND tc_class = ?")
    games = conn.execute(sql + " ORDER BY date DESC, CAST(SUBSTR(id, INSTR(id, ':') + 6) AS INTEGER) DESC, id DESC LIMIT 80",
                         [] if fmt == "any" else [fmt]).fetchall()
    if not games and not st.session_state.get("game_pick"):
        st.info("No stored games of this format yet. Fetch your newest games or paste a PGN above.")
        return

    def game_label(g) -> str:
        opp = g["black"] if g["my_color"] == "white" else g["white"]
        return f"{g['date']} · {g['tc_class']} · {g['my_color']} vs {opp} · {g['result']}"

    by_id = {g["id"]: g for g in games}
    picked = st.session_state.get("game_pick")
    if picked and picked not in by_id:               # e.g. a pasted game without date or time control
        row = conn.execute("SELECT * FROM games WHERE id=?", (picked,)).fetchone()
        if row and (fmt == "any" or row["tc_class"] == fmt):
            by_id = {picked: row, **by_id}
        else:
            st.session_state.pop("game_pick", None)
    gid = st.selectbox("Game", list(by_id), format_func=lambda i: game_label(by_id[i]), key="game_pick")
    game = by_id[gid]

    if game["analysis_status"] != "done":
        st.write("This game is not analyzed yet. It takes about a minute.")
        if st.button("▶ Analyze this game now", type="primary"):
            bar = st.progress(0.0, text="Starting Stockfish…")
            try:
                n = review.analyze_now(conn, gid, lambda done, total: bar.progress(
                    min(1.0, done / max(1, total)), text=f"Move {done // 2 + 1} of {total // 2 + 1}"))
                bar.empty()
                st.toast(f"Analyzed {n} of your moves")
                st.rerun()
            except FileNotFoundError as exc:
                bar.empty()
                st.error(str(exc))
            except Exception as exc:
                bar.empty()
                st.error(f"Analysis failed: {exc}")
        return

    data = review.game_review(conn, gid)
    s = data["summary"]
    st.markdown(f"**{game['white']} – {game['black']}** · {game['opening'] or 'Unknown opening'} · "
                f"{game['time_control']} · you were {game['my_color']} · **{game['result']}**"
                + (f" · [open on Chess.com]({game['url']})" if game["url"] else ""))
    if not data["moves"]:
        st.info("Every move of yours was opening theory: nothing to review.")
        return
    cols = st.columns(4)
    cols[0].metric("Avg win % lost / move", s["avg_loss"])
    cols[1].metric("Mistakes + blunders", s["counts"]["Mistake"] + s["counts"]["Blunder"])
    cols[2].metric("Quiet moves found", f"{s['quiet_found']}/{s['quiet_needed']}" if s["quiet_needed"] else "–")
    cols[3].metric("Leaks flagged", s["leaks"])
    st.caption(f"{s['counts']['Inaccuracy']} inaccuracies · {s['counts']['Best']} best moves · "
               f"opening book until move {s['book_plies'] // 2 + 1} · {s['analyzed']} moves analyzed")

    st.subheader("Lessons from this game")
    with st.container(border=True):
        for note in review.lessons(data):
            st.markdown("- " + note)

    st.subheader("Your winning chances")
    chart = pd.DataFrame({"Best available": [m["best_win"] for m in data["moves"]],
                          "After your move": [m["my_win"] for m in data["moves"]]},
                         index=pd.Index([m["move_no"] for m in data["moves"]], name="Move"))
    st.line_chart(chart, y_label="Win %", x_label="Move", height=260, color=["#81b64c", "#d9a441"])
    st.caption("The gap between the two lines is what each move gave away.")

    st.subheader("Move by move")
    game_moments(gid)


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
    st.dataframe(table, hide_index=True, width="stretch",
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
    board_col.image(board_svg(fen, leak["my_move"], leak["best_move"], size=420), width="stretch")
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
        st.dataframe(pd.DataFrame(lines), hide_index=True, width="stretch")
        st.caption(f"{game['white']} – {game['black']} · {game['date']} · {game['tc_class']}" +
                   (f" · [open game]({game['url']})" if game["url"] else "") +
                   (f" · already labelled: {existing['verdict']}" if existing else ""))

        def label(verdict: str) -> None:
            db.save_label(conn, leak["game_id"], leak["ply"], leak["leak_type"], verdict)
            st.session_state["calib_idx"] = idx + 1

        v, f, s = st.columns(3)
        v.button("Valid", type="primary", width="stretch", on_click=label, args=("valid",))
        f.button("False positive", width="stretch", on_click=label, args=("false_positive",))
        if s.button("Skip", width="stretch"):
            st.session_state["calib_idx"] = idx + 1
            st.rerun()


# =============================================================== Import & status

@st.fragment(run_every=5)
def analysis_progress() -> None:
    """Start / stop the batch and watch it; refreshes itself every 5 seconds."""
    d_from, d_to, formats = selection()
    done, total = selection_counts(d_from, d_to, formats)
    if total == 0:
        st.caption("No stored games in this selection yet. Import first.")
        return
    pid = runner.running_pid()
    tcs = [f for f in formats if f != "daily"]
    c1, c2, c3 = st.columns([2, 1, 3])
    workers = c2.number_input("Workers", 1, 64, runner.default_workers(), disabled=bool(pid),
                              help="Stockfish processes in parallel. Use about one per physical core.")
    if pid:
        if c1.button("■ Stop analysis", width="stretch"):
            runner.stop()
            st.toast("Stopping: finished games are saved, the rest resumes next time.")
    elif done < total:
        if c1.button("▶ Start analysis", type="primary", width="stretch"):
            try:
                runner.start(d_from.isoformat(), d_to.isoformat(), tcs, int(workers))
                st.toast("Analysis started in the background")
                st.rerun(scope="fragment")
            except FileNotFoundError as exc:
                st.error(str(exc))
    remaining = total - done
    hours = remaining * 40 / max(1, int(workers)) / 3600
    c3.caption(("**Running.** " if pid else "") +
               (f"{remaining} games left, roughly {hours:.1f} h with {int(workers)} workers. You can close this "
                "page; stop and resume any time." if remaining else "Everything in this selection is analyzed."))
    leaks_found = get_conn().execute("SELECT COUNT(*) FROM leaks").fetchone()[0]
    st.progress(done / total, text=f"Analyzed {done} of {total} games in the selection · {leaks_found} leaks found")
    if not pid and done < total:
        tail = runner.log_tail()
        if tail and ("Error" in tail or "Traceback" in tail or "not found" in tail):
            st.error("The last run stopped with a problem:")
            st.code(tail)
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
    clicked = b.button("Import games", type="primary", disabled=not ready, width="stretch")
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
    analysis_progress()
    with st.expander("Run it in a terminal instead"):
        st.write("Same thing from the project folder with `.venv` active:")
        st.code(batch_command(d_from, d_to, formats), language="bash")
    with st.expander("Use chess-coach on your phone"):
        st.write("Start the app so other devices on your Wi-Fi can reach it:")
        st.code("streamlit run app.py --server.address 0.0.0.0", language="bash")
        st.write(f"Then open **http://{lan_ip()}:8501** on the phone (same Wi-Fi as this computer).")

    st.subheader("Stored games")
    stored = pd.read_sql_query(
        """SELECT tc_class AS "Time format", COUNT(*) AS Games, MIN(NULLIF(date, '')) AS "First", MAX(date) AS "Last",
                  SUM(analysis_status = 'done') AS Analyzed
           FROM games GROUP BY tc_class ORDER BY Games DESC""", conn)
    if stored.empty:
        st.caption("Nothing stored yet.")
    else:
        st.dataframe(stored, hide_index=True, width="stretch")
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

    with st.expander("Paste or upload PGN instead"):
        pasted = st.text_area("Paste PGN (one or many games)", height=140, key="import_paste")
        if st.button("Import pasted PGN", disabled=not pasted.strip()):
            stats = importer.import_pgn_text(conn, pasted, importer.my_usernames([user]))
            st.write(f"Pasted text: {stats}")
            if stats.problems:
                st.caption("First problem: " + stats.problems[0])
        files = st.file_uploader("PGN files", type=["pgn"], accept_multiple_files=True)
        if files and st.button("Import PGN"):
            names = importer.my_usernames([user])
            for file in files:
                stats = importer.import_pgn_text(conn, file.getvalue().decode("utf-8", errors="replace"), names)
                st.write(f"{file.name}: {stats}")


    with st.expander("Backup and restore (do this before a hosted app goes to sleep)"):
        st.write("Everything (games, analysis, cards, training history) is one file. A hosted copy of the app "
                 "loses it when the server restarts, so download it after a session and restore it next time.")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if config.DB_PATH.exists():
            st.download_button("⬇ Download my data", config.DB_PATH.read_bytes(),
                               file_name=f"chesscoach-{date.today()}.db", mime="application/octet-stream")
        backup = st.file_uploader("Restore from a backup file", type=["db"], key="restore_file")
        sure = st.checkbox("Replace the current data with this backup", key="restore_sure")
        if backup and st.button("Restore", disabled=not sure):
            data = backup.getvalue()
            if not data.startswith(b"SQLite format 3"):
                st.error("That is not a chess-coach backup file.")
            else:
                conn.close()
                for suffix in ("", "-wal", "-shm"):
                    Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
                config.DB_PATH.write_bytes(data)
                get_conn.clear()
                st.cache_data.clear()
                st.session_state.pop("restore_sure", None)
                st.toast("Backup restored")
                st.rerun()


PAGES = {
    "import": st.Page(page_import, title="1 · Import & analyze", icon="⬇", default=True),
    "game": st.Page(page_game, title="Game review", icon="🔍", url_path="game"),
    "report": st.Page(page_report, title="2 · Leak report", icon="📊", url_path="report"),
    "trainer": st.Page(page_trainer, title="3 · Trainer", icon="♟", url_path="trainer"),
    "quiet": st.Page(page_quiet, title="Find the quiet move", icon="🤫", url_path="quiet"),
    "calibration": st.Page(page_calibration, title="Calibration", icon="🎯", url_path="calibration"),
}
st.navigation({"Workflow": [PAGES["import"], PAGES["report"], PAGES["trainer"]],
               "One game": [PAGES["game"]],
               "Extras": [PAGES["quiet"], PAGES["calibration"]]}).run()

st.sidebar.markdown("> *I don't believe in psychology. I believe in good moves.*\n>\n> — Bobby Fischer")
