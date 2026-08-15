"""站台抓取流程。

三站共用同一套流程，差別只在 kind（shopserve / fc2cart）決定的 URL 形態。

分頁是這裡最麻煩的地方：偵查時發現兩個 ShopServe 站的分頁按鈕是 javascript:void(0)，
HTML 裡看不到 ?page=2 這種參數。與其猜一個寫死，不如讓程式在第一次跑時
**自動探測**哪一種分頁參數有效，把結果記下來重複使用。
網站改版換了分頁參數時，也只要刪掉探測快取重跑即可。
"""

import json
import logging
import os
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import parse
from .config import (
    MAX_NAME_LENGTH,
    MIN_MACHINE_PRICE,
    NON_MACHINE_KEYWORDS,
    OPTION_CATEGORY_KEYWORDS,
    OVERVIEW_CATEGORY_KEYWORDS,
    SiteConfig,
)
from .http import Fetcher

log = logging.getLogger(__name__)

PROBE_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pagination.json"
)

# 分頁候選。每個是 (種類, 鍵值)：
#   ("path",  "list")  → /SHOP/a/b/list.html 變成 /SHOP/a/b/list2.html
#   ("query", "p")     → 網址加上 ?p=2
#
# ShopServe 兩站的分頁按鈕寫的是 javascript:void(0)，看起來像沒有可用網址，
# 但 onclick 旁邊的 href 其實是 list2.html —— 是路徑式而不是參數式。
# 第一次實跑就是因為只試了參數式，每個分類都只抓到第一頁（40 件）。
PAGE_SCHEME_CANDIDATES = {
    "shopserve": [("path", "list"), ("query", "p"), ("query", "page"), ("query", "PageNo")],
    "fc2cart": [("query", "page"), ("query", "p"), ("query", "pn")],
}

LIST_HTML_RE = re.compile(r"/list\.html$", re.I)


def _with_param(url: str, key: str, value) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = str(value)
    return urlunparse(parts._replace(query=urlencode(query)))


def _page_url(url: str, scheme, page_no: int):
    """依 scheme 組出第 N 頁的網址。無法套用時回 None。"""
    kind, key = scheme
    if kind == "path":
        parts = urlparse(url)
        if not LIST_HTML_RE.search(parts.path):
            return None
        new_path = LIST_HTML_RE.sub(f"/{key}{page_no}.html", parts.path)
        return urlunparse(parts._replace(path=new_path))
    return _with_param(url, key, page_no)


