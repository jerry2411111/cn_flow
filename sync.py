"""Keeps results/ in step with the results the GitHub workflow publishes after each trading day (the release
"results" of the repository), so the program shows the rankings and the regression without the tick history.

Every 20 minutes: meta.json of the release; when it is newer than the local one (later last day, or the same day
computed later), the other files are downloaded first and meta.json last (the page reloads on meta.json).  Local
results that are newer (a machine that collects and analyses itself) are left alone.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = "jerry2411111/cn_flow"
BASE = f"https://github.com/{REPO}/releases/download/results/"
FILES = ("rank_stocks.json.gz", "rank_sectors.json.gz", "regression.json.gz", "regression.csv", "last_update.json")
EVERY = 20 * 60
STATE = {"state": "off"}                          # shown on the page: off / checking / downloading / ok / error


def fetch(name, timeout=60):
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "cn_flow", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def stamp(m):
    return (str(m.get("last_day") or ""), str(m.get("generated") or ""))


def once(out: Path):
    STATE.update(state="checking")
    remote = json.loads(fetch("meta.json", 30))
    f = out / "meta.json"
    local = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if stamp(remote) > stamp(local):
        STATE.update(state="downloading")
        out.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            tmp = out / (name + ".tmp")
            tmp.write_bytes(fetch(name))
            os.replace(tmp, out / name)
        tmp = out / "meta.json.tmp"
        tmp.write_text(json.dumps(remote, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, f)
    STATE.update(state="ok", checked=datetime.now().strftime("%H:%M"), error=None)


def loop(out: Path):
    while True:
        try:
            once(out)
        except Exception as e:                    # offline, GitHub unreachable, a file cut short: try again later
            STATE.update(state="error", checked=datetime.now().strftime("%H:%M"), error=repr(e)[:200])
        time.sleep(EVERY if (out / "meta.json").exists() else 60)     # nothing to show yet: every minute


def start(out: Path):
    threading.Thread(target=loop, args=(out,), daemon=True, name="sync").start()
