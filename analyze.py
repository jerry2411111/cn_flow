"""Money-flow rankings and the regression of later price changes on past money flow, from data/days.

Definitions (per stock, per trading day; collect.py has how a tick row is classified)
  net inflow      money in buyer-initiated ticks - money in seller-initiated ticks (yuan); no bid-ask spread is used.
                  On a day whose high is the limit-up price (low: limit-down) the ticks at that price are left out:
                  at a locked limit the flag shows who hit the queue, not who wanted the stock.  Limits: 20% for
                  300/301/302/688/689, 5% for a main-board stock whose name has ST today, else 10%, from the previous
                  close (on an ex-date: the exchange's ex-rights reference price)
  window k       the last k trading days, k = 1, 3, 7, 14, 30, 60, 120: net_k is the sum of the daily net inflows
  net % of float  net_k / (float shares x close of the last day) x 100   (float shares: the latest figure)
  net % of turn.  net_k / turnover of the same k days x 100 = average daily net / average daily turnover
  The two percentages are the measures that compare stocks: the amount itself grows with the size of the stock.
  return          from close to close, adjusted for dividends, bonus shares and rights issues (xdxr)
Sectors: TDX industries (level 1, level 2) and TDX concept blocks.  A sector's flows are its members' sums; its
return is the float-cap-weighted average of its members' daily returns (weights from the day before).

Regression (one per day t, across stocks, then averaged over the days)
  y = return from the close of t to the close of t + h, in %: h = 1 (the next day) and 5 (the next week)
  x = one flow indicator over the k days up to t: net % of float, net % of turnover, or the net amount itself, cut at
      the day's 1st / 99th percentile and standardised across stocks that day (0 = the day's average, 1 = one
      standard deviation above it)
  y = a + b x: a + b is the expected return of a stock whose inflow is one standard deviation above the day's
  average.  Reported: its average over the days, compared with 0 (not with other stocks: it includes the market's
  own move), with a Newey-West t-statistic (h lags: the days' later returns overlap).
  Conditions: all stocks, and the same regression within the stocks whose price rose (up) or fell (down) over the
  flow's own k days (x is still standardised across all the stocks of the day, so one standard deviation means the
  same in each).  Stocks in their first 20 trading days in the data, or not trading on t or t + h,
  are left out.  No trading costs, no account balance: this is an analysis, not a trading test.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DAYS = ROOT / "data" / "days"
REF = ROOT / "data" / "ref"
OUT = ROOT / "results"
K = (1, 3, 7, 14, 30, 60, 120)
H = (1, 5)                                           # the next day, the next week (5 trading days)
SECTOR_TYPES = {"hy1": "行业(一级)", "hy2": "行业(二级)", "gn": "概念"}
INDICATORS = ("pct_float", "pct_turnover", "net")    # the flow indicators the regression uses, one at a time
CONDS = {"all": None, "up": 1, "down": -1}          # the price over the flow's own k days: any, rose, fell


# ------------------------------------------------------------------ the panel
def load():
    files = sorted(p for p in DAYS.glob("2*.parquet") if p.stem.isdigit())
    df = pd.concat([pd.read_parquet(f).assign(date=f.stem) for f in files], ignore_index=True)
    df = df[df.status != "error"]
    st = pd.read_parquet(REF / "stocks.parquet")
    fin = pd.read_parquet(REF / "finance.parquet")
    codes = sorted(set(df.code) & set(st.code))
    dates = sorted(df.date.unique())
    ok = df.status.eq("ok") & df.total.gt(0) & np.isfinite(df.close) & df.close.gt(0)
    df = df[ok]

    def wide(col):
        return df.pivot(index="date", columns="code", values=col).reindex(index=dates, columns=codes).to_numpy(float)

    P = {c: wide(c) for c in ("buy", "sell", "buy_hi", "sell_hi", "buy_lo", "sell_lo", "total", "high", "low", "close")}
    P["valid"] = np.isfinite(P["close"])
    P["dates"], P["codes"] = dates, codes
    st = st.set_index("code").reindex(codes)
    P["name"], P["mk"] = st.name.to_numpy(), st.mk.to_numpy()
    P["float_shares"] = fin.set_index("code").float_shares.reindex(codes).to_numpy(float)
    P["adj"], ref = adjusted(P, pd.read_parquet(REF / "xdxr.parquet"))
    # price limits: the limit-up / limit-down price from the reference price, rounded half up to the cent
    pct = np.where([c.startswith(("300", "301", "302", "688", "689")) for c in codes], 0.20,
                   np.where(["ST" in str(n).upper() for n in P["name"]], 0.05, 0.10))
    up = np.floor(ref * (1 + pct) * 100 + 0.5 + 1e-6) / 100
    dn = np.floor(ref * (1 - pct) * 100 + 0.5 + 1e-6) / 100
    P["at_up"] = np.isfinite(up) & (np.abs(P["high"] - up) < 0.0051)
    P["at_dn"] = np.isfinite(dn) & (np.abs(P["low"] - dn) < 0.0051)
    P["close_up"] = P["at_up"] & (np.abs(P["close"] - up) < 0.0051)
    P["close_dn"] = P["at_dn"] & (np.abs(P["close"] - dn) < 0.0051)
    P["net"] = (P["buy"] - P["sell"] - np.where(P["at_up"], P["buy_hi"] - P["sell_hi"], 0)
                - np.where(P["at_dn"], P["buy_lo"] - P["sell_lo"], 0))
    return P


def adjusted(P, xd):
    """back-adjusted closes (on an ex-date the earlier closes are scaled by the ex-rights reference price / the
    previous close), and each day's reference price for the price limits (the previous close; on an ex-date the
    reference price (previous close - cash + rights price x rights ratio) / (1 + bonus ratio + rights ratio))"""
    close, valid, dates = P["close"], P["valid"], np.array(P["dates"], dtype=int)
    adj = close.copy()
    prev = np.vstack([np.full((1, close.shape[1]), np.nan), ffill(close)[:-1]])
    ref = prev.copy()
    col = {c: j for j, c in enumerate(P["codes"])}
    xd = xd[(xd.date > dates[0]) & (xd.date <= dates[-1])]
    for r in xd.itertuples():
        j = col.get(r.code)
        if j is None:
            continue
        i = int(np.searchsorted(dates, r.date))            # the first trading day on or after the ex-date
        if i >= len(dates) or not valid[i:, j].any():
            continue
        i += int(valid[i:, j].argmax())                    # suspended on the ex-date: its next trading day
        pc = prev[i, j]
        if not np.isfinite(pc):
            continue
        ex = (pc - r.fenhong / 10 + r.peigu / 10 * r.peigujia) / (1 + r.songzhuangu / 10 + r.peigu / 10)
        if ex > 0 and np.isfinite(ex) and abs(pc / ex - 1) > 1e-6:
            adj[:i, j] *= ex / pc
            ref[i, j] = ex
    return adj, ref


def ffill(a):
    """forward-fill NaN down each column"""
    idx = np.where(np.isfinite(a), np.arange(len(a))[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    out = a[idx, np.arange(a.shape[1])]
    out[~np.isfinite(a[idx, np.arange(a.shape[1])])] = np.nan
    return out


def rolling_sum(a, k):
    c = np.vstack([np.zeros((1, a.shape[1])), np.cumsum(np.nan_to_num(a), axis=0)])
    out = np.full(a.shape, np.nan)
    out[k - 1:] = c[k:] - c[:-k]
    return out


def windows(P):
    """per window k: net_k, turnover_k, net % of float, net % of turnover, return over k (T x N each)"""
    valid, adj = P["valid"], P["adj"]
    pf = ffill(adj)
    first = np.where(valid.any(0), valid.argmax(0), len(valid))
    age = np.arange(len(valid))[:, None] - first[None, :]          # trading days since the first day in the data
    cap = P["float_shares"][None, :] * P["close"]
    W = {}
    for k in K:
        net, tot = rolling_sum(P["net"], k), rolling_sum(P["total"], k)
        full = (age >= k - 1) & valid                               # the whole window after the first day, traded on t
        past = np.full(adj.shape, np.nan)
        past[k:] = pf[k:] / pf[:-k] - 1
        for a in (net, tot, past):
            a[~full] = np.nan
        W[k] = {"net": net, "turnover": tot, "pct_float": 100 * net / cap,
                "pct_turnover": 100 * net / np.where(tot > 0, tot, np.nan), "ret": 100 * past}
    return W, age, first


# ------------------------------------------------------------------ sectors
def sector_members(P):
    ind = pd.read_parquet(REF / "industry.parquet")
    names = pd.read_csv(REF / "industry_names.csv", dtype=str) if (REF / "industry_names.csv").exists() else pd.DataFrame(columns=["t_code", "name"])
    nm = dict(zip(names.t_code, names.name))
    tcode = dict(zip(zip(ind.mk.astype(int), ind.code), ind.t_code))
    t = [tcode.get((int(m), c)) if m == m else None for m, c in zip(P["mk"], P["codes"])]
    groups = {"hy1": {}, "hy2": {}, "gn": {}}
    for j, tc in enumerate(t):
        if isinstance(tc, str) and tc.startswith("T"):
            groups["hy1"].setdefault(tc[:3], []).append(j)
            groups["hy2"].setdefault(tc[:5], []).append(j)
    col = {c: j for j, c in enumerate(P["codes"])}
    con = pd.read_parquet(REF / "concepts.parquet")
    for b, c in zip(con.block, con.code):
        if c in col:
            groups["gn"].setdefault(b, []).append(col[c])
    label = {"hy1": {g: nm.get(g, g) for g in groups["hy1"]}, "hy2": {g: nm.get(g, g) for g in groups["hy2"]},
             "gn": {g: g for g in groups["gn"]}}
    industry_name = [nm.get(tc[:5], "") if isinstance(tc, str) else "" for tc in t]
    return groups, label, industry_name


def sector_panel(P, members):
    """sector x day: net, turnover, float cap, and an index level from the members' cap-weighted returns"""
    valid, adj, close = P["valid"], ffill(P["adj"]), P["close"]
    r = np.full(adj.shape, np.nan)
    r[1:] = adj[1:] / adj[:-1] - 1
    r[~valid] = np.nan
    w = P["float_shares"][None, :] * ffill(close)
    w_prev = np.vstack([np.full((1, w.shape[1]), np.nan), w[:-1]])
    names = list(members)
    S = {c: np.full((len(valid), len(names)), np.nan) for c in ("net", "total", "cap", "level")}
    for g, js in enumerate(members.values()):
        js = np.array(js)
        S["net"][:, g] = np.nansum(P["net"][:, js], 1)
        S["total"][:, g] = np.nansum(P["total"][:, js], 1)
        S["cap"][:, g] = np.nansum(np.where(valid[:, js], P["float_shares"][js] * close[:, js], 0), 1)
        ww = np.where(np.isfinite(r[:, js]) & np.isfinite(w_prev[:, js]), w_prev[:, js], 0)
        rr = np.nansum(ww * np.nan_to_num(r[:, js]), 1) / np.where(ww.sum(1) > 0, ww.sum(1), np.nan)
        rr[0] = 0
        S["level"][:, g] = np.cumprod(1 + np.nan_to_num(rr))
    S["valid"] = np.isfinite(S["level"]) & (S["total"] > 0)
    S["names"] = names
    return S


