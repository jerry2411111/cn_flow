"""TDX (通达信) public market-data servers through pytdx: no client, no login.

What works on the 2026 servers (probe*.py, 2026-10-31): the stock lists of Shenzhen and Shanghai, the tick history of
any day back to at least 2020 (get_history_transaction_data, 2000 rows a call, newest first), finance (float shares,
IPO date), ex-rights events (xdxr), the concept blocks (block_gn.dat) and the stock -> industry file tdxhy.cfg.
K-lines and live quotes come back empty, so daily prices are taken from the ticks.  The Beijing list fails: not used.
All twelve servers below give identical buy / sell / total amounts for the same stock-day (probe7.py); a few omit
the zero-volume 09:15-09:25 auction-preview rows (flag 8), which carry no money.

A tick row is a 3-second exchange snapshot: time (minute), price, vol (lots of 100 shares), buyorsell:
  0 = buyer-initiated, 1 = seller-initiated (95% agree with the tick rule, probe6.py), 2 = auctions / neutral,
  5 = after-hours fixed-price trades, 8 = auction preview (vol 0).
"""
from __future__ import annotations

import itertools
import struct
import threading
import time

from pytdx.hq import TdxHq_API
from pytdx.parser.get_security_list import GetSecurityList

SERVERS = [("218.75.126.9", 7709), ("180.153.18.170", 7709), ("115.238.56.198", 7709), ("220.178.55.86", 7709),
           ("119.29.19.242", 7709), ("60.191.117.167", 7709), ("59.36.5.11", 7709), ("115.238.90.165", 7709),
           ("220.178.55.71", 7709), ("183.60.224.178", 7709), ("60.12.136.250", 7709), ("218.106.92.182", 7709)]
SZ, SH = 0, 1
A_PREFIX = {SZ: ("000", "001", "002", "003", "300", "301", "302"), SH: ("600", "601", "603", "605", "688", "689")}
PAGE = 2000

_local = threading.local()
_next = itertools.count()


def api():
    """this thread's connection (each thread its own socket, spread over the servers)"""
    a = getattr(_local, "api", None)
    if a is None:
        i = next(_next)
        for j in range(len(SERVERS)):
            ip, port = SERVERS[(i + j) % len(SERVERS)]
            a = TdxHq_API(raise_exception=True)
            try:
                a.connect(ip, port, time_out=6)
                _local.api, _local.server = a, ip
                return a
            except Exception:
                continue
        raise ConnectionError("no TDX server answers")
    return a


def drop():
    a = getattr(_local, "api", None)
    _local.api = None
    try:
        a and a.disconnect()
    except Exception:
        pass


def call(fn, *args, tries=4):
    """fn(api, *args) with a reconnect (to the next server) after a failure"""
    for k in range(tries):
        try:
            return fn(api(), *args)
        except Exception:
            drop()
            if k == tries - 1:
                raise
            time.sleep(0.5 * (k + 1))


def _ticks(a, mk, code, date):
    rows, start = [], 0
    while True:
        r = a.get_history_transaction_data(mk, code, start, PAGE, int(date))
        if not r:
            break
        rows = r + rows
        start += len(r)
        if len(r) < PAGE:
            break
    return rows


def ticks(mk, code, date):
    """every tick row of one stock-day, oldest first; [] when the stock did not trade (or the day is no trading day)"""
    return call(_ticks, mk, code, date)


class _SecurityList(GetSecurityList):
    """pytdx's list parser, but a name that is not valid GBK (some 2026 pages: a cut double-byte character or a
    GB18030-only one) no longer fails the whole page of 1000"""

    def parseResponse(self, body_buf):
        (num,) = struct.unpack("<H", body_buf[:2])
        out, pos = [], 2
        for _ in range(num):
            code, volunit, name, _r1, dp, _pc, _r2 = struct.unpack("<6sH8s4sBI4s", body_buf[pos: pos + 29])
            out.append({"code": code.decode("ascii", errors="replace"), "volunit": volunit, "decimal_point": dp,
                        "name": name.decode("gb18030", errors="replace").rstrip("\x00").rstrip("�")})
            pos += 29
        return out


def _stock_list(a, mk):
    out, start = [], 0
    while True:
        cmd = _SecurityList(a.client, lock=a.lock)
        cmd.setParams(mk, start)
        r = cmd.call_api()
        if not r:
            break
        out += r
        start += len(r)
        if len(r) < 1000:
            break
    return out


def a_shares():
    """[(market, code, name)] of the A shares listed today (Shenzhen + Shanghai)"""
    out = []
    for mk in (SZ, SH):
        for s in call(_stock_list, mk):
            if s["code"].startswith(A_PREFIX[mk]) and len(s["code"]) == 6:
                out.append((mk, s["code"], s["name"].strip()))
    return out


def finance(mk, code):
    return call(lambda a: a.get_finance_info(mk, code))


def xdxr(mk, code):
    return call(lambda a: a.get_xdxr_info(mk, code)) or []


def blocks(name="block_gn.dat"):
    """[(block name, code)] of a block file (block_gn.dat = concepts).  pytdx's get_and_parse_block_info asks every
    chunk for the whole file's size and gets overlapping pieces (records misaligned after the first 30 kB); here each
    chunk asks for its own length."""
    from pytdx.reader.block_reader import BlockReader, BlockReader_TYPE_FLAT

    def get(a):
        size = a.get_block_info_meta(name)["size"]
        data = bytearray()
        for start in range(0, size, 0x7530):
            data.extend(a.get_block_info(name, start, min(0x7530, size - start)))
        if len(data) != size:
            raise ValueError(f"{name}: got {len(data)} of {size} bytes")
        return BlockReader().get_data(data, BlockReader_TYPE_FLAT)       # a bytearray: parsed in memory
    return [(b["blockname"], b["code"]) for b in call(get) or []]


def report_file(name):
    """a whole file from the server's report area (tdxhy.cfg = stock -> industry codes), as bytes"""
    def get(a):
        data, off = b"", 0
        while True:
            r = a.get_report_file(name, off)
            n = r.get("chunksize", 0)
            if not n:
                break
            data += r["chunkdata"]
            off += n
            if n < 30000:
                break
        return data
    return call(get)
