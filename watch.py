#!/usr/bin/env python3
"""
TGT Deal Watcher
Watches TexasGunTrader for cheap guns near Georgetown TX (78626) and texts you on Telegram.
Criteria: max $500, within 20 miles, handguns + semi-auto rifles.
"""

import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# ---------------- YOUR CREDENTIALS ----------------
TELEGRAM_TOKEN   = "9046847877:AAHKQyLh0SMqj0L74ozN0HU39z8C6w4L0Yc"
TELEGRAM_CHAT_ID = "5217009434"

# ---------------- CRITERIA ----------------
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
PAGES     = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept-Language": "en-US,en;q=0.9",
}

ID_RE    = re.compile(r"/listings/(\d+)")
PRINT_RE = re.compile(r"/print/(\d+)")
PRICE_RE = re.compile(r"\$\s?([\d][\d,]*)")


# ---------------- HELPERS ----------------
def get(url, tries=3):
    for _ in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code == 200:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(2)
    return ""


def container_text(a, levels=2):
    node = a
    for _ in range(levels):
        if node.parent:
            node = node.parent
    return " ".join(node.get_text(" ", strip=True).split())


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


def parse_search(html, catname):
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = ID_RE.search(href) or PRINT_RE.search(href)
        if not m:
            continue
        lid = m.group(1)
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 3:
            continue
        blob = container_text(a)
        pm = PRICE_RE.search(blob)
        price = int(pm.group(1).replace(",", "")) if pm else None

        if lid not in found:
            found[lid] = {"id": lid, "title": title, "blob": blob,
                          "price": price, "cat": catname}
        else:
            e = found[lid]
            if len(blob) > len(e["blob"]):
                e["blob"] = blob
                if price:
                    e["price"] = price
            if len(title) > len(e["title"]):
                e["title"] = title
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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        }, timeout=20)
    except requests.RequestException as e:
        print("telegram error:", e)


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=1, sort_keys=True)


# ---------------- MAIN ----------------
def main():
    first_run = not os.path.exists(SEEN_FILE)
    seen = load_seen()
    alerts = []

    for cat, (slug, catname) in CATEGORIES.items():
        for page in range(1, PAGES + 1):
            html = ""
            for url in candidate_urls(cat, slug, page):
                html = get(url)
                if html and ("/listings/" in html or "/print/" in html):
                    break
            if not html:
                continue

            for lid, item in parse_search(html, catname).items():
                price = item["price"]
                prev = seen.get(lid)
                link = f"{BASE}/print/{lid}/item.html"

                # already alerted, no price change -> skip
                if prev is not None:
                    old = prev.get("price")
                    if price is not None and old is not None and price < old - 20:
                        resale = est_resale(price)
                        prof = f"\nEst. resale: ${resale}  →  ~${resale - price} profit" if resale else ""
                        alerts.append(
                            f"📉 <b>PRICE DROP — {catname}</b>\n"
                            f"{item['title']}\n"
                            f"Was ${old} → now <b>${price}</b>{prof}\n{link}"
                        )
                        seen[lid] = {"price": price, "t": datetime.utcnow().isoformat()}
                    continue

                # new listing
                seen[lid] = {"price": price, "t": datetime.utcnow().isoformat()}

                if price is None:
                    # "contact for price" — worth a lowball
                    alerts.append(
                        f"❓ <b>NO PRICE — {catname}</b>\n"
                        f"{item['title']}\n"
                        f"No price listed — worth a lowball bid\n{link}"
                    )
                    continue

                if price > MAX_PRICE:
                    continue

                resale = est_resale(price)
                prof = resale - price if resale else 0
                if resale and prof < MIN_PROFIT:
                    continue

                tag = "🔥" if prof >= 250 else "✅"
                prof_line = f"\nEst. resale: ${resale}  →  ~${prof} profit" if resale else ""
                alerts.append(
                    f"{tag} <b>NEW DEAL — {catname}</b>\n"
                    f"{item['title']}\n"
                    f"Ask: <b>${price}</b>{prof_line}\n{link}"
                )

    if first_run:
        send_telegram("✅ TGT watcher is LIVE. Watching TexasGunTrader every 5 min "
                      "for guns under $500 within 20 mi of 78626.")

    for msg in alerts[:25]:
        send_telegram(msg)
        time.sleep(1)

    save_seen(seen)
    print(f"done — {len(alerts)} alert(s)")


if __name__ == "__main__":
    main()
