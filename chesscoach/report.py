"""Data frames behind the leak report (pure pandas, no Streamlit)."""

from __future__ import annotations

import sqlite3

import pandas as pd

from . import config

LEAK_LABELS = {
    "forcing_bias": "Forcing bias",
    "horizon:material": "Horizon (material)",
    "horizon:mate": "Horizon (mate)",
    "horizon:positional": "Horizon (positional)",
}


def opening_family(name: str) -> str:
    return (name or "Unknown").split(":")[0].strip() or "Unknown"


def _phase_from_fen(fen: pd.Series) -> pd.Series:
    """Vectorised twin of leaks.game_phase (non-pawn material, max 62)."""
    placement = fen.str.split(" ").str[0].str.lower()
    npm = (placement.str.count("q") * 9 + placement.str.count("r") * 5
           + placement.str.count("b") * 3 + placement.str.count("n") * 3)
    fullmove = pd.to_numeric(fen.str.split(" ").str[-1], errors="coerce").fillna(99)
    phase = pd.Series("middlegame", index=fen.index)
    phase[npm <= config.ENDGAME_MAX_MATERIAL] = "endgame"
    phase[(fullmove <= config.OPENING_MAX_FULLMOVE) & (npm >= config.OPENING_MIN_MATERIAL)] = "opening"
    return phase


def load_moves(conn: sqlite3.Connection) -> pd.DataFrame:
    """Every analyzed move of mine with game context: the denominator for leak rates."""
    df = pd.read_sql_query(
        """SELECT a.game_id, a.ply, a.fen_before, a.win_drop,
                  g.tc_class, g.my_color, g.opening, g.result, g.date, g.url,
                  g.base_seconds, g.increment_seconds,
                  prev.clock_seconds AS prev_clock, own.clock_seconds AS own_clock
           FROM analysis a
           JOIN games g ON g.id = a.game_id
           LEFT JOIN moves prev ON prev.game_id = a.game_id AND prev.ply = a.ply - 2
           LEFT JOIN moves own ON own.game_id = a.game_id AND own.ply = a.ply""", conn)
    if df.empty:
        return df
    df["opening_family"] = df["opening"].map(opening_family)
    df["month"] = df["date"].fillna("").str[:7].replace("", "unknown")
    df["phase"] = _phase_from_fen(df["fen_before"])
    # Same rule as leaks.decision_clock: previous clock + increment, base time on move one.
    inc = df["increment_seconds"].fillna(0)
    clock = (df["prev_clock"] + inc).where(df["prev_clock"].notna(), df["own_clock"])
    first = (df["ply"] < 2) & df["own_clock"].notna() & df["base_seconds"].notna()
    clock = clock.where(~first, df["base_seconds"])
    budget = df["base_seconds"] + config.TIME_TROUBLE_MOVES * inc
    df["time_trouble"] = "unknown"
    known = clock.notna() & df["base_seconds"].notna()
    df.loc[known, "time_trouble"] = (clock < config.TIME_TROUBLE_RATIO * budget)[known].map({True: "yes", False: "no"})
    return df.drop(columns=["prev_clock", "own_clock"])


def load_leaks(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """SELECT l.*, g.tc_class, g.my_color, g.opening, g.result, g.date, g.url, g.white, g.black
           FROM leaks l JOIN games g ON g.id = l.game_id""", conn)
    if df.empty:
        return df
    key = df["leak_type"] + df["sub_type"].map(lambda s: f":{s}" if isinstance(s, str) and s else "")
    df["label"] = key.map(LEAK_LABELS).fillna(key)
    df["opening_family"] = df["opening"].map(opening_family)
    df["month"] = df["date"].fillna("").str[:7].replace("", "unknown")
    return df


def apply_filters(df: pd.DataFrame, tcs=None, colours=None, openings=None, results=None,
                  date_from=None, date_to=None) -> pd.DataFrame:
    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    for column, allowed in (("tc_class", tcs), ("my_color", colours),
                            ("opening_family", openings), ("result", results)):
        if allowed:
            mask &= df[column].isin(allowed)
    if date_from:
        mask &= df["date"].fillna("") >= str(date_from)
    if date_to:
        mask &= df["date"].fillna("") <= str(date_to)
    return df[mask]


def breakdown(leaks: pd.DataFrame, moves: pd.DataFrame, by: str) -> pd.DataFrame:
    """Leak counts per label and leaks per 100 analyzed moves, grouped by `by`."""
    counts = leaks.pivot_table(index=by, columns="label", values="ply", aggfunc="count", fill_value=0)
    out = counts.copy()
    out["Leaks"] = counts.sum(axis=1)
    out["Moves analyzed"] = moves.groupby(by).size().reindex(out.index).fillna(0).astype(int)
    out["Leaks per 100 moves"] = (100 * out["Leaks"] / out["Moves analyzed"].where(out["Moves analyzed"] > 0)).round(2)
    return out.sort_index()


