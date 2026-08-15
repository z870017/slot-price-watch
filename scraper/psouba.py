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

    欄位數量會因為有沒有登入而不同（金額欄要登入才會輸出），
    所以不靠固定索引，改用內容特徵判斷：
      「12位」→ 排名 ／ 「90週目」→ 上市週數
      含「円」且開頭有 +- ± → 漲跌額（需登入）
      含「円」但沒有正負號 → 行情價（需登入）

    認列一筆資料的條件是「有排名欄」而不是「有金額欄」。
    早期版本用金額當條件，結果未登入時整頁一個「円」都沒有，
    100 筆全被跳過 —— 排名和機種名明明是公開的。
    """
    soup = soup_of(html)
    out = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if not any(RANK_RE.match(c) for c in cells):
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
            # 證據也塞進 warnings。GitHub Actions 的 log 要一層層點開才看得到，
            # warnings 會直接進 data.json，出問題時一眼就能判斷是編碼還是版面。
            head = html[:120].replace("\n", " ").replace("\r", " ")
            warnings.append(
                f"[p_souba] {label} 解析不到資料"
                f"（{len(html)} 字、<tr> {html.lower().count('<tr')} 個、"
                f"「位」{html.count('位')} 處、「円」{html.count('円')} 處）"
                f"｜開頭：{head}"
            )
            continue
        rows.extend(parsed)
        with_price = sum(1 for r in parsed if r["price"] is not None)
        log.info("[p_souba] %s：%d 筆（含價格 %d 筆）", label, len(parsed), with_price)

    if rows and not any(r["price"] is not None or r["delta"] is not None for r in rows):
        warnings.append(
            "[p_souba] 中古機相場.com 的金額欄（行情價、本週漲跌）需要會員登入才輸出，"
            "自動排程抓不到，比價表這兩欄顯示「-」；行情排名與上市週數正常更新"
        )
    return rows, warnings


FUZZY_MIN = 92


def attach_to_comparison(comparison: list, rankings: list) -> int:
    """把行情資訊掛到比價表上。回傳成功對上的機種數。

    比對規則刻意嚴格。第一版用「一邊包含另一邊」當退路，結果行情榜的
    「サンダーV」同時掛到了 サンダーV2、サンダーVスペシャル、
    サンダーVライトニング、ダイナミックサンダーV —— 那是四台不同的機器。
    現在的退路是：數字指紋要一致、機種類型不能互斥、字面相似度 92 分以上。
    寧可少掛幾台，也不要掛錯行情。
    """
    from rapidfuzz import fuzz

    from .normalize import normalize

    index = {}
    for r in rankings:
        norm = normalize(r["name"])
        if norm["key"]:
            index.setdefault(norm["key"], {**r, "_digits": norm["digits"]})

    matched = 0
    for row in comparison:
        norm = normalize(row["name"])
        key = norm["key"]
        hit = index.get(key)
        if hit is None and key:
            best_score = 0
            for k, r in index.items():
                if r["_digits"] != norm["digits"]:
                    continue          # 續作編號不同 → 不同機器，直接排除
                if norm["kind"] and r["kind"] != norm["kind"]:
                    continue          # 柏青哥／柏青嫂不能互掛
                score = fuzz.ratio(k, key)
                if score >= FUZZY_MIN and score > best_score:
                    best_score, hit = score, r
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
undefined"""中古機相場.com（p-souba）行情排行榜。

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

    欄位數量會因為有沒有登入而不同（金額欄要登入才會輸出），
    所以不靠固定索引，改用內容特徵判斷：
      「12位」→ 排名 ／ 「90週目」→ 上市週數
      含「円」且開頭有 +- ± → 漲跌額（需登入）
      含「円」但沒有正負號 → 行情價（需登入）

    認列一筆資料的條件是「有排名欄」而不是「有金額欄」。
    早期版本用金額當條件，結果未登入時整頁一個「円」都沒有，
    100 筆全被跳過 —— 排名和機種名明明是公開的。
    """
    soup = soup_of(html)
    out = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if not any(RANK_RE.match(c) for c in cells):
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
            # 證據也塞進 warnings。GitHub Actions 的 log 要一層層點開才看得到，
            # warnings 會直接進 data.json，出問題時一眼就能判斷是編碼還是版面。
            head = html[:120].replace("\n", " ").replace("\r", " ")
            warnings.append(
                f"[p_souba] {label} 解析不到資料"
                f"（{len(html)} 字、<tr> {html.lower().count('<tr')} 個、"
                f"「位」{html.count('位')} 處、「円」{html.count('円')} 處）"
                f"｜開頭：{head}"
            )
            continue
        rows.extend(parsed)
        with_price = sum(1 for r in parsed if r["price"] is not None)
        log.info("[p_souba] %s：%d 筆（含價格 %d 筆）", label, len(parsed), with_price)

    if rows and not any(r["price"] is not None or r["delta"] is not None for r in rows):
        warnings.append(
            "[p_souba] 中古機相場.com 的金額欄（行情價、本週漲跌）需要會員登入才輸出，"
            "自動排程抓不到，比價表這兩欄顯示「-」；行情排名與上市週數正常更新"
        )
    return rows, warnings


FUZZY_MIN = 92


def attach_to_comparison(comparison: list, rankings: list) -> int:
    """把行情資訊掛到比價表上。回傳成功對上的機種數。

    比對規則刻意嚴格。第一版用「一邊包含另一邊」當退路，結果行情榜的
    「サンダーV」同時掛到了 サンダーV2、サンダーVスペシャル、
    サンダーVライトニング、ダイナミックサンダーV —— 那是四台不同的機器。
    現在的退路是：數字指紋要一致、機種類型不能互斥、字面相似度 92 分以上。
    寧可少掛幾台，也不要掛錯行情。
    """
    from rapidfuzz import fuzz

    from .normalize import normalize

    index = {}
    for r in rankings:
        norm = normalize(r["name"])
        if norm["key"]:
            index.setdefault(norm["key"], {**r, "_digits": norm["digits"]})

    matched = 0
    for row in comparison:
        norm = normalize(row["name"])
        key = norm["key"]
        hit = index.get(key)
        if hit is None and key:
            best_score = 0
            for k, r in index.items():
                if r["_digits"] != norm["digits"]:
                    continue          # 續作編號不同 → 不同機器，直接排除
                if norm["kind"] and r["kind"] != norm["kind"]:
                    continue          # 柏青哥／柏青嫂不能互掛
                score = fuzz.ratio(k, key)
                if score >= FUZZY_MIN and score > best_score:
                    best_score, hit = score, r
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
