"""Giao diện web cục bộ: python3 app.py  ->  http://127.0.0.1:8765"""
import csv
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import crawl
import crawl_kol
from playwright.sync_api import sync_playwright
import x_cookies

PORT = 8765
INDEX = Path(__file__).parent / "ui.html"
lock = threading.Lock()
state = {"running": False, "users": [], "done": [], "log": [], "rows": []}
kol = {"running": False, "keyword": "", "keywords": [], "kw_index": 0, "scrolls": 0, "idle": 0, "users": [], "error": ""}
kol_index: dict = {}
kol_stop = threading.Event()
KOL_COLS = ["username", "name", "followers", "verified", "bio", "keywords", "tweet_url", "tweet"]


resolving = threading.Event()


def resolve_in_background():
    if resolving.is_set():
        return
    resolving.set()

    def run():
        try:
            x_cookies.resolve_account()
        finally:
            resolving.clear()

    threading.Thread(target=run, daemon=True).start()


def cookie_status() -> dict:
    if not x_cookies.COOKIE_FILE.exists():
        return {"ok": False, "msg": "Chưa import cookie"}
    names = {c["name"] for c in x_cookies.load()}
    miss = [k for k in x_cookies.REQUIRED if k not in names]
    if miss:
        return {"ok": False, "msg": "Thiếu " + ", ".join(miss)}
    info = {}
    if x_cookies.META_FILE.exists():
        info = json.loads(x_cookies.META_FILE.read_text())
    else:
        resolve_in_background()
    return {"ok": True, "msg": "Đã có cookie", "username": info.get("username"), "name": info.get("name")}


def job(users, max_scrolls):
    def on_user(u, rows, err):
        with lock:
            state["done"].append(u)
            state["rows"] += rows
            state["log"].append(f"@{u}: " + (f"lỗi - {err}" if err else f"{len({r['telegram'] for r in rows})} link"))

    try:
        crawl.run_crawl(users, max_scrolls, True, on_user)
        crawl.write_csv(state["rows"])
    except Exception as e:
        with lock:
            state["log"].append(f"Lỗi: {e}")
    finally:
        with lock:
            state["running"] = False


def kol_job(keywords, latest, max_users):
    current = {"kw": ""}

    def on_user(u):
        with lock:
            key = u["username"].lower()
            if key in kol_index:
                if current["kw"] not in kol_index[key]["keywords"]:
                    kol_index[key]["keywords"].append(current["kw"])
            else:
                u["keywords"] = [current["kw"]]
                kol_index[key] = u
                kol["users"].append(u)

    def on_tick(scrolls, total, idle):
        with lock:
            kol.update(scrolls=scrolls, idle=idle)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(locale="en-US")
            x_cookies.apply_to_context(context)
            for i, kw in enumerate(keywords):
                if kol_stop.is_set():
                    break
                current["kw"] = kw
                with lock:
                    kol.update(keyword=kw, kw_index=i, scrolls=0, idle=0)
                try:
                    crawl_kol.crawl_keyword(context, kw, latest, max_users, on_user=on_user,
                                            on_tick=on_tick, should_stop=kol_stop.is_set)
                except Exception as e:
                    with lock:
                        kol["error"] += f"[{kw}] {e} "
            browser.close()
    except Exception as e:
        with lock:
            kol["error"] += str(e)
    finally:
        with lock:
            kol.update(running=False, kw_index=len(keywords))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200, extra=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/":
            self.send(INDEX.read_bytes(), "text/html")
        elif self.path == "/api/status":
            with lock:
                self.send(json.dumps({**state, "cookie": cookie_status()}))
        elif self.path == "/api/kol/status":
            with lock:
                self.send(json.dumps(kol))
        elif self.path == "/api/kol/export.csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, KOL_COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows([{**u, "keywords": ", ".join(u["keywords"])} for u in kol["users"]])
            self.send(buf.getvalue(), "text/csv", extra={"Content-Disposition": "attachment; filename=kol_users.csv"})
        elif self.path == "/api/export.csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, ["kol", "telegram", "source", "x_url", "count"])
            w.writeheader()
            w.writerows(state["rows"])
            self.send(buf.getvalue(), "text/csv", extra={"Content-Disposition": "attachment; filename=telegram_links.csv"})
        else:
            self.send("not found", "text/plain", 404)

    def do_POST(self):
        data = self.body()
        if self.path == "/api/cookies":
            cookies = x_cookies.parse_cookie_string(data.get("raw", ""))
            miss = [k for k in x_cookies.REQUIRED if k not in cookies]
            if miss:
                return self.send(json.dumps({"error": "Thiếu cookie bắt buộc: " + ", ".join(miss)}), code=400)
            x_cookies.save(cookies)
            x_cookies.META_FILE.unlink(missing_ok=True)
            resolve_in_background()
            self.send(json.dumps({"ok": True}))
        elif self.path == "/api/cookies/logout":
            x_cookies.clear()
            self.send(json.dumps({"ok": True}))
        elif self.path == "/api/kol/start":
            keywords = list(dict.fromkeys(k.strip() for k in data.get("keywords", "").replace(",", "\n").splitlines() if k.strip()))
            if not keywords:
                return self.send(json.dumps({"error": "Chưa nhập từ khoá"}), code=400)
            if not cookie_status()["ok"]:
                return self.send(json.dumps({"error": "Cần import cookie trước"}), code=400)
            with lock:
                if kol["running"]:
                    return self.send(json.dumps({"error": "Đang chạy"}), code=409)
                kol.update(running=True, keyword="", keywords=keywords, kw_index=0, scrolls=0, idle=0, users=[], error="")
                kol_index.clear()
            kol_stop.clear()
            threading.Thread(target=kol_job, daemon=True, args=(
                keywords, data.get("latest", True), int(data.get("max_users", 500)))).start()
            self.send(json.dumps({"ok": True}))
        elif self.path == "/api/kol/stop":
            kol_stop.set()
            self.send(json.dumps({"ok": True}))
        elif self.path == "/api/crawl":
            users = crawl.parse_users(data.get("users", "").split())
            if not users:
                return self.send(json.dumps({"error": "Chưa nhập KOL nào"}), code=400)
            if not cookie_status()["ok"]:
                return self.send(json.dumps({"error": "Cần import cookie trước"}), code=400)
            with lock:
                if state["running"]:
                    return self.send(json.dumps({"error": "Đang chạy"}), code=409)
                state.update(running=True, users=users, done=[], log=[], rows=[])
            threading.Thread(target=job, args=(users, int(data.get("max_scrolls", 10))), daemon=True).start()
            self.send(json.dumps({"ok": True}))
        else:
            self.send("not found", "text/plain", 404)


if __name__ == "__main__":
    print(f"Mở http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
