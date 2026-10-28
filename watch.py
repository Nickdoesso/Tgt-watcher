#!/usr/bin/env python3
"""TGT Deal Watcher"""

import json
import os
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN   = "9009311645:AAEQsuOHAacPg85bd8TSCe5ZLpezFQth2EM"
TELEGRAM_CHAT_ID = "5217009434"

MAX_PRICE  = 500
MIN_PROFIT = 200
PAGES      = 2

CATS = [
    ("900", "Handguns-for-Sale-Pistols-for-Sale", "Handgun"),
    ("948", "Rifles-for-Sale-Semi-Auto-for-Sale", "Rifle"),
]

NEAR = {
    "georgetown", "round rock", "cedar park", "leander", "hutto", "taylor",
    "pflugerville", "austin", "liberty hill", "jarrell", "florence", "granger",
    "bartlett", "schwertner", "salado", "brushy creek", "jollyville",
    "wells branch", "north austin", "-", "",
}

BASE      = "https://texasguntrader.com"
SEEN_FILE = "seen.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")

ID_RE = re.compile(r"/listings/(\d+)")
HDR   = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}


def page_url(cat, slug, page):
    if page == 1:
        return f"{BASE}/GUNS-FOR-SALE/{cat}/{slug}.html"
    return f"{BASE}/category/{cat}/{slug}/{page}.html"


def get(url):
    enc = quote(url, safe="")
    tries = [
        ("corsproxy", "https://corsproxy.io/?url=" + enc),
        ("corslol", "https://api.cors.lol/?url=" + enc),
        ("corsfix", "https://proxy.corsfix.com/?" + url),
        ("thingproxy", "https://thingproxy.freeboard.io/fetch/" + url),
        ("allorigins", "https://api.allorigins.win/raw?url=" + enc),
        ("codetabs", "https://api.codetabs.com/v1/proxy?quest=" + enc),
        ("jina", "https://r.jina.ai/" + url),
        ("direct", url),
    ]
    for label, u in tries:
        try:
            r = requests.get(u, headers=HDR, timeout=30)
            body = r.text or ""
            ok = r.status_code == 200 and "/listings/" in body
            print(f"[{label}] {r.status_code} len={len(body)} ok={ok}")
            if ok:
                return body
        except requests.RequestException as e:
            print(f"[{label}-err] {str(e)[:80]}")
        time.sleep(1)
    return ""


def parse(html, catname):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=ID_RE)
        if not a:
            continue
        m = ID_RE.search(a["href"])
        if not m:
            continue
        if "sold.gif" in str(tr):
            continue
        tds = tr.find_all("td")
        texts = [" ".join(td.get_text(" ", strip=True).split()) for td in tds]
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 4:
            continue
        city = texts[3] if len(texts) > 3 else ""
        praw = texts[4] if len(texts) > 4 else ""
        price = None
        if praw.startswith("$"):
            try:
                price = int(float(praw.replace("$", "").replace(",", "")))
            except ValueError:
                price = None
        out[m.group(1)] = {"id": m.group(1), "title": title,
                           "city": city, "price": price, "cat": catname}
    return out


def est_resale(p):
    if p is None:
        return None
    if p <= 250:
        return 450
    if p <= 350:
        return 600
    if p <= 450:
        return 700
    if p <= 500:
        return 800
    return None


def send(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": "false"},
            timeout=20)
        print(f"[telegram] {r.status_code}")
    except Exception as e:
        print(f"[telegram-err] {e}")


def main():
    print("[start]")
    seen = {}
    if os.path.exists(SEEN_FILE):
        try:
            seen = json.load(open(SEEN_FILE))
        except Exception:
            seen = {}

    alerts = []
    scanned = 0

    for cat, slug, catname in CATS:
        for p in range(1, PAGES + 1):
            html = get(page_url(cat, slug, p))
            if not html:
                print(f"[skip] {cat} p{p}")
                continue
            rows = parse(html, catname)
            scanned += len(rows)
            print(f"[parse] {cat} p{p} rows={len(rows)}")

            for lid, it in rows.items():
                if lid in seen:
                    continue
                seen[lid] = {"price": it["price"]}
                if it["city"].lower() not in NEAR:
                    continue
                link = f"{BASE}/print/{lid}/item.html"
                if it["price"] is None:
                    alerts.append(f"❓ NO PRICE — {catname}\n{it['title']}\n"
                                  f"{it['city']}\n{link}")
                    continue
                if it["price"] > MAX_PRICE:
                    continue
                res = est_resale(it["price"])
                prof = (res - it["price"]) if res else 0
                if res and prof < MIN_PROFIT:
                    continue
                tag = "🔥" if prof >= 250 else "✅"
                alerts.append(f"{tag} DEAL — {catname}\n{it['title']}\n"
                              f"{it['city']} · ask ${it['price']} → est ${res} (~${prof})\n{link}")

    print(f"[scan] rows={scanned}")
    if scanned == 0:
        send("⚠️ Ran, 0 rows — still blocked.")

    for m in alerts[:25]:
        send(m)
        time.sleep(1)

    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=1, sort_keys=True)
    print(f"[done] {len(alerts)}")


if __name__ == "__main__":
    main()
