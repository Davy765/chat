"""Daily paper-trading report. NO broker connection; it places no orders.

Each evening, after the close:

    python scripts/download_etfs.py --out data/live --paper
    python scripts/download_rates.py --out data/live
    python scripts/paper_report.py

How the paper account works: it IS the backtest engine, run with fixed
rules from the start date in paper/config.json on the data available so
far. The engines are causal (tested), so rerunning each evening reproduces
every earlier day and adds one new one. Positions are what the rules hold;
orders are what they would place for the next session.

Each report is archived as paper/reports/<last session>.md and never
overwritten, so the real-time record can later be compared with a rerun.
A difference means the data was revised or something changed in the code.

Two versions run side by side on the same markets and capital:
  A. Modern Turtle rulebook (turtle_bot/portfolio.py), long/flat, cash account.
  B. Original Turtle S2, long-only, one equal sub-account per ETF.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.data import load_csv, load_rate_csv  # noqa: E402
from turtle_bot.indicators import wilder_atr  # noqa: E402
from turtle_bot.portfolio import ModernConfig, PortfolioBacktester  # noqa: E402


def money(v: float) -> str:
    return f"${v:,.0f}"


def px(v) -> str:
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:,.2f}"


def table(rows: list[dict]) -> str:
    if not rows:
        return "_none_\n"
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(out) + "\n"


def load_inputs(universe):
    live = ROOT / "data" / "live"
    src = live if all((live / f"{t}.csv").exists() for t in universe) else ROOT / "data"
    if src != live:
        print("WARNING: data/live/ is missing or incomplete, so this uses the FROZEN research snapshot. "
              "Refresh with: python scripts/download_etfs.py --out data/live --paper && "
              "python scripts/download_rates.py --out data/live\n")
    data = {t: load_csv(str(src / f"{t}.csv")) for t in universe}
    rate_path = src / "DTB3.csv" if (src / "DTB3.csv").exists() else ROOT / "data" / "DTB3.csv"
    return data, load_rate_csv(str(rate_path)), src


def modern_section(data, groups, rate, start, equity) -> tuple[str, dict]:
    res = PortfolioBacktester(ModernConfig(initial_equity=equity)).run(data, groups, rate, start)
    st = res.state
    lines = ["## A. Modern Turtle rulebook\n",
             f"Equity **{money(st['equity'])}** · peak {money(st['peak'])} · drawdown {st['drawdown']:.1%} · "
             f"sizing equity {money(st['sizing_equity'])} · cash {money(st['cash'])} · "
             f"invested {st['gross_leverage']:.0%} · heat {st['heat']:.2%}"
             + (f" · **new trades paused: {st['pause_sessions_left']} sessions left**" if st["pause_sessions_left"] else "")
             + "\n"]
    lines.append("### Orders for the next open (market orders at the open)\n")
    orders = []
    for o in sorted(res.pending_orders, key=lambda o: {"exit": 0, "add": 1, "entry": 2}[o["kind"]]):
        what = {"exit": "SELL all (close below 20-day low)", "add": "BUY add 1 unit",
                "entry": "BUY new position"}[o["kind"]]
        orders.append({"ticker": o["ticker"], "order": what,
                       "shares": "all" if o["kind"] == "exit" else o["shares"],
                       "then place GTC stop at": (f"fill − {3 * o['n']:.2f}" if o["kind"] == "entry"
                                                  else "see positions (shared stop)" if o["kind"] == "add" else "–")})
    lines.append(table(orders))
    lines.append("### Open positions and their GTC stop orders\n")
    pos = [{"ticker": p["ticker"], "since": p["entry_date"].date(), "units": p["units"], "shares": p["shares"],
            "avg entry": px(p["avg_entry"]), "last close": px(p["last_close"]),
            "GTC sell stop": px(p["stop"]),
            "add if close ≥": px(p["add_if_close_at_or_above"]),
            "unrealized": money(p["unrealized_pnl"])} for p in res.open_positions]
    lines.append(table(pos))
    lines.append("### Watchlist: closest to a new entry (trend filter passed, not held)\n")
    watch = []
    for s in res.snapshot:
        if s["in_position"] or not s["trend_ok"] or not s["n"] or math.isnan(s["n"]):
            continue
        gap_n = (s["entry_level_next"] - s["close"]) / s["n"]
        watch.append((gap_n, {"ticker": s["ticker"], "close": px(s["close"]),
                              "entry if a close is above": px(s["entry_level_next"]),
                              "distance": f"{gap_n:.1f} N"}))
    lines.append(table([w for _, w in sorted(watch, key=lambda x: x[0])[:6]]))
    recent = [t for t in res.trades if t.exit_date >= res.equity.index[-1] - pd.Timedelta(days=7)]
    if recent:
        lines.append("### Closed in the last 7 days\n")
        lines.append(table([{"ticker": t.ticker, "entry": t.entry_date.date(), "exit": t.exit_date.date(),
                             "reason": t.exit_reason, "P&L": money(t.pnl), "R": f"{t.r_multiple:.2f}"}
                            for t in recent]))
    return "\n".join(lines), {"version": "modern", "equity": st["equity"], "positions": len(res.open_positions),
                              "orders": len(res.pending_orders), "trades_closed": len(res.trades)}


def s2_section(data, rate, start, equity) -> tuple[str, dict]:
    per = equity / len(data)
    rows_pos, rows_orders, total, n_pos, closed = [], [], 0.0, 0, 0
    bt = TurtleBacktester(TurtleConfig(system="S2", allow_short=False, initial_equity=per))
    for t, df in data.items():
        df = df[df["Volume"] > 0]
        # S2 uses resting stop orders, so the first session that can fill is
        # the one AFTER the paper start (whose close set the first levels).
        after = df.index[df.index > pd.Timestamp(start)]
        if len(after):
            res = bt.run(df, cash_rate=rate, start=str(after[0].date()))
            value = float(res.equity.iloc[-1])
            eq = res.equity
        else:
            res, value, eq = None, per, pd.Series([per], index=[df.index[-1]])
        total += value
        closed += len(res.trades) if res else 0
        n = float(wilder_atr(df, 20).iloc[-1])
        hi55 = float(df["High"].iloc[-55:].max())
        lo20 = float(df["Low"].iloc[-20:].min())
        prior_year = eq[eq.index.year < eq.index[-1].year]
        ref = float(prior_year.iloc[-1]) if len(prior_year) else per
        unit = math.floor(0.01 * bt._sizing_equity(value, ref) / n)
        p = res.open_position if res else None
        if p is None:
            rows_orders.append({"ticker": t, "order": "BUY STOP (new position)", "price": px(hi55),
                                "shares": min(unit, math.floor(value / hi55)),
                                "after a fill": f"sell stop at fill − {2 * n:.2f}"})
            continue
        n_pos += 1
        sell_level = max(p["stop"], lo20)
        rows_pos.append({"ticker": t, "since": p["entry_date"].date(), "units": p["units"],
                         "shares": int(p["shares"]), "avg entry": px(p["avg_entry"]),
                         "last close": px(float(df["Close"].iloc[-1])),
                         "unrealized": money(p["unrealized_pnl"])})
        rows_orders.append({"ticker": t, "order": "SELL STOP (all shares)", "price": px(sell_level),
                            "shares": int(p["shares"]),
                            "after a fill": "position closed (" + ("2N stop" if sell_level == p["stop"]
                                                                   else "20-day low") + ")"})
        if p["next_add"] is not None:
            room = math.floor(max(value - p["shares"] * p["next_add"], 0) / p["next_add"])
            add = min(int(p["unit_shares"]), room)
            if add > 0:
                rows_orders.append({"ticker": t, "order": "BUY STOP (add 1 unit)", "price": px(p["next_add"]),
                                    "shares": add,
                                    "after a fill": f"move all units' stop to fill − {2 * p['n']:.2f}"})
            else:
                rows_orders.append({"ticker": t, "order": "no add (sub-account fully invested)",
                                    "price": "–", "shares": 0, "after a fill": "–"})
    lines = ["## B. Original Turtle S2, long-only (one sub-account per ETF)\n",
             f"Equity **{money(total)}** across {len(data)} sub-accounts of {money(per)} each at the start.\n",
             "### Resting orders for the next session (stop orders, good for that day only)\n",
             table(rows_orders),
             "### Open positions\n", table(rows_pos)]
    return "\n".join(lines), {"version": "s2_basket", "equity": total, "positions": n_pos,
                              "orders": len(rows_orders), "trades_closed": closed}


def _next_session_already_opened(last: pd.Timestamp) -> bool:
    """True if the next weekday after ``last`` has reached 09:30 New York
    time (exchange holidays are ignored, which can only flag later)."""
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("America/New_York"))
    nxt = last.date() + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    return now.date() > nxt or (now.date() == nxt and (now.hour, now.minute) >= (9, 30))


def main() -> None:
    cfg = json.loads((ROOT / "paper" / "config.json").read_text())
    universe, start, equity = cfg["universe"], cfg["start"], float(cfg["equity"])
    data, rate, src = load_inputs(universe)
    last = min(df.index[-1] for df in data.values())
    data = {t: df.loc[:last] for t, df in data.items()}
    if last < pd.Timestamp(start):
        print(f"Data ends {last.date()}, before the paper start {start}. Refresh the data after the close.")
        return
    stale = _next_session_already_opened(last)

    a_text, a_row = modern_section(data, universe, rate, start, equity)
    b_text, b_row = s2_section(data, rate, start, equity)
    header = [f"# Paper-trading report — after the close of {last.date()}\n",
              f"Paper account start: {start} with {money(equity)} per version · data: `{src.relative_to(ROOT)}` · "
              f"generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
              "> **Paper trading only.** Nothing here is sent to a broker. Fills, stops and P&L are "
              "simulated by the backtest engine with the same rules and costs as the research.\n"]
    if stale:
        header.append("> ⚠️ **Generated late:** the session these orders are for had already opened. "
                      "This report doesn't count as a real-time record.\n")
    text = "\n".join(header + [a_text, b_text])

    out_dir = ROOT / "paper" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{last.date()}.md"
    if path.exists() and path.read_text().split("\n", 3)[3:] != text.split("\n", 3)[3:]:
        path = out_dir / f"{last.date()}-rerun-{dt.datetime.now().strftime('%H%M%S')}.md"
        print(f"NOTE: a report for {last.date()} already exists and differs; archived as {path.name}")
    if not path.exists():
        path.write_text(text)
        journal = ROOT / "paper" / "journal.csv"
        row = pd.DataFrame([{**r, "session": last.date(), "real_time": not stale, "report": path.name,
                             "generated": dt.datetime.now().isoformat(timespec="seconds")}
                            for r in (a_row, b_row)])
        row.to_csv(journal, mode="a", header=not journal.exists(), index=False)
    print(text)
    print(f"\nsaved {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