def _load_probe_cache() -> dict:
    if os.path.exists(PROBE_CACHE):
        try:
            with open(PROBE_CACHE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_probe_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(PROBE_CACHE), exist_ok=True)
    with open(PROBE_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def is_option_category(name: str) -> bool:
    """這個大分類裝的是配件／服務，不是實機。"""
    return any(word in name for word in OPTION_CATEGORY_KEYWORDS)


def is_overview_category(name: str) -> bool:
    """「全件表示」這種總覽分類：內容與其他分類重複，且含配件。"""
    return any(word in name for word in OVERVIEW_CATEGORY_KEYWORDS)


def is_machine(name: str, price: int) -> bool:
    """判斷這筆是不是一台實機（而不是配件、服務或版面文案）。

    三站的分類頁裡實機和配件是混在一起的，不濾掉的話比價表會被
    「コイン不要機」「不要台回収サービス」「お客様の声」這種東西洗版。
    """
    if price < MIN_MACHINE_PRICE:
        return False
    if len(name) > MAX_NAME_LENGTH:
        return False
    return not any(word in name for word in NON_MACHINE_KEYWORDS)


class SiteScraper:
    def __init__(self, site: SiteConfig, use_cache: bool = False, detail_fallback: bool = True):
        self.site = site
        self.fetcher = Fetcher(delay=site.delay, use_cache=use_cache)
        self.detail_fallback = detail_fallback
        self.warnings = []

    # ---- 分類探索 -------------------------------------------------------
    # 第一層找到這麼多分類就夠了，不必再往下鑽
    ENOUGH_CATEGORIES = 10
    # 分類第一頁至少有這麼多商品，才值得拿它去試「有沒有第 2 頁」
    PAGE_PROBE_MIN_ITEMS = 20
    # 探測最多試幾個分類。
    #
    # 這個數字要放大，因為 ShopServe 對超出範圍的頁碼是「回傳第 1 頁」而不是 404 ——
    # 拿一個只有一頁的分類去試，看起來就跟「這個站不支援分頁」一模一樣。
    # 之前設 3，A-SLOT 前三個滿 20 件的分類剛好都只有一頁，就被誤判成沒有分頁，
    # 整站少抓了一大半商品。
    MAX_PROBE_ATTEMPTS = 8

    def discover_categories(self) -> list:
        """找出所有商品分類頁。

        只從首頁抓一層。這兩個站的首頁側欄本來就列了全部廠商分類，
        再逐一打開每個分類頁去找子分類，會多送上百個請求 —— 第一次實跑時
        光是這個探索階段就花了 5 分 43 秒，佔掉整輪一半的時間。
        只有第一層收穫太少時才退回去做第二層。
        """
        entries = []
        for url in self.site.start_urls:
            html = self.fetcher.get(url)
            if not html:
                continue
            for e in parse.find_category_links(html, self.site.base_url, self.site.kind):
                if not any(x["url"] == e["url"] for x in entries):
                    entries.append(e)

        if len(entries) < self.site.enough_categories:
            log.info("[%s] 第一層只找到 %d 個分類，往下再找一層", self.site.key, len(entries))
            for e in list(entries):
                html = self.fetcher.get(e["url"])
                if not html:
                    continue
                for sub in parse.find_category_links(html, self.site.base_url, self.site.kind):
                    if not any(x["url"] == sub["url"] for x in entries):
                        entries.append(sub)

        # 大分類 ID → 導覽上的名稱（「スロット実機」「オプション」…）
        top_names = {
            e["top"]: e["text"]
            for e in entries
            if e["top"] and not e["sub"] and e["text"]
        }
        excluded_tops = {tid: name for tid, name in top_names.items() if is_option_category(name)}
        if excluded_tops:
            log.info("[%s] 排除配件／服務大分類：%s", self.site.key,
                     "、".join(f"{n}({i})" for i, n in excluded_tops.items()))

        found, skipped = [], 0
        for e in entries:
            # 大分類頁本身不抓，它底下的小分類會逐一走訪
            if e["top"] and not e["sub"] and e["top"] in top_names:
                continue
            # ShopServe：靠大分類名稱判斷（スロット実機 vs スロットオプション）
            if e["top"] and e["top"] in excluded_tops:
                skipped += 1
                continue
            # FC2 カート：沒有大分類這層，但側欄的分類名稱本身就寫得很清楚
            # （中古パチンコ 一覧 / 中古パチスロ 一覧 / オプション品 一覧），直接看名稱
            if not e["top"] and e["text"] and is_option_category(e["text"]):
                skipped += 1
                log.info("[%s] 跳過配件分類：%s", self.site.key, e["text"])
                continue
            if e["text"] and is_overview_category(e["text"]):
                skipped += 1
                log.info("[%s] 跳過總覽分類：%s", self.site.key, e["text"])
                continue
            found.append(e["url"])

        if skipped:
            log.info("[%s] 跳過 %d 個配件分類", self.site.key, skipped)
        if not found:
            self.warnings.append(f"[{self.site.key}] 找不到任何分類頁 — 網站結構可能已改版")
        log.info("[%s] 找到 %d 個實機分類", self.site.key, len(found))
        if self.site.max_categories:
            found = found[: self.site.max_categories]
        return found

    # ---- 分頁探測 -------------------------------------------------------
    def detect_page_scheme(self, sample_category_url: str, first_page_urls: set):
        """試出這個站的分頁方式。回傳 (種類, 鍵值)，或 None（單頁 / 偵測不到）。"""
        cache = _load_probe_cache()
        cached = cache.get(self.site.key)
        if isinstance(cached, list) and len(cached) == 2:
            return tuple(cached)
        # 只快取「成功」的結果。失敗不記錄，下一輪會重新探測 ——
        # 否則站方改版或探測邏輯修好之後，會被舊的失敗紀錄永久卡住。

        result = None
        for scheme in PAGE_SCHEME_CANDIDATES.get(self.site.kind, [("query", "page")]):
            probe_url = _page_url(sample_category_url, scheme, 2)
            if not probe_url:
                continue
            html = self.fetcher.get(probe_url)
            if not html:
                continue
            # 用跟正式解析同一條路徑取商品，否則像 FC2 這種要靠價格特徵
            # 才認得出商品的站，探測會拿到 0 件而誤判成「沒有分頁」
            urls = {i["url"] for i in parse.parse_list_page(html, probe_url, self.site.kind)}
            # 有商品、且跟第 1 頁明顯不同 → 這個方式有效。
            # 比對「新出現的商品」而不是只看不相等，因為有些站分頁失敗時
            # 會默默回傳第一頁，那種情況必須判定為失敗。
            if urls and len(urls - first_page_urls) >= 3:
                result = scheme
                log.info("[%s] 分頁方式偵測成功：%s / %s", self.site.key, scheme[0], scheme[1])
                break
            log.debug("[%s] 分頁候選 %s/%s 無效（新商品 %d 個）",
                      self.site.key, scheme[0], scheme[1],
                      len(urls - first_page_urls) if urls else 0)

        if result:
            cache[self.site.key] = list(result)
            _save_probe_cache(cache)
        return result

    # ---- 主流程 ---------------------------------------------------------
    def scrape(self) -> list:
        categories = self.discover_categories()
        if not categories:
            return []

        by_url = {}
        page_scheme = "__undetected__"
        probe_attempts = 0
        saw_full_page = False

        for idx, cat_url in enumerate(categories, 1):
            html = self.fetcher.get(cat_url)
            if not html:
                continue
            items = parse.parse_list_page(html, cat_url, self.site.kind)
            if not items and idx <= 2:
                # 分類頁抓得到、卻一件商品都認不出來 → 多半是商品網址規則沒對上。
                # 把實際連結印出來，比在本地猜正則快得多。
                log.info("[%s] %s 認不出任何商品，頁面連結樣本：\n    %s",
                         self.site.key, cat_url, parse.sample_links(html, cat_url))
            first_page_urls = {i["url"] for i in items}
            for item in items:
                by_url.setdefault(item["url"], item)

            # 只拿「看起來裝滿了」的分類去探測分頁。
            # 用只有 5 件商品的小分類去試第 2 頁，一定找不到，
            # 那不代表這個站沒有分頁 —— 第一次實跑就是這樣誤判成失敗的。
            if len(items) >= self.PAGE_PROBE_MIN_ITEMS:
                saw_full_page = True
                if page_scheme == "__undetected__" and probe_attempts < self.MAX_PROBE_ATTEMPTS:
                    probe_attempts += 1
                    detected = self.detect_page_scheme(cat_url, first_page_urls)
                    if detected:
                        page_scheme = detected
                    elif probe_attempts >= self.MAX_PROBE_ATTEMPTS:
                        page_scheme = None      # 試夠了還是不行，放棄

            if page_scheme and page_scheme != "__undetected__":
                seen_urls = set(first_page_urls)
                for page_no in range(2, self.site.max_pages + 1):
                    page_url = _page_url(cat_url, page_scheme, page_no)
                    if not page_url:
                        break
                    page_html = self.fetcher.get(page_url)
                    if not page_html:
                        break
                    page_items = parse.parse_list_page(page_html, page_url, self.site.kind)
                    new_urls = {i["url"] for i in page_items} - seen_urls
                    if not new_urls:
                        break              # 沒有新商品 = 已到最後一頁
                    seen_urls |= new_urls
                    for item in page_items:
                        by_url.setdefault(item["url"], item)

            log.info("[%s] %d/%d %s → 累計 %d 件",
                     self.site.key, idx, len(categories), cat_url, len(by_url))

        # 列表頁抽不到價格 / 名稱的，補抓明細頁
        if self.detail_fallback:
            pending = [i for i in by_url.values() if i["needs_detail"]]
            if pending:
                log.info("[%s] %d 件需補抓明細頁", self.site.key, len(pending))
            for item in pending:
                html = self.fetcher.get(item["url"])
                if not html:
                    continue
                detail = parse.parse_detail_page(html, item["url"])
                if detail["price"]:
                    item.update(detail)

        rows = []
        skipped_non_machine = 0
        for item in by_url.values():
            if not item["name"] or item["price"] is None:
                continue                    # 名稱或價格缺一不可，寧可丟掉也不要髒資料
            if not is_machine(item["name"], item["price"]):
                skipped_non_machine += 1
                continue
            rows.append({
                "site": self.site.key,
                "site_name": self.site.name,
                "url": item["url"],
                "raw_name": item["name"],
                "price": item["price"],
                "sold_out": bool(item["sold_out"]),
            })
        if skipped_non_machine:
            log.info("[%s] 濾掉 %d 筆非機台項目（配件／服務／版面文案）",
                     self.site.key, skipped_non_machine)

        # 只有在「確實遇過滿頁分類、卻仍然探測不出分頁」時才示警。
        # 試跑模式下分類少又小，沒探測是正常的，不該亂報警。
        if saw_full_page and not (page_scheme and page_scheme != "__undetected__"):
            self.warnings.append(
                f"[{self.site.key}] 分頁方式偵測失敗，只抓到每個分類的第一頁"
                f"（後續頁面的商品會漏掉）"
            )

        # 一件都沒抓到一定要吵。之前這裡是靜悄悄的 ——
        # FC2 站整站 0 件，摘要上卻一個警告都沒有，只有翻各站明細才會發現少了一整站。
        if not rows:
            self.warnings.append(
                f"⚠ [{self.site.key}] 完全沒抓到任何商品"
                f"（共送出 {self.fetcher.stats['requests']} 次請求、"
                f"失敗 {self.fetcher.stats['errors']} 次）— 該站可能擋掉了自動抓取"
            )

        # 扣掉被過濾器擋下的，剩下的才是真正「抓壞了」的筆數
        dropped = len(by_url) - len(rows) - skipped_non_machine
        if dropped:
            self.warnings.append(f"[{self.site.key}] {dropped} 件因缺名稱或價格被捨棄")
        if skipped_non_machine:
            log.info("[%s] 另有 %d 筆非機台項目被過濾", self.site.key, skipped_non_machine)
        log.info("[%s] 完成：%d 件有效商品（請求 %d 次）",
                 self.site.key, len(rows), self.fetcher.stats["requests"])
        return rows
