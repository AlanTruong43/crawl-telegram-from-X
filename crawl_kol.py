"""Tìm KOL theo từ khoá: lướt kết quả tìm kiếm X và gom username của người đăng bài.

    python crawl_kol.py crypto --max-users 300
"""
import argparse
import json
from urllib.parse import quote

from playwright.sync_api import sync_playwright

import x_cookies


def iter_authors(node):
    """Sinh (user_result, tweet_id, full_text) cho mọi tweet trong JSON."""
    if isinstance(node, dict):
        legacy = node.get("legacy") if isinstance(node.get("legacy"), dict) else {}
        if node.get("rest_id") and "full_text" in legacy:
            u = node.get("core", {}).get("user_results", {}).get("result")
            if isinstance(u, dict):
                text = (node.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {}).get("text")
                        or legacy["full_text"])
                yield u, node["rest_id"], text
        for v in node.values():
            yield from iter_authors(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_authors(v)


def to_user(u, tweet_id, text):
    legacy = u.get("legacy", {})
    core = u.get("core", {})
    name = core.get("screen_name") or legacy.get("screen_name")
    if not name:
        return None
    return {
        "username": name,
        "name": core.get("name") or legacy.get("name") or "",
        "followers": u.get("relationship_counts", {}).get("followers", legacy.get("followers_count", 0)),
        "verified": bool(u.get("is_blue_verified")),
        "bio": u.get("profile_bio", {}).get("description") or legacy.get("description", ""),
        "tweet_url": f"https://x.com/{name}/status/{tweet_id}",
        "tweet": text[:200],
    }


def crawl_keyword(context, keyword, latest=True, max_users=500, max_idle=12,
                  on_user=None, on_tick=None, should_stop=lambda: False, log=lambda *a: None):
    """Lướt liên tục kết quả tìm kiếm tới khi hết bài mới / đủ max_users / bị dừng.

    on_user(user_dict) gọi cho mỗi username mới; on_tick(scrolls, total, idle) mỗi lần lướt.
    Trả về list user.
    """
    users: dict[str, dict] = {}
    page = context.new_page()

    def on_resp(r):
        if "SearchTimeline" not in r.url:
            return
        try:
            body = json.loads(r.text())
        except Exception:
            return
        for u, tid, text in iter_authors(body):
            info = to_user(u, tid, text)
            if info and info["username"].lower() not in users:
                users[info["username"].lower()] = info
                if on_user:
                    on_user(info)

    page.on("response", on_resp)
    url = f"https://x.com/search?q={quote(keyword)}&src=typed_query" + ("&f=live" if latest else "")
    log("info", f"Mở {url}")
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    try:
        page.wait_for_selector('[data-testid="SideNav_AccountSwitcher_Button"]', timeout=8000)
    except Exception:
        log("error", "Không thấy giao diện đã đăng nhập - cookie hết hạn, không hợp lệ hoặc chưa nhập cookie")

    idle = scrolls = 0
    while idle < max_idle and len(users) < max_users and not should_stop():
        before = len(users)
        page.mouse.wheel(0, 5000)
        page.keyboard.press("End")
        page.wait_for_timeout(1500)
        # X đôi khi báo lỗi tạm thời -> bấm Retry
        retry = page.get_by_role("button", name="Retry")
        if retry.count():
            try:
                retry.first.click(timeout=1000)
                log("warn", "X báo lỗi tạm thời (có thể bị giới hạn tốc độ) - đã bấm Retry")
                page.wait_for_timeout(2500)
            except Exception:
                pass
        scrolls += 1
        idle = 0 if len(users) > before else idle + 1
        log("debug", f"  cuộn {scrolls}: +{len(users) - before} user mới (tổng {len(users)})"
            + (f", {idle}/{max_idle} lần không có bài mới" if idle else ""))
        if on_tick:
            on_tick(scrolls, len(users), idle)
    if should_stop():
        reason = "bị dừng thủ công"
    elif len(users) >= max_users:
        reason = f"đủ {max_users} user"
    else:
        reason = f"{max_idle} lần cuộn liên tiếp không có bài mới (hết kết quả hoặc X ngừng trả thêm)"
    log("ok", f"Xong \"{keyword}\": {len(users)} user - dừng vì {reason}")
    page.close()
    return list(users.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("--max-users", type=int, default=200)
    ap.add_argument("--top", action="store_true", help="tab Top thay vì Latest")
    args = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="en-US")
        x_cookies.apply_to_context(context)
        res = crawl_keyword(context, args.keyword, not args.top, args.max_users,
                            on_user=lambda u: print(f"@{u['username']:<20} {u['followers']:>9} followers"))
        browser.close()
    print(f"Tổng {len(res)} username")


if __name__ == "__main__":
    main()