def sector_windows(S):
    W = {}
    lvl = S["level"]
    for k in K:
        net, tot = rolling_sum(S["net"], k), rolling_sum(S["total"], k)
        past = np.full(lvl.shape, np.nan)
        past[k:] = lvl[k:] / lvl[:-k] - 1
        W[k] = {"net": net, "turnover": tot, "pct_float": 100 * net / np.where(S["cap"] > 0, S["cap"], np.nan),
                "pct_turnover": 100 * net / np.where(tot > 0, tot, np.nan), "ret": 100 * past}
    return W


# ------------------------------------------------------------------ regression
def winsor(x, lo=1, hi=99):
    a, b = np.nanpercentile(x, [lo, hi])
    return np.clip(x, a, b)


def z(x):
    s = x.std()
    return (x - x.mean()) / s if s > 0 else x * 0


def newey_west_t(b, lags):
    b = np.asarray(b, float)
    b = b[np.isfinite(b)]
    n = len(b)
    if n < 10:
        return np.nan
    e = b - b.mean()
    v = e @ e / n
    for L in range(1, min(lags, n - 1) + 1):
        v += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return b.mean() / np.sqrt(v / n) if v > 0 else np.nan


def regress(X, Y, ok, min_n, R):
    """one regression a day, y = a + b x, x standardised that day across all the stocks; for each condition (all, and
    the stocks whose price R over the flow's own k days rose / fell) per day: n, a + b (the expected return at one
    standard deviation more inflow than the day's average), a (at the day's average inflow), and one standard
    deviation of the flow in its own units"""
    rows = {c: [] for c in CONDS}
    for t in range(len(Y)):
        m = ok[t] & np.isfinite(X[t]) & np.isfinite(Y[t]) & np.isfinite(R[t])
        if m.sum() < min_n:
            continue
        xw, y, r = winsor(X[t][m]), Y[t][m], R[t][m]
        x, sd = z(xw), xw.std()
        for c, sign in CONDS.items():
            g = np.ones(len(y), bool) if sign is None else np.sign(r) == sign
            if g.sum() < max(min_n // 4, 5) or np.ptp(x[g]) == 0:
                continue
            a, b = np.linalg.lstsq(np.column_stack([np.ones(g.sum()), x[g]]), y[g], rcond=None)[0]
            rows[c].append((t, g.sum(), a + b, a, sd))
    return rows


def summarize_reg(rows, h, level, k, indicator, cond):
    if not rows:
        return None
    a = np.array(rows, float)
    return {"level": level, "indicator": indicator, "cond": cond, "k": k, "h": h, "days": len(a),
            "mean_n": round(a[:, 1].mean(), 1), "ret_1sd": a[:, 2].mean(), "t": newey_west_t(a[:, 2], h),
            "share_pos": (a[:, 2] > 0).mean(), "ret_0sd": a[:, 3].mean(), "sd": a[:, 4].mean()}


def future(adj_ff, valid, h):
    f = np.full(adj_ff.shape, np.nan)
    f[:-h] = 100 * (adj_ff[h:] / adj_ff[:-h] - 1)
    f[:-h][~valid[h:]] = np.nan                       # must trade on t + h
    return f


# ------------------------------------------------------------------ output
def write(f, text, mode="wt"):
    """a results file, replaced in one step (the page may read it at any moment)"""
    tmp = f.with_name(f.name + ".tmp")
    with (gzip.open(tmp, mode, encoding="utf-8") if f.suffix == ".gz" else open(tmp, mode, encoding="utf-8")) as fh:
        fh.write(text)
    os.replace(tmp, f)


def write_table(df, f):
    """rows as {"columns": [...], "rows": [[...], ...]}, missing and infinite numbers as null"""
    d = df.replace([np.inf, -np.inf], np.nan).astype(object)
    d = d.where(d.notna(), None)
    write(f, json.dumps({"columns": list(d.columns), "rows": d.values.tolist()}, ensure_ascii=False,
                        default=lambda o: o.item(), allow_nan=False))


# ------------------------------------------------------------------ main
def main(log=print):
    OUT.mkdir(exist_ok=True)
    P = load()
    W, age, first = windows(P)
    T = len(P["dates"])
    last = P["dates"][-1]
    groups, labels, ind_name = sector_members(P)
    log(f"panel: {T} days {P['dates'][0]}..{last}, {len(P['codes'])} stocks")

    # rankings on the last day
    rk = []
    t = T - 1
    cap = P["float_shares"] * P["close"][t]
    tag = np.where(P["close_up"][t], "涨停", np.where(P["close_dn"][t], "跌停", ""))   # closed at the limit on the last day
    for k in K:
        w = W[k]
        df = pd.DataFrame({"k": k, "code": P["codes"], "name": P["name"], "industry": ind_name,
                           "net": w["net"][t], "turnover": w["turnover"][t],
                           "pct_float": w["pct_float"][t], "pct_turnover": w["pct_turnover"][t], "ret": w["ret"][t],
                           "close": P["close"][t], "float_cap": cap, "limit": tag})
        rk.append(df[np.isfinite(df.net)])
    rk = pd.concat(rk, ignore_index=True)
    write_table(rk, OUT / "rank_stocks.json.gz")

    srk, sreg = [], []
    for typ, members in groups.items():
        S = sector_panel(P, members)
        SW = sector_windows(S)
        for k in K:
            w = SW[k]
            lead = {}
            for m in INDICATORS:                           # the three members with the largest inflow over k, by m
                lead[m] = []
                for js in members.values():
                    js = np.array(js)
                    v = W[k][m][t, js]
                    o = js[np.argsort(-np.nan_to_num(v, nan=-np.inf))[:3]]
                    lead[m].append("、".join(f"{P['name'][j]}" for j in o if np.isfinite(W[k][m][t, j])))
            srk.append(pd.DataFrame({"type": typ, "k": k, "sector": S["names"],
                                     "name": [labels[typ][g] for g in S["names"]],
                                     "members": [len(v) for v in members.values()], "net": w["net"][t],
                                     "turnover": w["turnover"][t], "pct_float": w["pct_float"][t],
                                     "pct_turnover": w["pct_turnover"][t], "ret": w["ret"][t],
                                     **{f"leaders_{m}": v for m, v in lead.items()}}))
        lvl = S["level"]
        for h in H:
            Y = np.full(lvl.shape, np.nan)
            Y[:-h] = 100 * (lvl[h:] / lvl[:-h] - 1)
            for k in K:
                ok = S["valid"] & (np.array([len(v) for v in members.values()])[None, :] >= 3)
                for m in INDICATORS:
                    for c, rows in regress(SW[k][m], Y, ok, 8, SW[k]["ret"]).items():
                        s = summarize_reg(rows, h, typ, k, m, c)
                        if s:
                            sreg.append(s)
    srk = pd.concat(srk, ignore_index=True)
    srk = srk[np.isfinite(srk.net)]
    write_table(srk, OUT / "rank_sectors.json.gz")

    reg = []
    pf = ffill(P["adj"])
    for h in H:
        Y = future(pf, P["valid"], h)
        for k in K:
            # a stock that started trading inside the data (a new listing) waits 20 more days
            ok = P["valid"] & (age >= k - 1) & ((first == 0)[None, :] | (age >= k - 1 + 20))
            for m in INDICATORS:
                for c, rows in regress(W[k][m], Y, ok, 200, W[k]["ret"]).items():
                    s = summarize_reg(rows, h, "stock", k, m, c)
                    if s:
                        reg.append(s)
    reg = pd.DataFrame(reg + sreg)
    write(OUT / "regression.csv", reg.to_csv(index=False))
    write_table(reg, OUT / "regression.json.gz")
    meta = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "first_day": P["dates"][0],
            "last_day": last, "days": T, "stocks": len(P["codes"]),
            "sectors": {typ: len(m) for typ, m in groups.items()}, "K": K, "H": H}
    write(OUT / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))     # last: the page reloads on it
    log(f"rankings for {last}: {len(rk)} stock rows, {len(srk)} sector rows; regression {len(reg)} rows")
    return meta


if __name__ == "__main__":
    main()
