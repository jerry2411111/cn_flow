"""Desktop program for the money-flow page (the "A股资金流向" shortcut or cn_flow.exe runs this).  Same page and data as
ui.py, in its own window instead of a browser: the read-only server runs inside this process on a free local port and
stops when the window closes.  It places no orders.  sync.py fetches each day's results from the GitHub release; the
page checks for them every minute and reloads itself.

Usage: .venv\\Scripts\\pythonw.exe -I -X utf8 app.py   (create_shortcut.cmd makes the desktop and Start menu shortcuts)
"""
from __future__ import annotations

import ctypes
import sys
import threading
import traceback
import winreg
from http.server import ThreadingHTTPServer
from pathlib import Path

if getattr(sys, "frozen", False):                 # cn_flow.exe: page files inside, results and settings next to it
    HERE, ROOT = Path(sys._MEIPASS), Path(sys.executable).resolve().parent
else:
    HERE = ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                       # -I leaves the script's folder off sys.path

TITLE = "A股资金流向"
user32 = ctypes.windll.user32
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def dark_theme():
    """True when Windows apps use the dark theme, so the window opens dark instead of flashing white"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def already_open():
    """a second click on the shortcut brings the open window forward instead of opening another one"""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, "Local\\cn_flow_app")
    if ctypes.get_last_error() != 183:              # ERROR_ALREADY_EXISTS
        return False
    h = user32.FindWindowW(None, TITLE)
    if h:
        user32.ShowWindow(h, 9)                     # SW_RESTORE
        user32.SetForegroundWindow(h)
    return True


def main():
    if already_open():
        return
    import webview
    import sync
    import ui
    sync.start(ui.OUT)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    webview.create_window(TITLE, f"http://127.0.0.1:{srv.server_port}/", width=1400, height=900, min_size=(900, 600),
                          background_color="#0d0d0d" if dark_theme() else "#f9f9f7", text_select=True, zoomable=True)
    webview.start(gui="edgechromium", icon=str(HERE / "ui" / "icon.ico"),
                  private_mode=False, storage_path=str(ROOT / "data" / "webview"))   # keeps the page's saved choices
    srv.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:                               # pythonw has no console: show the error and keep it in logs/
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "app_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        user32.MessageBoxW(None, f"打开失败：\n{traceback.format_exc(limit=1)}\n详细见 logs\\app_error.log", TITLE, 0x10)
