"""Local page for the money-flow rankings and the regression (http://127.0.0.1:8790).  Read only: it shows what
analyze.py wrote to results/ (here, or downloaded from the GitHub release by sync.py) and places no orders.  Reloads
the files when they change (the daily update).

Usage: python ui.py   (for a browser; app.py runs the same Handler inside the desktop program)
"""
from __future__ import annotations

import gzip
import json
import math
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if getattr(sys, "frozen", False):                 # the packed program: page files inside, results next to the .exe
    HERE, ROOT = Path(sys._MEIPASS), Path(sys.executable).resolve().parent
else:
    HERE = ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sync  # noqa: E402

OUT = ROOT / "results"
DAYS = ROOT / "data" / "days"
PORT = 8790
STOCK_COLS = ["code", "name", "industry", "net", "pct_float", "pct_turnover", "ret", "close", "float_cap", "turnover", "limit"]
SECTOR_COLS = ["sector", "name", "members", "net", "pct_float", "pct_turnover", "ret", "turnover",
               "leaders_net", "leaders_pct_float", "leaders_pct_turnover"]
SEARCH = ("code", "name", "sector", "leaders_net", "leaders_pct_float", "leaders_pct_turnover", "industry")

_cache = {}


def table(name, reader):
    """a results file, read again when its modification time changes"""
    f = OUT / name
    if not f.exists():
        return None
    mt = f.stat().st_mtime
    hit = _cache.get(name)
    if hit is None or hit[0] != mt:
        hit = (mt, reader(f))
        _cache[name] = hit
    return hit[1]


def read_rows(f):
    """analyze.write_table's file -> list of dicts"""
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        d = json.load(fh)
    cols = d["columns"]
    return [dict(zip(cols, r)) for r in d["rows"]]


def read_json(f):
    return json.loads(f.read_text(encoding="utf-8"))


def missing(v):
    return v is None or (isinstance(v, float) and not math.isfinite(v))


def pick(rows, q, sort, order, limit, offset, cols):
    if q:
        q = q.lower()
        rows = [r for r in rows if any(q in str(r.get(c) or "").lower() for c in SEARCH)]
    if rows and sort in rows[0]:                    # missing values last in either order; ties keep their order
        have = [r for r in rows if not missing(r[sort])]
        have.sort(key=lambda r: r[sort], reverse=order != "asc")
        rows = have + [r for r in rows if missing(r[sort])]
    page = rows[offset: offset + limit]
    return {"total": len(rows), "rows": [{"rank": offset + i + 1, **{c: r.get(c) for c in cols}} for i, r in enumerate(page)]}


def meta():
    m = table("meta.json", read_json) or {}
    up = table("last_update.json", read_json) or {}
    files = sorted(p.stem for p in DAYS.glob("2*.parquet") if p.stem.isdigit())
    return {**m, "update": up, "sync": sync.STATE,
            "collected": {"days": len(files), "first": files[0] if files else None, "last": files[-1] if files else None}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self.send(200, (HERE / "ui" / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/meta":
                return self.send(200, json.dumps(meta(), ensure_ascii=False))
            k, limit, offset = int(q.get("k", 1)), min(int(q.get("limit", 100)), 1000), int(q.get("offset", 0))
            if u.path == "/api/stocks":
                rows = table("rank_stocks.json.gz", read_rows)
                if rows is None:
                    return self.send(200, json.dumps({"total": 0, "rows": [], "industries": []}))
                d = [r for r in rows if r["k"] == k]
                inds = sorted({r["industry"] for r in d if r["industry"]})
                if q.get("industry"):
                    d = [r for r in d if r["industry"] == q["industry"]]
                out = pick(d, q.get("q", "").strip(), q.get("sort", "net"), q.get("order", "desc"), limit, offset, STOCK_COLS)
                out["industries"] = inds
                return self.send(200, json.dumps(out, ensure_ascii=False))
            if u.path == "/api/sectors":
                rows = table("rank_sectors.json.gz", read_rows)
                if rows is None:
                    return self.send(200, json.dumps({"total": 0, "rows": []}))
                d = [r for r in rows if r["k"] == k and r["type"] == q.get("type", "hy1")]
                out = pick(d, q.get("q", "").strip(), q.get("sort", "net"), q.get("order", "desc"), limit, offset, SECTOR_COLS)
                return self.send(200, json.dumps(out, ensure_ascii=False))
            if u.path == "/api/regression":
                return self.send(200, json.dumps(table("regression.json.gz", read_rows) or [], ensure_ascii=False))
            return self.send(404, json.dumps({"error": "not found"}))
        except Exception as e:                       # a bad parameter or a file being replaced: say so, keep serving
            return self.send(500, json.dumps({"error": repr(e)[:300]}))


def main():
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"already running: http://127.0.0.1:{PORT}/")
            return
    sync.start(OUT)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"cn_flow page: http://127.0.0.1:{PORT}/  (close this window to stop it)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
