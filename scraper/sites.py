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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import parse
from .config import SiteConfig
from .http import Fetcher

log = logging.getLogger(__name__)

PROBE_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pagination.json"
)

# 依常見程度排序的分頁參數候選
PAGE_PARAM_CANDIDATES = {
    "shopserve": ["p", "page", "PageNo", "pageno", "pno"],
    "fc2cart": ["page", "p", "pn"],
}


def _with_param(url: str, key: str, value) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = str(value)
    return urlunparse(parts._replace(query=urlencode(query)))


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


class SiteScraper:
    def __init__(self, site: SiteConfig, use_cache: bool = False, detail_fallback: bool = True):
        self.site = site
        self.fetcher = Fetcher(delay=site.delay, use_cache=use_cache)
        self.detail_fallback = detail_fallback
        self.warnings = []

    # ---- 分類探索 -------------------------------------------------------
    def discover_categories(self) -> list:
        """從起點頁 BFS 兩層，收齊所有商品分類頁。"""
        found, queue, visited = [], list(self.site.start_urls), set()
        depth = {u: 0 for u in queue}

        while queue:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            html = self.fetcher.get(url)
            if not html:
                continue
            for cat in parse.find_category_links(html, self.site.base_url, self.site.kind):
                if cat not in found:
                    found.append(cat)
                if depth.get(url, 0) < 1 and cat not in depth:
                    depth[cat] = depth.get(url, 0) + 1
                    queue.append(cat)

        if not found:
            self.warnings.append(f"[{self.site.key}] 找不到任何分類頁 — 網站結構可能已改版")
        log.info("[%s] 找到 %d 個分類", self.site.key, len(found))
        if self.site.max_categories:
            found = found[: self.site.max_categories]
        return found

    # ---- 分頁探測 -------------------------------------------------------
    def detect_page_param(self, sample_category_url: str, first_page_urls: set):
        """試出這個站的分頁參數。回傳參數名，或 None（代表單頁或不支援）。"""
        cache = _load_probe_cache()
        if self.site.key in cache:
            return cache[self.site.key] or None

        result = None
        for key in PAGE_PARAM_CANDIDATES.get(self.site.kind, ["page"]):
            probe_url = _with_param(sample_category_url, key, 2)
            html = self.fetcher.get(probe_url)
            if not html:
                continue
            urls = set(parse.find_item_links(html, probe_url, self.site.kind))
            # 有商品、且與第 1 頁不同 → 這個參數是有效的
            if urls and urls != first_page_urls and len(urls - first_page_urls) >= 3:
                result = key
                log.info("[%s] 分頁參數偵測成功：?%s=", self.site.key, key)
                break

        if result is None:
            self.warnings.append(
                f"[{self.site.key}] 分頁參數偵測失敗，只會抓每個分類的第一頁"
                f"（可能漏抓後續頁面的商品）"
            )
        cache[self.site.key] = result or ""
        _save_probe_cache(cache)
        return result

    # ---- 主流程 ---------------------------------------------------------
    def scrape(self) -> list:
        categories = self.discover_categories()
        if not categories:
            return []

        by_url = {}
        page_param = "__undetected__"

        for idx, cat_url in enumerate(categories, 1):
            html = self.fetcher.get(cat_url)
            if not html:
                continue
            items = parse.parse_list_page(html, cat_url, self.site.kind)
            first_page_urls = {i["url"] for i in items}
            for item in items:
                by_url.setdefault(item["url"], item)

            if page_param == "__undetected__":
                page_param = self.detect_page_param(cat_url, first_page_urls) if items else None

            if page_param:
                seen_urls = set(first_page_urls)
                for page_no in range(2, self.site.max_pages + 1):
                    page_url = _with_param(cat_url, page_param, page_no)
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
        for item in by_url.values():
            if not item["name"] or item["price"] is None:
                continue                    # 名稱或價格缺一不可，寧可丟掉也不要髒資料
            rows.append({
                "site": self.site.key,
                "site_name": self.site.name,
                "url": item["url"],
                "raw_name": item["name"],
                "price": item["price"],
                "sold_out": bool(item["sold_out"]),
            })

        dropped = len(by_url) - len(rows)
        if dropped:
            self.warnings.append(f"[{self.site.key}] {dropped} 件因缺名稱或價格被捨棄")
        log.info("[%s] 完成：%d 件有效商品（請求 %d 次）",
                 self.site.key, len(rows), self.fetcher.stats["requests"])
        return rows
