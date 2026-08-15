"""通用 HTML 解析。

刻意「不」依賴各站的 CSS class 名稱，因為那是網站改版第一個會變的東西。
改用結構性特徵：
  1. 先用 URL 形態找出商品連結
  2. 對每個商品連結，往上找「剛好只包住這一個商品連結」的最小容器
  3. 在那個容器裡找價格 / 售完標記 / 商品名

這樣即使站方換了版型、改了 class，只要商品連結格式沒變就還能跑。
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# 價格：¥123,456 / 123,456円 / 価格：123,456
PRICE_RE = re.compile(r"[¥￥]\s*([0-9][0-9,]{2,})|([0-9][0-9,]{2,})\s*円")
SOLD_OUT_WORDS = ("在庫切れ", "売り切れ", "売切れ", "完売", "SOLD OUT", "soldout", "販売終了", "入荷待ち")
TAX_INCL_WORDS = ("税込", "込)", "込）")

# ShopServe 系：商品明細頁是 /SHOP/xxxx.html
# 分類頁有兩種：/SHOP/大分類/list.html 與 /SHOP/大分類/小分類/list.html
SHOPSERVE_ITEM_RE = re.compile(r"/SHOP/(?!.*/)([^/]+)\.html$", re.I)
SHOPSERVE_CAT_RE = re.compile(r"/SHOP/(\d+)(?:/(\d+))?/list\.html$", re.I)

# FC2 カート：商品頁常見 ?pid=123 或 /ca3/45/
FC2_ITEM_RE = re.compile(r"[?&]pid=(\d+)|/ca\d+/\d+/?$", re.I)
FC2_CAT_RE = re.compile(r"[?&]ca=(\d+)")


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _clean_text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def parse_price(text: str):
    """從一段文字取價格。有多個時取「税込」附近的，否則取最大的那個
    （中古機常同時列出稅前/稅後，稅後較大且是實付金額）。"""
    if not text:
        return None
    candidates = []
    for m in PRICE_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            value = int(raw.replace(",", ""))
        except ValueError:
            continue
        if value < 1000:          # 過濾掉「4ch」「8ch」之類的雜訊數字
            continue
        window = text[max(0, m.start() - 12): m.end() + 12]
        tax_incl = any(w in window for w in TAX_INCL_WORDS)
        candidates.append((tax_incl, value))
    if not candidates:
        return None
    tax_ones = [v for incl, v in candidates if incl]
    if tax_ones:
        return max(tax_ones)
    return max(v for _, v in candidates)


def is_sold_out(text: str) -> bool:
    lowered = text.lower()
    return any(w.lower() in lowered for w in SOLD_OUT_WORDS)


def find_item_links(html: str, base_url: str, kind: str):
    """回傳頁面上所有商品明細頁的絕對 URL（去重、保序）。"""
    soup = soup_of(html)
    pattern = SHOPSERVE_ITEM_RE if kind == "shopserve" else FC2_ITEM_RE
    host = urlparse(base_url).netloc
    seen, out = set(), []
    for a in soup.select("a[href]"):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).netloc != host:
            continue
        if kind == "shopserve":
            path = urlparse(absolute).path
            if not SHOPSERVE_ITEM_RE.search(path):
                continue
            if path.lower().endswith(("/list.html", "/index.html")):
                continue
        else:
            if not pattern.search(absolute):
                continue
        absolute = absolute.split("#")[0]
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out


def find_category_links(html: str, base_url: str, kind: str):
    """找出分類頁。回傳 [{url, text, top, sub}]。

    text / top / sub 是給上層判斷「這個分類裝的是實機還是配件」用的：
    兩站都把 スロット実機 和 スロットオプション 放在不同的大分類底下，
    大分類 ID 就寫在網址裡，比猜商品名可靠得多。
    """
    soup = soup_of(html)
    host = urlparse(base_url).netloc
    seen, out = set(), []
    for a in soup.select("a[href]"):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if urlparse(absolute).netloc != host:
            continue

        top = sub = None
        if kind == "shopserve":
            m = SHOPSERVE_CAT_RE.search(absolute)
            if not m:
                continue
            top, sub = m.group(1), m.group(2)
        else:
            # FC2 的商品頁網址也帶著 ca=（?ca=3&pid=120），要把商品頁排除掉，
            # 否則會把幾千個商品頁全部當成分類頁去翻頁。
            if not (FC2_CAT_RE.search(absolute) and not FC2_ITEM_RE.search(absolute)):
                continue

        if absolute in seen:
            continue
        seen.add(absolute)
        out.append({
            "url": absolute,
            "text": re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:60],
            "top": top,
            "sub": sub,
        })
    return out


def _minimal_container(anchor, item_hrefs):
    """往上找『剛好只包住這一個商品連結』的最小容器。

    一旦某層 parent 開始包住兩個以上商品連結，就代表爬過頭到整個列表了，
    退回上一層。
    """
    node = anchor
    best = anchor
    for _ in range(8):
        node = node.parent
        if node is None or node.name in ("body", "html"):
            break
        # 注意：要算的是「幾個不同商品」，不是「幾個連結」。
        # 商品卡片幾乎都同時有圖片連結和文字連結，兩個都指向同一商品；
        # 若按連結數算，容器在卡片層就被判定為爬過頭，價格永遠抓不到。
        distinct = {
            a["href"].split("#")[0]
            for a in node.select("a[href]")
            if a.get("href") and a["href"].split("#")[0] in item_hrefs
        }
        if len(distinct) > 1:
            break
        best = node
        # 已經找到價格就可以停了，不必再往上擴
        if parse_price(_clean_text(node)) is not None:
            break
    return best


def parse_list_page(html: str, page_url: str, kind: str):
    """從分類列表頁直接抽出商品（名稱/價格/售完）。

    這是主要路徑：一頁請求就拿到 40 筆，比每個商品開一次明細頁省下 95% 的請求量。
    抽不到價格的商品會標記 needs_detail，由上層決定要不要補抓明細頁。
    """
    soup = soup_of(html)
    items_abs = find_item_links(html, page_url, kind)
    if not items_abs:
        return []

    # 建立 href 原字串集合，供 _minimal_container 判斷
    raw_hrefs = set()
    anchors_by_abs = {}
    host_base = page_url
    for a in soup.select("a[href]"):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(host_base, href).split("#")[0]
        if absolute in items_abs:
            raw_hrefs.add(href.split("#")[0])
            anchors_by_abs.setdefault(absolute, []).append(a)

    results = []
    for url in items_abs:
        anchors = anchors_by_abs.get(url, [])
        if not anchors:
            continue
        # 同一商品常有「圖片連結 + 文字連結」兩個 a，取能拿到最多資訊的那個容器
        best = None
        for a in anchors:
            container = _minimal_container(a, raw_hrefs)
            text = _clean_text(container)
            price = parse_price(text)
            name = _extract_name(container, anchors)
            score = (1 if price else 0) * 2 + (1 if name else 0)
            if best is None or score > best[0]:
                best = (score, name, price, text)
        _, name, price, text = best
        results.append({
            "url": url,
            "name": name or "",
            "price": price,
            "sold_out": is_sold_out(text),
            "needs_detail": price is None or not name,
        })
    return results


def _extract_name(container, anchors):
    """商品名：連結文字 > 圖片 alt。兩者都拿不到就回空字串。

    早期版本在這兩招都失敗時，會退而取「容器內最長的一行文字」。
    那一招會把版面上的區塊標題和廣告文案當成商品名撈進來
    （實際跑出來就出現了「お客様の声」和一整段配件說明）。
    寧可回空字串讓上層去抓商品明細頁，也不要猜。
    """
    for a in anchors:
        t = _clean_text(a)
        if len(t) >= 4 and not is_sold_out(t):
            return t
    for a in anchors:
        img = a.find("img")
        if img and img.get("alt") and len(img["alt"].strip()) >= 4:
            return re.sub(r"\s+", " ", img["alt"].strip())
    img = container.find("img")
    if img and img.get("alt") and len(img["alt"].strip()) >= 4:
        return re.sub(r"\s+", " ", img["alt"].strip())
    return ""


def parse_detail_page(html: str, url: str):
    """商品明細頁解析（列表頁抽不到時的後援）。"""
    soup = soup_of(html)
    name = ""
    h1 = soup.find("h1")
    if h1:
        name = _clean_text(h1)
    if not name:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            name = og["content"].strip()
    if not name and soup.title:
        name = re.split(r"[|｜\-–—]", soup.title.get_text())[0].strip()

    body_text = _clean_text(soup.body) if soup.body else ""
    # 價格優先在「カートに入れる」按鈕附近找，避免抓到「関連商品」的價格
    scope_text = body_text
    for kw in ("カートに入れる", "カートへ入れる", "購入手続き"):
        idx = body_text.find(kw)
        if idx > 0:
            scope_text = body_text[max(0, idx - 400): idx + 100]
            break

    return {
        "url": url,
        "name": name,
        "price": parse_price(scope_text) or parse_price(body_text),
        "sold_out": is_sold_out(body_text[:3000]),
        "needs_detail": False,
    }
