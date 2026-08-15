"""中古機相場.com（p-souba）行情排行榜。

跟三家商店性質不同：這站不賣機器，是業者間的中古行情資訊站，
每週統計柏青哥／柏青嫂各前 100 名的成交行情。

一個必須知道的限制：**「價格」欄是會員限定**。未登入時伺服器根本不輸出
那個欄位（不是用 CSS 藏起來），所以自動排程抓不到價格，該欄會是 None。
排名、機種名、機種類型、本週漲跌額、上市週數則完全公開。

漲跌額其實比單一價格更有判斷價值：店家售價告訴你要付多少，
這裡的漲跌告訴你現在是不是好時機（正在漲就趁早，正在跌就再等）。
"""

import logging
import re

from .parse import soup_of

log = logging.getLogger(__name__)

BASE = "http://www.p-souba.com"

# 這站是 EUC-JP，而且沒在 HTTP header 宣告 charset。
# 自動偵測會猜成 Shift_JIS，解出來整頁亂碼 —— 「1位」「+3,000円」「90週目」
# 這些解析用的記號全部消失，於是安靜地抓到 0 筆。編碼寫死。
ENCODING = "euc-jp"
BOARDS = [
    ("pachinko", f"{BASE}/krank_1.htm", "パチンコ価格ランキング"),
    ("slot", f"{BASE}/krank_2.htm", "スロット価格ランキング"),
]

RANK_RE = re.compile(r"^(\d+)位$")
WEEKS_RE = re.compile(r"^(\d+)週目$")
YEN_RE = re.compile(r"円")
SIGNED_RE = re.compile(r"^[+\-±]")


def parse_ranking(html: str, kind: str) -> list:
    """解析排行榜表格。

    欄位位置會因為有沒有登入而位移（登入才會多一欄價格），
    所以不靠固定索引，改用內容特徵判斷：
      「12位」→ 排名 ／ 「90週目」→ 上市週數
      含「円」且開頭有 +- ± → 漲跌額
      含「円」但沒有正負號 → 價格（只有登入才會出現）
    """
    soup = soup_of(html)
    out = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if not any(YEN_RE.search(c) for c in cells):
            continue

        rank = weeks = price = delta = None
        leftovers = []
        for c in cells:
            m = RANK_RE.match(c)
            if m:
                rank = int(m.group(1))
                continue
            m = WEEKS_RE.match(c)
            if m:
                weeks = int(m.group(1))
                continue
            if YEN_RE.search(c):
                if SIGNED_RE.match(c):
                    delta = _to_int(c)
                else:
                    price = _to_int(c)
                continue
            if c:
                leftovers.append(c)

        if rank is None or len(leftovers) < 2:
            continue
        out.append({
            "kind": kind,
            "rank": rank,
            "maker": leftovers[0],
            "name": leftovers[1],
            "type": leftovers[2] if len(leftovers) > 2 else "",
            "price": price,          # 未登入時為 None
            "delta": delta,
            "weeks": weeks,
        })
    return out


def _to_int(text: str):
    m = re.search(r"([+\-]?)\s*([0-9][0-9,]*)", text)
    if not m:
        return 0 if "±" in text else None
    value = int(m.group(2).replace(",", ""))
    return -value if m.group(1) == "-" else value


def fetch_rankings(fetcher) -> tuple:
    """抓兩張排行榜。回傳 (資料列, 警告訊息)。"""
    rows, warnings = [], []
    fetcher.force_encoding = ENCODING
    for kind, url, label in BOARDS:
        html = fetcher.get(url)
        if not html:
            warnings.append(f"[p_souba] {label} 抓取失敗")
            continue
        parsed = parse_ranking(html, kind)
        if not parsed:
            # 解析失敗時把現場證據印出來，不要只留一句「版面可能改了」。
            # 上一次就是因為沒有這段，花了很久才發現真正的原因是編碼。
            log.warning("[p_souba] %s 解析不到資料：%d 位元組，<tr> %d 個，"
                        "含「位」%d 處，含「円」%d 處",
                        label, len(html), html.lower().count("<tr"),
                        html.count("位"), html.count("円"))
            log.warning("[p_souba] 前 200 字：%s", html[:200].replace("\n", " "))
            warnings.append(f"[p_souba] {label} 解析不到資料 — 版面可能改了")
            continue
        rows.extend(parsed)
        with_price = sum(1 for r in parsed if r["price"] is not None)
        log.info("[p_souba] %s：%d 筆（含價格 %d 筆）", label, len(parsed), with_price)

    if rows and not any(r["price"] is not None for r in rows):
        warnings.append(
            "[p_souba] 行情價欄位需要會員登入才會顯示，自動抓取取不到，"
            "比價表該欄會顯示「-」；漲跌額與排名不受影響"
        )
    return rows, warnings


def attach_to_comparison(comparison: list, rankings: list) -> int:
    """把行情資訊掛到比價表上。回傳成功對上的機種數。"""
    from .normalize import normalize

    index = {}
    for r in rankings:
        norm = normalize(r["name"])
        if norm["key"]:
            index.setdefault(norm["key"], r)

    matched = 0
    for row in comparison:
        key = normalize(row["name"])["key"]
        hit = index.get(key)
        if hit is None:
            # 排行榜的寫法可能多／少幾個字，退一步做包含比對
            for k, r in index.items():
                if k and key and (k in key or key in k) and abs(len(k) - len(key)) <= 6:
                    hit = r
                    break
        if hit:
            matched += 1
            row["souba"] = {
                "rank": hit["rank"],
                "delta": hit["delta"],
                "weeks": hit["weeks"],
                "type": hit["type"],
                "price": hit["price"],
            }
        else:
            row["souba"] = None
    return matched
