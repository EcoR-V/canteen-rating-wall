# -*- coding: utf-8 -*-
"""从 Wikimedia Commons 下载自由版权的菜品实拍图，分配给 30 个招牌菜"""
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "uploads")
UA = "CanteenRatingDemo/1.0 (educational project; contact: student@example.edu)"

# (菜品 id, 搜索词)
TARGETS = [
    (1,   "Lanzhou beef noodle soup"),
    (4,   "braised chicken rice Chinese"),
    (7,   "stir fried pork with chili"),
    (10,  "Chinese dumplings jiaozi plate"),
    (16,  "egg fried rice plate"),
    (19,  "char siu rice barbecue pork"),
    (22,  "century egg pork congee"),
    (25,  "tomato scrambled egg Chinese dish"),
    (31,  "malatang spicy hot pot bowl"),
    (34,  "bibimbap stone bowl"),
    (37,  "luosifen river snail rice noodle"),
    (40,  "lotus root pork rib soup"),
    (43,  "double skin milk dessert"),
    (46,  "chicken burger and fries"),
    (49,  "yangrou paomo lamb soup bread"),
    (55,  "seafood congee crab shrimp"),
    (58,  "sichuan boiled fish chili"),
    (61,  "noodles wonton soup bowl"),
    (64,  "braised pork trotter rice"),
    (67,  "beef soup with flatbread"),
    (73,  "Chinese canteen food tray rice dishes"),
    (76,  "char siu scrambled egg rice"),
    (79,  "Korean fried chicken glazed"),
    (82,  "wonton soup bowl"),
    (85,  "roujiamo Chinese meat sandwich"),
    (100, "crossing the bridge rice noodles"),
    (106, "pad kra pao rice fried egg"),
    (121, "beef noodle soup bowl"),
    (127, "red braised pork belly"),
    (175, "toast bread slices breakfast"),
]

BAD_WORDS = ("logo", "map", "sign", "chart", "diagram", "menu", "poster", "monument",
             "building", "street", "people", "portrait", "museum", "packaging")


def api(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def search(term, limit=10):
    d = api({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": term, "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": 1280,
    })
    pages = (d.get("query") or {}).get("pages") or {}
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        title = p.get("title", "")
        w, h = ii.get("width", 0), ii.get("height", 0)
        if not ii.get("thumburl") or w < 800 or h < 520:
            continue
        if w / max(h, 1) < 1.05:
            continue
        low = title.lower()
        if any(b in low for b in BAD_WORDS):
            continue
        out.append((title, ii["thumburl"], w, h))
    return out


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if len(data) < 15000:
        return 0
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def main():
    conn = sqlite3.connect(os.path.join(BASE, "canteen.db"))
    ok = fail = 0
    limit = int(os.environ.get("LIMIT", "0") or len(TARGETS))
    for did, term in TARGETS[:limit]:
        name = conn.execute("SELECT name FROM dishes WHERE id=?", (did,)).fetchone()
        if not name:
            print("  skip", did)
            continue
        fname = "photo-%d.jpg" % did
        dest = os.path.join(OUT, fname)
        got = False
        try:
            cands = search(term)
        except Exception as e:
            print("  搜索失败 %-18s %s" % (name[0][:16], e))
            fail += 1
            continue
        for title, url, w, h in cands[:3]:
            try:
                size = download(url, dest)
                if size:
                    conn.execute("UPDATE dishes SET photo=? WHERE id=?", (fname, did))
                    print("  ✓ %-18s %6dpx  %s" % (name[0][:16], w, title[5:50]))
                    ok += 1
                    got = True
                    break
            except Exception:
                continue
            time.sleep(0.3)
        if not got:
            print("  ✗ %-18s 没找到合适图片（%s）" % (name[0][:16], term))
            fail += 1
        time.sleep(0.6)
    conn.commit()
    conn.close()
    print("\n成功 %d 张，失败 %d 张" % (ok, fail))


if __name__ == "__main__":
    main()
