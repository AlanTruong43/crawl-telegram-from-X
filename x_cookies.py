"""Import cookies của tài khoản X và nạp vào Playwright.

Dùng:
    python x_cookies.py import            # dán chuỗi cookie rồi Ctrl-D
    python x_cookies.py import -f raw.txt # đọc từ file
    python x_cookies.py check             # mở x.com/home kiểm tra đã đăng nhập chưa

Chuỗi đầu vào có thể là `user|pass|name=value; name=value; ...` hoặc chỉ phần cookie.
"""
import argparse
import json
import re
import sys
from pathlib import Path

COOKIE_FILE = Path(__file__).parent / "x_cookies.json"
META_FILE = Path(__file__).parent / "x_account.json"
REQUIRED = ("auth_token", "ct0")


def parse_cookie_string(raw: str) -> dict[str, str]:
    raw = raw.strip()
    # bỏ phần user|pass| phía trước: bắt đầu từ đoạn đầu tiên có dạng name=value
    # (không cắt theo dấu | cuối vì giá trị cookie như external_referer cũng chứa |)
    parts = raw.split("|")
    start = next((i for i, p in enumerate(parts) if re.match(r"\s*[\w.\-]+=", p)), 0)
    cookie = "|".join(parts[start:])
    cookies: dict[str, str] = {}
    for part in cookie.split(";"):
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if sep and name and value:
            cookies[name] = value
    return cookies


def to_playwright(cookies: dict[str, str]) -> list[dict]:
    return [
        {
            "name": k,
            "value": v,
            "domain": ".x.com",
            "path": "/",
            "secure": True,
            "httpOnly": k == "auth_token",
            "sameSite": "None",
        }
        for k, v in cookies.items()
    ]


def save(cookies: dict[str, str]) -> None:
    COOKIE_FILE.write_text(json.dumps(to_playwright(cookies), indent=2))
    COOKIE_FILE.chmod(0o600)


def load() -> list[dict]:
    if not COOKIE_FILE.exists():
        sys.exit(f"Chưa có {COOKIE_FILE.name}; chạy `python x_cookies.py import` trước.")
    return json.loads(COOKIE_FILE.read_text())


def apply_to_context(context) -> None:
    """Nạp cookie đã lưu vào BrowserContext của Playwright."""
    context.add_cookies(load())


def resolve_account() -> dict | None:
    """Mở x.com/home bằng cookie đã lưu để đọc username đang đăng nhập."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(locale="en-US")
            apply_to_context(context)
            page = context.new_page()
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            link = page.wait_for_selector('[data-testid="AppTabBar_Profile_Link"]', timeout=20000)
            username = link.get_attribute("href").strip("/")
            name = ""
            try:
                name = page.locator('[data-testid="SideNav_AccountSwitcher_Button"]').inner_text(timeout=3000).split("\n")[0]
            except Exception:
                pass
            info = {"username": username, "name": name}
            META_FILE.write_text(json.dumps(info))
            return info
        except Exception:
            return None
        finally:
            browser.close()


def clear() -> None:
    COOKIE_FILE.unlink(missing_ok=True)
    META_FILE.unlink(missing_ok=True)


def cmd_import(args) -> None:
    raw = Path(args.file).read_text() if args.file else sys.stdin.read()
    cookies = parse_cookie_string(raw)
    missing = [k for k in REQUIRED if k not in cookies]
    if missing:
        sys.exit(f"Thiếu cookie bắt buộc: {', '.join(missing)}")
    save(cookies)
    print(f"Đã lưu {len(cookies)} cookie vào {COOKIE_FILE.name}")


def cmd_check(args) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        context = browser.new_context()
        apply_to_context(context)
        page = context.new_page()
        page.goto("https://x.com/home", wait_until="domcontentloaded")
        try:
            page.wait_for_selector('[data-testid="primaryColumn"]', timeout=15000)
            ok = "/login" not in page.url and "/i/flow" not in page.url
        except Exception:
            ok = False
        browser.close()
    print("Đăng nhập OK" if ok else "Cookie không hợp lệ hoặc đã hết hạn")
    sys.exit(0 if ok else 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("-f", "--file")
    imp.set_defaults(fn=cmd_import)
    chk = sub.add_parser("check")
    chk.add_argument("--show", action="store_true", help="hiện cửa sổ trình duyệt")
    chk.set_defaults(fn=cmd_check)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