def top_patterns(leaks: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """Most frequent (leak label, phase) combinations."""
    if leaks.empty:
        return pd.DataFrame(columns=["label", "phase", "count", "avg_drop"])
    grouped = (leaks.groupby(["label", "phase"])
               .agg(count=("ply", "size"), avg_drop=("win_drop", "mean")).reset_index()
               .sort_values(["count", "avg_drop"], ascending=False))
    return grouped.head(n)


def monthly_trend(leaks: pd.DataFrame, moves: pd.DataFrame) -> pd.DataFrame:
    """Leaks per 100 analyzed moves per month, one column per leak type."""
    if leaks.empty or moves.empty:
        return pd.DataFrame()
    denom = moves.groupby("month").size()
    kind = leaks["leak_type"].map({"forcing_bias": "Forcing bias", "horizon": "Horizon"})
    counts = leaks.assign(kind=kind).pivot_table(index="month", columns="kind", values="ply",
                                                 aggfunc="count", fill_value=0)
    counts = counts.reindex(denom.index, fill_value=0)
    return (100 * counts.div(denom, axis=0)).round(2).drop(index="unknown", errors="ignore").sort_index()


def headlines(leaks: pd.DataFrame, moves: pd.DataFrame, min_moves: int = 60) -> list[str]:
    """Plain-number findings worth acting on (rule based, no text generation)."""
    out: list[str] = []
    if leaks.empty or moves.empty:
        return out
    games = moves["game_id"].nunique()
    by_label = leaks.groupby("label").agg(n=("ply", "size"), cost=("win_drop", "sum")).sort_values("cost", ascending=False)
    top = by_label.iloc[0]
    out.append(f"**Most expensive leak: {by_label.index[0]}** — {int(top.n)} of {len(leaks)} leaks, "
               f"{top.cost / games:.1f} win % lost per game on average.")

    def rate(df_l: pd.DataFrame, df_m: pd.DataFrame, column: str) -> pd.Series:
        m = df_m.groupby(column).size()
        m = m[m >= min_moves]
        return (100 * df_l.groupby(column).size().reindex(m.index, fill_value=0) / m).sort_values(ascending=False)

    tt = rate(leaks, moves, "time_trouble")
    if {"yes", "no"} <= set(tt.index) and tt["no"] > 0:
        ratio = tt["yes"] / tt["no"]
        if ratio >= 1.3:
            out.append(f"**Time trouble multiplies leaks ×{ratio:.1f}** — {tt['yes']:.1f} vs {tt['no']:.1f} per 100 moves.")
        elif ratio <= 0.8:
            out.append(f"**Leaks are not a clock problem** — {tt['no']:.1f} per 100 moves with time, "
                       f"{tt['yes']:.1f} in time trouble.")
    ph = rate(leaks, moves, "phase")
    if len(ph) >= 2 and ph.iloc[0] > 0:
        out.append(f"**Weakest phase: {ph.index[0]}** — {ph.iloc[0]:.1f} leaks per 100 moves "
                   f"(vs {ph.iloc[-1]:.1f} in the {ph.index[-1]}).")
    col = rate(leaks, moves, "my_color")
    if len(col) == 2 and col.iloc[1] > 0 and col.iloc[0] / col.iloc[1] >= 1.3:
        out.append(f"**More leaks as {col.index[0].capitalize()}** — {col.iloc[0]:.1f} vs {col.iloc[1]:.1f} per 100 moves.")
    months = moves[moves["month"] != "unknown"].groupby("month").size()
    months = months[months >= min_moves].sort_index()
    if len(months) >= 4:
        lm = leaks.groupby("month").size().reindex(months.index, fill_value=0)
        recent, before = months.index[-2:], months.index[:-2]
        r_new, r_old = 100 * lm[recent].sum() / months[recent].sum(), 100 * lm[before].sum() / months[before].sum()
        if r_old > 0:
            change = 100 * (r_new - r_old) / r_old
            word = "down" if change < 0 else "up"
            out.append(f"**Trend: {word} {abs(change):.0f}%** — {r_new:.2f} leaks per 100 moves in the last two "
                       f"months vs {r_old:.2f} before.")
    return out


def precision_table(conn: sqlite3.Connection) -> pd.DataFrame:
    """Calibration precision per leak type, over labels whose leak is still flagged."""
    df = pd.read_sql_query(
        """SELECT l.leak_type, l.sub_type, c.verdict FROM calibration_labels c
           JOIN leaks l ON l.game_id = c.game_id AND l.ply = c.ply AND l.leak_type = c.leak_type""", conn)
    rows = []
    for key, label in LEAK_LABELS.items():
        leak_type, _, sub = key.partition(":")
        part = df[(df["leak_type"] == leak_type) & ((df["sub_type"] == sub) if sub else True)] if not df.empty else df
        valid = int((part["verdict"] == "valid").sum()) if not part.empty else 0
        total = len(part)
        rows.append({"Leak type": label, "Labelled": total, "Valid": valid, "False positive": total - valid,
                     "Precision": round(100 * valid / total, 1) if total else None})
    return pd.DataFrame(rows)
