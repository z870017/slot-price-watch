"""有禮貌的 HTTP 層。

原則：寧可慢，不要被封。
- 每站獨立節流（同站請求之間強制間隔）
- 失敗指數退避重試
- 本地快取（同一次執行內不重複抓同一頁；--use-cache 時跨執行也重用）
"""

import hashlib
import logging
import os
import time

import requests
from urllib.parse import urlsplit

from .config import USER_AGENT

log = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".httpcache")


class Fetcher:
    def __init__(self, delay: float = 2.0, use_cache: bool = False, timeout: int = 30,
                 encoding: str = None):
        self.delay = delay
        self.use_cache = use_cache
        self.timeout = timeout
        # 指定編碼。有些老站（中古機相場.com 是 EUC-JP）既沒在 header 宣告 charset，
        # 內容也短到自動偵測會猜錯——猜錯的結果不是報錯，而是整頁變亂碼、
        # 解析器安靜地抓到 0 筆。碰過一次就該把編碼寫死。
        self.force_encoding = encoding
        self._last_request_at = 0.0
        self._last_url = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            # FC2 カート 那站對「看起來不像瀏覽器」的請求會直接回 503，
            # 補齊這幾個一般瀏覽器都會送的標頭。
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        })
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0}

    # ---- cache ---------------------------------------------------------
    def _cache_path(self, url: str) -> str:
        return os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest() + ".html")

    def _read_cache(self, url: str):
        p = self._cache_path(url)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        return None

    def _write_cache(self, url: str, html: str) -> None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(self._cache_path(url), "w", encoding="utf-8") as f:
            f.write(html)

    # ---- fetch ---------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_at = time.time()

    def get(self, url: str, retries: int = 3):
        """回傳 HTML 字串；徹底失敗回 None（不丟例外，讓單頁失敗不炸掉整輪）。"""
        if self.use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                self.stats["cache_hits"] += 1
                return cached

        backoff = 4.0
        for attempt in range(1, retries + 1):
            self._throttle()
            try:
                # 帶上同站 Referer，跟正常瀏覽行為一致
                headers = {}
                parts = urlsplit(url)
                if self._last_url and urlsplit(self._last_url).netloc == parts.netloc:
                    headers["Referer"] = self._last_url
                else:
                    headers["Referer"] = f"{parts.scheme}://{parts.netloc}/"
                resp = self.session.get(url, timeout=self.timeout, headers=headers)
                self._last_url = url
                self.stats["requests"] += 1

                if resp.status_code == 200:
                    # 日本電商常見 Shift_JIS / EUC-JP，交給 requests 依 meta 推斷
                    if self.force_encoding:
                        resp.encoding = self.force_encoding
                    elif resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                        resp.encoding = resp.apparent_encoding
                    html = resp.text
                    self._write_cache(url, html)
                    return html

                if resp.status_code == 404:
                    return None

                # 403 / 429 是「你太吵了」，不是內容不存在 → 退避要更狠，
                # 而且退完之後要把整站的固定間隔也拉長，否則接下來每一頁都會撞牆
                if resp.status_code in (403, 429):
                    self.delay = min(self.delay * 1.5, 20.0)
                    backoff = max(backoff, 15.0)
                    log.warning("HTTP %s on %s — 放慢到每 %.1f 秒一次 (attempt %d/%d)",
                                resp.status_code, url, self.delay, attempt, retries)
                else:
                    log.warning("HTTP %s on %s (attempt %d/%d)",
                                resp.status_code, url, attempt, retries)
            except requests.RequestException as e:
                log.warning("request failed %s: %s (attempt %d/%d)", url, e, attempt, retries)

            if attempt < retries:
                time.sleep(backoff)
                backoff *= 2

        self.stats["errors"] += 1
        log.error("giving up on %s", url)
        return None
"""有禮貌的 HTTP 層。

原則：寧可慢，不要被封。
- 每站獨立節流（同站請求之間強制間隔）
- 失敗指數退避重試
- 本地快取（同一次執行內不重複抓同一頁；--use-cache 時跨執行也重用）
"""

import hashlib
import logging
import os
import time

import requests
from urllib.parse import urlsplit

from .config import USER_AGENT

log = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".httpcache")


class Fetcher:
    def __init__(self, delay: float = 2.0, use_cache: bool = False, timeout: int = 30):
        self.delay = delay
        self.use_cache = use_cache
        self.timeout = timeout
        self._last_request_at = 0.0
        self._last_url = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            # FC2 カート 那站對「看起來不像瀏覽器」的請求會直接回 503，
            # 補齊這幾個一般瀏覽器都會送的標頭。
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        })
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0}

    # ---- cache ---------------------------------------------------------
    def _cache_path(self, url: str) -> str:
        return os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest() + ".html")

    def _read_cache(self, url: str):
        p = self._cache_path(url)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        return None

    def _write_cache(self, url: str, html: str) -> None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(self._cache_path(url), "w", encoding="utf-8") as f:
            f.write(html)

    # ---- fetch ---------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_at = time.time()

    def get(self, url: str, retries: int = 3):
        """回傳 HTML 字串；徹底失敗回 None（不丟例外，讓單頁失敗不炸掉整輪）。"""
        if self.use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                self.stats["cache_hits"] += 1
                return cached

        backoff = 4.0
        for attempt in range(1, retries + 1):
            self._throttle()
            try:
                # 帶上同站 Referer，跟正常瀏覽行為一致
                headers = {}
                parts = urlsplit(url)
                if self._last_url and urlsplit(self._last_url).netloc == parts.netloc:
                    headers["Referer"] = self._last_url
                else:
                    headers["Referer"] = f"{parts.scheme}://{parts.netloc}/"
                resp = self.session.get(url, timeout=self.timeout, headers=headers)
                self._last_url = url
                self.stats["requests"] += 1

                if resp.status_code == 200:
                    # 日本電商常見 Shift_JIS / EUC-JP，交給 requests 依 meta 推斷
                    if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                        resp.encoding = resp.apparent_encoding
                    html = resp.text
                    self._write_cache(url, html)
                    return html

                if resp.status_code == 404:
                    return None

                # 403 / 429 是「你太吵了」，不是內容不存在 → 退避要更狠，
                # 而且退完之後要把整站的固定間隔也拉長，否則接下來每一頁都會撞牆
                if resp.status_code in (403, 429):
                    self.delay = min(self.delay * 1.5, 20.0)
                    backoff = max(backoff, 15.0)
                    log.warning("HTTP %s on %s — 放慢到每 %.1f 秒一次 (attempt %d/%d)",
                                resp.status_code, url, self.delay, attempt, retries)
                else:
                    log.warning("HTTP %s on %s (attempt %d/%d)",
                                resp.status_code, url, attempt, retries)
            except requests.RequestException as e:
                log.warning("request failed %s: %s (attempt %d/%d)", url, e, attempt, retries)

            if attempt < retries:
                time.sleep(backoff)
                backoff *= 2

        self.stats["errors"] += 1
        log.error("giving up on %s", url)
        return None
