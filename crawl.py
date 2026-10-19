"""Tìm channel/group Telegram từ trang X của các KOL.

    python crawl.py nivradotfun ArtPlatformX MeowCoin777
    python crawl.py -f kols.txt --max-scrolls 15 --show

Nguồn quét: bio, tweet ghim/tweet trên trang chủ, tab Replies (comment của chính KOL),
tweet dài (note_tweet, không cần bấm Show more), link card, và các trang link-in-bio
(linktr.ee, bio.link...). Dữ liệu lấy thẳng từ GraphQL JSON mà X trả về nên
link đã được expand sẵn, không phải qua t.co.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

import x_cookies

OUT_DIR = Path(__file__).parent / "output"
RESERVED = {"share", "addstickers", "proxy", "socks", "setlanguage", "iv", "login",
            "addtheme", "addemoji", "boost", "contact", "bg", "invoice", "addlist"}
TG_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/"
    r"@?(joinchat/[\w-]+|c/\d+(?:/\d+)?|s/[A-Za-z]\w{3,}|\+[\w-]+|[A-Za-z]\w{3,})",
    re.I,
)
TG_SCHEME_RE = re.compile(r"tg://(?:resolve\?domain=([A-Za-z]\w{3,})|join\?invite=([\w-]+))", re.I)
LINKINBIO = ("linktr.ee", "bio.link", "beacons.ai", "lnk.bio", "carrd.co", "linktree.com",
             "bit.ly", "tinyurl.com", "linkin.bio", "solo.to", "taplink.cc", "allmylinks.com")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def normalize(m: str) -> str | None:
    m = m.rstrip("/")
    if m.startswith("s/"):
        m = m[2:]
    first = m.split("/")[0].lower()
    if first in RESERVED:
        return None
    if m.startswith("joinchat/") or m.startswith("+"):
        return "t.me/" + m
    if m.startswith("c/"):
        return "t.me/" + "/".join(m.split("/")[:2])
    return "t.me/" + m


def find_tg(s: str):
    s = s.replace("\\/", "/")
    for m in TG_RE.finditer(s):
        if s[m.end():m.end() + 1] == "…":  # display_url bị cắt
            continue
        link = normalize(m.group(1))
        if link:
            yield link
    for m in TG_SCHEME_RE.finditer(s):
        yield "t.me/" + (m.group(1) or "+" + m.group(2))


def walk(node, ctx, target, hits, others):
    """Duyệt JSON, ctx = (kind, tweet_id, screen_name) của tweet/user gần nhất."""
    if isinstance(node, dict):
        legacy = node.get("legacy") if isinstance(node.get("legacy"), dict) else {}
        if node.get("rest_id") and "full_text" in legacy:
            u = (node.get("core", {}).get("user_results", {}).get("result") or {})
            name = (u.get("core", {}).get("screen_name") or u.get("legacy", {}).get("screen_name") or "")
            ctx = ("tweet", node["rest_id"], name.lower())
        elif node.get("__typename") == "User":
            name = node.get("core", {}).get("screen_name") or legacy.get("screen_name") or ""
            ctx = ("bio", None, name.lower())
        for v in node.values():
            walk(v, ctx, target, hits, others)
    elif isinstance(node, list):
        for v in node:
            walk(v, ctx, target, hits, others)
    elif isinstance(node, str) and ctx:
        kind, tid, author = ctx
        if author != target:
            return
        for link in find_tg(node):
            hits.setdefault((link, kind, tid), None)
        if kind == "bio":
            for u in URL_RE.findall(node):
                if any(d in u for d in LINKINBIO):
                    others.add(u)


def scroll_tab(page, url, responses, max_scrolls):
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    stale = 0
    last = 0
    for _ in range(max_scrolls):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(1800)
        n = len(responses)
        stale = stale + 1 if n == last else 0
        last = n
        if stale >= 3:
            break


def crawl_user(context, user, max_scrolls):
    target = user.lower().lstrip("@")
    responses: list[str] = []
    page = context.new_page()

    def on_resp(r):
        if "/i/api/graphql/" in r.url:
            try:
                responses.append(r.text())
            except Exception:
                pass

    page.on("response", on_resp)
    for tab in ("", "/with_replies"):
        scroll_tab(page, f"https://x.com/{target}{tab}", responses, max_scrolls)
    hits: dict = {}
    others: set[str] = set()
    for body in responses:
        try:
            walk(json.loads(body), None, target, hits, others)
        except Exception:
            continue
    page.close()

    # trang link-in-bio: mở và tìm link t.me
    for url in others:
        p = context.new_page()
        try:
            p.goto(url, wait_until="domcontentloaded", timeout=20000)
            p.wait_for_timeout(2500)
            html = p.content() + " " + p.url
            for link in find_tg(html):
                hits.setdefault((link, f"linkinbio:{url}", None), None)
        except Exception:
            pass
        p.close()

    agg: dict = {}
    for (link, kind, tid) in hits:
        src = f"https://x.com/{target}/status/{tid}" if tid else f"https://x.com/{target}"
        r = agg.setdefault(link, {"kol": target, "telegram": link, "source": kind, "x_url": src, "count": 0})
        r["count"] += 1
        if kind == "bio":  # ưu tiên bio làm nguồn đại diện
            r["source"], r["x_url"] = kind, src
    return list(agg.values())


def parse_users(items) -> list[str]:
    users = [re.sub(r"^https?://(?:www\.)?(?:x|twitter)\.com/", "", u.strip()).split("/")[0].split("?")[0].lstrip("@")
             for u in items]
    return list(dict.fromkeys(u for u in users if u))


def run_crawl(users, max_scrolls=10, headless=True, on_user=None):
    """Crawl lần lượt từng KOL. on_user(user, rows|None, error|None) gọi sau mỗi KOL."""
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="en-US")
        x_cookies.apply_to_context(context)
        for u in users:
            try:
                rows = crawl_user(context, u, max_scrolls)
                err = None
            except Exception as e:
                rows, err = [], str(e)
            all_rows += rows
            if on_user:
                on_user(u, rows, err)
        browser.close()
    return all_rows


def write_csv(rows, path=None):
    OUT_DIR.mkdir(exist_ok=True)
    path = path or OUT_DIR / "telegram_links.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, ["kol", "telegram", "source", "x_url", "count"])
        w.writeheader()
        w.writerows(rows)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("users", nargs="*")
    ap.add_argument("-f", "--file", help="file mỗi dòng 1 handle hoặc URL x.com")
    ap.add_argument("--max-scrolls", type=int, default=10)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    users = list(args.users)
    if args.file:
        users += Path(args.file).read_text().split()
    users = parse_users(users)
    if not users:
        sys.exit("Chưa có KOL nào.")

    def show(u, rows, err):
        print(f"@{u}: " + (f"lỗi {err}" if err else f"{len({r['telegram'] for r in rows})} link telegram"))
        for r in rows:
            print(f"   {r['telegram']:<40} {r['source']:<10} x{r['count']}  {r['x_url']}")

    rows = run_crawl(users, args.max_scrolls, not args.show, show)
    print(f"Đã ghi {len(rows)} dòng vào {write_csv(rows)}")


if __name__ == "__main__":
    main()
