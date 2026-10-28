#!/usr/bin/env python3
"""TGT Deal Watcher - patchright headful build"""

import json
import os
import re
import time
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from patchright.sync_api import sync_playwright, TimeoutError as PWTimeout

TELEGRAM_TOKEN   = "9009311645:AAEQsuOHAacPg85bd8TSCe5ZLpezFQth2EM"
TELEGRAM_CHAT_ID = "5217009434"

ZIP            = "78626"
DISTANCE_MILES = 20
MAX_PRICE      = 500
MIN_PROFIT     = 200

CATEGORIES = {
    "900": ("Handguns-Pistols", "Handgun/Pistol"),
    "948": ("Rifles-Semi-Auto", "Rifle (semi-auto)"),
}

BASE      = "https://texasguntrader.com"
SEEN_FILE = "seen.json"
PAGES     = 1

ID_RE    = re.compile(r"/listings/(\d+)")
PRINT_RE = re.compile(r"/print/(\d+)")
PRICE_RE = re.compile(r"\$\s?([\d][\d,]*)")


def candidate_urls(cat, slug, page):
    q = [
        ("b[by_price_lower]", "0"),
        ("b[by_price_higher]", str(MAX_PRICE)),
        ("b[by_zip_code]", ZIP),
        ("b[by_zip_code_distance]", str(DISTANCE_MILES)),
        ("order", "4"),
        ("page", str(page)),
    ]
    return [
        f"{BASE}/category/{cat}/{slug}/listings?" + urlencode(q),
        f"{BASE}/index.php?a=2&" + urlencode(dict(q + [("c", cat)])),
    ]


def fetch_all(urls):
    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chromium",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            locale="en-US",
            timezone_id="America/Chicago",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        for url in urls:
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                code = resp.status if resp else 0
                html, ok = "", False
                for i in range(60):
                    html = page.content()
                    if "/listings/" in html or "/print/" in html:
                        ok = True
                        break
                    if i == 8:
                        for fr in page.frames:
                            try:
                                box = fr.locator("input[type=checkbox]")
                                if box.count():
                                    box.first.click(timeout=2000)
                                    print("[pw] clicked turnstile")
                                    break
                            except Exception:
                                pass
                    page.wait_for_timeout(1000)
                print(f"[pw] {code} ok={ok} title={page.title()[:70]!r}")
                if ok:
                    out[url] = html
            except PWTimeout:
                print(f"[pw-timeout] {url}")
            except Exception as e:
                print(f"[pw-error] {e}")
        browser.close()
    return out


def container_text(a, levels=2):
    node = a
    for _ in range(levels):
        if node.parent:
            node = node.parent
    return " ".join(node.get_text(" ", strip=True).split())


def add_hit(found, lid, title, blob, catname):
    title = " ".join(title.split())
    if len(title) < 3:
        return
    pm = PRICE_RE.search(blob)
    price = int(pm.group(1).replace(",", "")) if pm else None
    if lid not in found:
        found[lid] = {"id": lid, "title": title, "price": price, "cat": catname}
    else:
        e = found[lid]
        if price and not e["price"]:
            e["price"] = price
        if len(title) > len(e["title"]):
            e["title"] = title


def parse_search(html, catname):
    found = {}
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        m = ID_RE.search(a["href"]) or PRINT_RE.search(a["href"])
        if not m:
            continue
        add_hit(found, m.group(1), a.get_text(" ", strip=True),
                container_text(a), catname)
    return found


def est_resale(price):
    if price is None:
        return None
    if price <= 250:
        return 450
    if price <= 350:
        return 600
    if price <= 450:
        return 700
    if price <= 500:
        return 800
    return None


def send_telegram(text):
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        }, timeout=20)
        print(f"[telegram] {r.status_code}")
    except Exception as e:
        print(f"[telegram-error] {e}")


def main():
    print("[start]")
    seen = {}
    if os.path.exists(SEEN_FILE):
        try:
            seen = json.load(open(SEEN_FILE))
        except Exception:
            seen = {}

    jobs = []
    for cat, (slug, catname) in CATEGORIES.items():
        for page in range(1, PAGES + 1):
            jobs.append((cat, catname, candidate_urls(cat, slug, page)))

    fetched = fetch_all([u for _, _, urls in jobs for u in urls])

    alerts = []
    scanned = 0

    for cat, catname, urls in jobs:
        html = ""
        for u in urls:
            if u in fetched:
                html = fetched[u]
                break
        if not html:
            print(f"[skip] blocked cat={cat}")
            continue

        items = parse_search(html, catname)
        scanned += len(items)
        print(f"[parse] cat={cat} items={len(items)}")

        for lid, item in items.items():
            price = item["price"]
            if lid in seen:
                continue
            seen[lid] = {"price": price}
            link = f"{BASE}/print/{lid}/item.html"

            if price is None:
                alerts.append(f"❓ NO PRICE — {catname}\n{item['title']}\n{link}")
                continue
            if price > MAX_PRICE:
                continue
            resale = est_resale(price)
            prof = (resale - price) if resale else 0
            if resale and prof < MIN_PROFIT:
                continue
            tag = "🔥" if prof >= 250 else "✅"
            alerts.append(
                f"{tag} DEAL — {catname}\n{item['title']}\n"
                f"Ask ${price} → est ${resale} (~${prof})\n{link}"
            )

    print(f"[scan] listings = {scanned}")

    if scanned == 0:
        send_telegram("⚠️ Ran, found 0 listings.")

    for msg in alerts[:25]:
        send_telegram(msg)
        time.sleep(1)

    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=1, sort_keys=True)
    print(f"[done] {len(alerts)} alert(s)")


if __name__ == "__main__":
    main()
