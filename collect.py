"""Collect the data: the A-share list, reference data, and one file per trading day with every stock's tick summary.

  data/days/YYYYMMDD.parquet   one row per stock: money bought / sold by buyer- and seller-initiated ticks (by size),
                               auction, after-hours and total turnover, open / high / low / close, status
  data/ref/                    stocks.parquet, finance.parquet (float shares, IPO date), xdxr.parquet (dividends and
                               splits, for adjusted returns), industry.parquet (TDX industry code), concepts.parquet,
                               industry_names.csv, calendar.json (which weekdays traded)

Money flow of a tick row = price x vol x 100 (yuan); buyer-initiated rows (flag 0) count as inflow, seller-initiated
(flag 1) as outflow.  Auctions (flag 2), after-hours fixed-price trades (5) and the auction preview (8) have no
initiator: they enter the turnover, not the flow.  No bid-ask spread is used: the tick history has no quotes, the
direction is the server's own flag (the trade at the ask or at the bid of the moment).
Size of a row (a 3-second snapshot, so several orders can be in one row): S < 40k yuan, M < 200k, L < 1m, XL >= 1m.

Usage (with the project's .venv):
  python collect.py backfill --days 245      the last 245 trading days, newest first; resumable
  python collect.py update                   reference data + any trading day not collected yet + the analysis
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
import itertools
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))                      # run with -I: the project's own modules
import tdx  # noqa: E402

DAYS = ROOT / "data" / "days"
REF = ROOT / "data" / "ref"
CN = timezone(timedelta(hours=8))                  # Beijing, no daylight saving
BUCKETS = (("s", 4e4), ("m", 2e5), ("l", 1e6), ("xl", float("inf")))
COLS = ["mk", "code", "status", "n", "buy", "sell"] + [f"{s}_{b}" for b, _ in BUCKETS for s in ("buy", "sell")] + \
       ["buy_hi", "sell_hi", "buy_lo", "sell_lo", "neutral", "after", "total", "vol", "open", "high", "low", "close"]
INCON = Path(os.environ.get("CN_FLOW_INCON", ""))  # a TDX client's incon.dat: only if industry_names.csv is missing


def below_normal():
    try:                                           # leave the CPU to the trading app
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except Exception:
        pass


def summarize(rows):
    """one stock-day's tick rows -> the day file's numbers.  buy_hi / sell_hi (buy_lo / sell_lo): the buyer- and
    seller-initiated money at the day's high (low) price.  When that price is the limit-up (limit-down) price the
    flag says nothing about demand: with the asks empty at limit-up, trades happen when sellers hit the queued bids
    and are flagged seller-initiated; the analysis takes these amounts out of the net flow on such days."""
    out = dict.fromkeys(COLS[3:], 0.0)
    out["n"] = len(rows)
    px = []
    live = [r["price"] for r in rows if r["buyorsell"] in (0, 1, 2) and r["vol"] > 0 and r["time"] <= "15:00"]
    hi, lo = (max(live), min(live)) if live else (None, None)
    for r in rows:
        f, p, v = r["buyorsell"], r["price"], r["vol"]
        amt = p * v * 100.0
        out["total"] += amt
        out["vol"] += v
        if f in (0, 1):
            side = "buy" if f == 0 else "sell"
            out[side] += amt
            for b, top in BUCKETS:
                if amt < top:
                    out[f"{side}_{b}"] += amt
                    break
            if p == hi:
                out[f"{side}_hi"] += amt
            if p == lo:
                out[f"{side}_lo"] += amt
        elif f == 2:
            out["neutral"] += amt
        elif f == 5:
            out["after"] += amt
        if f in (0, 1, 2) and v > 0 and r["time"] <= "15:00":
            px.append((r["time"], p))
    if px:
        out["open"] = next((p for t, p in px if t >= "09:25"), px[0][1])
        out["close"] = px[-1][1]                    # the 15:00 closing auction (or the last trade before it)
        out["high"] = max(p for _, p in px)
        out["low"] = min(p for _, p in px)
    else:
        out["open"] = out["high"] = out["low"] = out["close"] = float("nan")
    return out


def one(mk, code, d):
    try:
        rows = tdx.ticks(mk, code, d)
        s = summarize(rows)
        s["status"] = "ok" if rows else "none"
    except Exception:
        s = dict.fromkeys(COLS[3:], float("nan"))
        s["status"] = "error"
    s["mk"], s["code"] = mk, code
    return s


def write_day(d, recs):
    df = pd.DataFrame(recs, columns=COLS).sort_values(["mk", "code"])
    tmp = DAYS / f"{d}.tmp.parquet"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, DAYS / f"{d}.parquet")


# ------------------------------------------------------------------ calendar
def _traded(d):
    for mk, code in ((tdx.SZ, "000001"), (tdx.SH, "600519"), (tdx.SH, "601318")):
        if tdx.call(lambda a: a.get_history_transaction_data(mk, code, 0, 10, int(d))):
            return True
    return False


def last_complete_day():
    now = datetime.now(CN)
    return now.date() if now.hour >= 16 else now.date() - timedelta(days=1)


def trading_days(n, end=None, since=None):
    """the last n trading days up to end (newest first), or with since (YYYYMMDD) every trading day back to it, from
    calendar.json, asking the server about new weekdays"""
    f = REF / "calendar.json"
    cal = json.loads(f.read_text()) if f.exists() else {}
    end = end or last_complete_day()
    if since:
        first = datetime.strptime(since, "%Y%m%d").date()
        n = (end - first).days + 1                         # more than enough: the loop stops at since
    out, d, asked = [], end, 0
    while len(out) < n and (not since or d >= first):
        if d.weekday() < 5:
            k = d.strftime("%Y%m%d")
            if k not in cal or (not cal[k] and (end - d).days < 7):   # a recent "no" is asked again
                cal[k] = _traded(k)
                asked += 1
            if cal[k]:
                out.append(k)
        d -= timedelta(days=1)
        if (end - d).days > 3 * 366 + n * 2:
            break
    if asked:
        f.write_text(json.dumps(dict(sorted(cal.items())), indent=0))
    return out


# ------------------------------------------------------------------ reference data
def refresh_reference(threads=32, log=print):
    REF.mkdir(parents=True, exist_ok=True)
    stocks = tdx.a_shares()
    st = pd.DataFrame(stocks, columns=["mk", "code", "name"])
    st.to_parquet(REF / "stocks.parquet", index=False)
    log(f"stocks: {len(st)} A shares (Shenzhen {int((st.mk == 0).sum())}, Shanghai {int((st.mk == 1).sum())})")

    def fin(mk, code):
        try:
            f = tdx.finance(mk, code)
            return {"mk": mk, "code": code, "float_shares": f["liutongguben"], "total_shares": f["zongguben"],
                    "ipo_date": int(f["ipo_date"] or 0), "updated": int(f["updated_date"] or 0)}
        except Exception:
            return {"mk": mk, "code": code}

    def xd(mk, code):
        try:
            return [{"mk": mk, "code": code, "date": e["year"] * 10000 + e["month"] * 100 + e["day"],
                     "fenhong": e["fenhong"] or 0.0, "peigu": e["peigu"] or 0.0, "peigujia": e["peigujia"] or 0.0,
                     "songzhuangu": e["songzhuangu"] or 0.0}
                    for e in tdx.xdxr(mk, code) if e.get("category") == 1]
        except Exception:
            return []

    with ThreadPoolExecutor(threads) as ex:
        fins = list(tqdm(ex.map(lambda s: fin(s[0], s[1]), stocks), total=len(stocks), desc="float shares", unit="stock"))
        xds = list(tqdm(ex.map(lambda s: xd(s[0], s[1]), stocks), total=len(stocks), desc="dividends/splits", unit="stock"))
    pd.DataFrame(fins).to_parquet(REF / "finance.parquet", index=False)
    pd.DataFrame([e for x in xds for e in x],
                 columns=["mk", "code", "date", "fenhong", "peigu", "peigujia", "songzhuangu"]).to_parquet(REF / "xdxr.parquet", index=False)
    log(f"finance: {sum(1 for f in fins if 'float_shares' in f)} of {len(fins)}; ex-rights events: {sum(len(x) for x in xds)}")

    raw = tdx.report_file("tdxhy.cfg").decode("gbk", errors="replace")
    ind = []
    for line in raw.splitlines():
        p = line.split("|")
        if len(p) >= 3 and p[2].startswith("T"):
            ind.append({"mk": int(p[0]), "code": p[1], "t_code": p[2]})
    pd.DataFrame(ind).to_parquet(REF / "industry.parquet", index=False)
    con = pd.DataFrame(tdx.blocks("block_gn.dat"), columns=["block", "code"]).drop_duplicates()
    con.to_parquet(REF / "concepts.parquet", index=False)
    log(f"industries: {len(ind)} stocks; concepts: {con.block.nunique()} blocks, {len(con)} memberships")

    names = REF / "industry_names.csv"             # TDX industry code -> name, a fixed table kept in the repository
    if not names.exists() and INCON.is_file():
        t = INCON.read_bytes().decode("gbk", errors="replace")
        sec = t[t.index("#TDXNHY"):]
        sec = sec[:sec.index("#", 1)] if "#" in sec[1:] else sec
        rows = [l.split("|", 1) for l in sec.splitlines()[1:] if "|" in l]
        pd.DataFrame(rows, columns=["t_code", "name"]).to_csv(names, index=False, encoding="utf-8")
        log(f"industry names: {len(rows)} copied from {INCON}")
    return st


def universe():
    st = pd.read_parquet(REF / "stocks.parquet")
    fin = pd.read_parquet(REF / "finance.parquet")
    st = st.merge(fin[["mk", "code", "ipo_date"]], on=["mk", "code"], how="left")
    return st


# ------------------------------------------------------------------ the day files
_done = None                                       # stock-days finished, shared by the worker processes


def _init_worker(done, slot, per):
    """a worker process: the shared progress counter, and its own range of servers to connect to"""
    global _done
    _done = done
    with slot.get_lock():
        i, slot.value = slot.value, slot.value + 1
    tdx._next = itertools.count(i * per)


def collect_day(d, todo, kept, threads):
    """one day in a worker process, with its own connections; writes the day file, returns how many stocks failed"""
    recs = list(kept)
    with ThreadPoolExecutor(threads) as ex:
        for r in ex.map(lambda s: one(s[0], s[1], d), todo):
            recs.append(r)
            with _done.get_lock():
                _done.value += 1
    write_day(d, recs)
    return d, sum(r["status"] == "error" for r in recs)


def collect_days(days, threads, desc, log=print, workers=1):
    """every stock of each day in days (newest first); a day with error rows is collected again for those rows only.
    threads: connections in all; workers > 1: that many processes, each collecting a day at a time with
    threads / workers connections (one process alone spends a good part of its time parsing, not waiting)"""
    DAYS.mkdir(parents=True, exist_ok=True)
    st = universe()
    work = []                                      # (day, [(mk, code)], kept rows)
    for d in days:
        f = DAYS / f"{d}.parquet"
        listed = [(int(r.mk), r.code) for r in st.itertuples() if not (r.ipo_date and r.ipo_date > int(d))]
        if not f.exists():
            work.append((d, listed, []))
        else:
            old = pd.read_parquet(f)
            redo = old[old.status == "error"]
            have = set(zip(old.mk.astype(int), old.code))
            missing = [s for s in listed if s not in have]
            if len(redo) or missing:
                work.append((d, [(int(r.mk), r.code) for r in redo.itertuples()] + missing,
                             old[old.status != "error"].to_dict("records")))
    total = sum(len(w[1]) for w in work)
    if not total:
        log(f"{desc}: nothing to collect")
        return 0
    bar = tqdm(total=total, desc=desc, unit="stock-day", smoothing=0.05, dynamic_ncols=True)
    errors = 0
    if workers > 1 and len(work) > 1:
        per = -(-threads // workers)
        done, slot = mp.Value("q", 0), mp.Value("i", 0)
        bar.set_postfix_str(f"{workers} workers x {per} connections")
        with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(done, slot, per)) as ex:
            pending = {ex.submit(collect_day, d, todo, kept, per) for d, todo, kept in work}
            while pending:
                finished, pending = wait(pending, timeout=1)
                bar.update(done.value - bar.n)
                for fu in finished:
                    d, e = fu.result()
                    errors += e
                    if e:
                        log(f"{d}: {e} stocks failed; run again to retry them")
        bar.close()
        return errors
    with ThreadPoolExecutor(threads) as ex:
        for d, todo, kept in work:
            bar.set_postfix_str(f"day {d}")
            futs = [ex.submit(one, mk, code, d) for mk, code in todo]
            recs = list(kept)
            for fu in as_completed(futs):
                recs.append(fu.result())
                bar.update()
            e = sum(r["status"] == "error" for r in recs)
            errors += e
            write_day(d, recs)
            if e:
                log(f"{d}: {e} stocks failed; run again to retry them")
    bar.close()
    return errors


def single_instance():
    """an open, locked file while this run collects (the system frees the lock if the process dies); None if another
    backfill or update holds it"""
    (ROOT / "data").mkdir(exist_ok=True)
    f = open(ROOT / "data" / "collect.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:                                      # the GitHub workflow runs on Linux
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backfill", "update", "reference"])
    ap.add_argument("--days", type=int, default=245)
    ap.add_argument("--since", help="backfill every trading day from this date (YYYYMMDD) instead of --days")
    ap.add_argument("--threads", type=int, default=32, help="connections in all")
    ap.add_argument("--workers", type=int, default=1, help="backfill: processes, each collecting a day at a time")
    a = ap.parse_args()
    below_normal()
    (ROOT / "logs").mkdir(exist_ok=True)
    logf = ROOT / "logs" / f"{a.cmd}_{date.today():%Y%m%d}.log"

    def log(msg):
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        tqdm.write(line)
        with open(logf, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    lock = single_instance()
    if lock is None:
        log(f"{a.cmd}: another collection (backfill or update) is running; nothing done")
        return
    t0 = time.time()
    refresh_reference(a.threads, log)              # the list, float shares and dividends change: every run
    if a.cmd == "reference":
        return
    if a.cmd == "backfill":
        days = trading_days(a.days, since=a.since)
        log(f"backfill: {len(days)} trading days {days[-1]}..{days[0]}")
        e = collect_days(days, a.threads, "tick history", log, a.workers)
    else:                                          # new days, and any gap back to the oldest day collected
        have = sorted(p.stem for p in DAYS.glob("2*.parquet") if p.stem.isdigit())
        days = [d for d in trading_days(len(have) + 15) if not have or d >= have[0]]
        e = collect_days(days, a.threads, "new days", log)
    log(f"{a.cmd} done in {(time.time() - t0) / 60:.1f} min; {e} stock-days failed")
    if a.cmd == "update":
        import analyze
        analyze.main(log=log)
        (ROOT / "results" / "last_update.json").write_text(json.dumps(
            {"time": datetime.now(CN).strftime("%Y-%m-%d %H:%M"), "failed": e}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