"""中古機相場.com（p-souba）行情排行榜。

跟三家商店性質不同：這站不賣機器，是業者間的中古行情資訊站，
每週統計柏青哥／柏青嫂各前 100 名的成交行情。

一個必須知道的限制：**「價格」欄是會員限定**。未登入時伺服器根本不輸出
那個欄位（不是用 CSS 藏起來），所以自動排程抓不到價格，該欄會是 None。
排名、機種名、機種類型、本週漲跌額、上市週數則完全公開。

漲跌額其實比單一價格更有判斷價值：店家售價告訴你要付多少，
這裡的漲跌告訴你現在是不是好時機（正在漲就趁早，正在跌就再等）。
"""

import logging
import re

from .parse import soup_of

log = logging.getLogger(__name__)

BASE = "http://www.p-souba.com"
BOARDS = [
    ("pachinko", f"{BASE}/krank_1.htm", "パチンコ価格ランキング"),
    ("slot", f"{BASE}/krank_2.htm", "スロット価格ランキング"),
]

RANK_RE = re.compile(r"^(\d+)位$")
WEEKS_RE = re.compile(r"^(\d+)週目$")
YEN_RE = re.compile(r"円")
SIGNED_RE = re.compile(r"^[+\-±]")


def parse_ranking(html: str, kind: str) -> list:
    """解析排行榜表格。

    欄位位置會因為有沒有登入而位移（登入才會多一欄價格），
    所以不靠固定索引，改用內容特徵判斷：
      「12位」→ 排名 ／ 「90週目」→ 上市週數
      含「円」且開頭有 +- ± → 漲跌額
      含「円」但沒有正負號 → 價格（只有登入才會出現）
    """
    soup = soup_of(html)
    out = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if not any(YEN_RE.search(c) for c in cells):
            continue

        rank = weeks = price = delta = None
        leftovers = []
        for c in cells:
            m = RANK_RE.match(c)
            if m:
                rank = int(m.group(1))
                continue
            m = WEEKS_RE.match(c)
            if m:
                weeks = int(m.group(1))
                continue
            if YEN_RE.search(c):
                if SIGNED_RE.match(c):
                    delta = _to_int(c)
                else:
                    price = _to_int(c)
                continue
            if c:
                leftovers.append(c)

        if rank is None or len(leftovers) < 2:
            continue
        out.append({
            "kind": kind,
            "rank": rank,
            "maker": leftovers[0],
            "name": leftovers[1],
            "type": leftovers[2] if len(leftovers) > 2 else "",
            "price": price,          # 未登入時為 None
            "delta": delta,
            "weeks": weeks,
        })
    return out


def _to_int(text: str):
    m = re.search(r"([+\-]?)\s*([0-9][0-9,]*)", text)
    if not m:
        return 0 if "±" in text else None
    value = int(m.group(2).replace(",", ""))
    return -value if m.group(1) == "-" else value


def fetch_rankings(fetcher) -> tuple:
    """抓兩張排行榜。回傳 (資料列, 警告訊息)。"""
    rows, warnings = [], []
    for kind, url, label in BOARDS:
        html = fetcher.get(url)
        if not html:
            warnings.append(f"[p_souba] {label} 抓取失敗")
            continue
        parsed = parse_ranking(html, kind)
        if not parsed:
            warnings.append(f"[p_souba] {label} 解析不到資料 — 版面可能改了")
            continue
        rows.extend(parsed)
        with_price = sum(1 for r in parsed if r["price"] is not None)
        log.info("[p_souba] %s：%d 筆（含價格 %d 筆）", label, len(parsed), with_price)

    if rows and not any(r["price"] is not None for r in rows):
        warnings.append(
            "[p_souba] 行情價欄位需要會員登入才會顯示，自動抓取取不到，"
            "比價表該欄會顯示「-」；漲跌額與排名不受影響"
        )
    return rows, warnings


def attach_to_comparison(comparison: list, rankings: list) -> int:
    """把行情資訊掛到比價表上。回傳成功對上的機種數。"""
    from .normalize import normalize

    index = {}
    for r in rankings:
        norm = normalize(r["name"])
        if norm["key"]:
            index.setdefault(norm["key"], r)

    matched = 0
    for row in comparison:
        key = normalize(row["name"])["key"]
        hit = index.get(key)
        if hit is None:
            # 排行榜的寫法可能多／少幾個字，退一步做包含比對
            for k, r in index.items():
                if k and key and (k in key or key in k) and abs(len(k) - len(key)) <= 6:
                    hit = r
                    break
        if hit:
            matched += 1
            row["souba"] = {
                "rank": hit["rank"],
                "delta": hit["delta"],
                "weeks": hit["weeks"],
                "type": hit["type"],
                "price": hit["price"],
            }
        else:
            row["souba"] = None
    return matched
