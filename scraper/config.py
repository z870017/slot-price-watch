"""站點設定：Phase 0 只鎖定客戶指定的三站。

新增站點時只要在 SITES 補一筆，其餘流程（正規化 / 比對 / 報表 / 前端）不用動。
"""

from dataclasses import dataclass, field


@dataclass
class SiteConfig:
    key: str                    # 內部代號
    name: str                   # 顯示名稱
    base_url: str               # 站台根網址
    kind: str                   # "shopserve" | "fc2cart"
    start_urls: list = field(default_factory=list)   # 分類清單的起點
    # 每次 HTTP 請求之間的間隔秒數。FC2 站對機器人較敏感，拉長。
    delay: float = 2.5
    # 單次執行的分類數上限（0 = 不限）。PoC 想快速試跑時可調小。
    max_categories: int = 0
    # 單一分類最多翻幾頁的保險絲，避免分頁探測失誤造成無限迴圈。
    max_pages: int = 30
    enabled: bool = True


SITES = [
    SiteConfig(
        key="home_slot",
        name="ホームスロット",
        base_url="https://home-slot.net",
        kind="shopserve",
        start_urls=["https://home-slot.net/"],
        delay=2.0,
    ),
    SiteConfig(
        key="a_slot",
        name="A-SLOT",
        base_url="https://www.a-slot.com",
        kind="shopserve",
        start_urls=["https://www.a-slot.com/"],
        delay=2.0,
    ),
    SiteConfig(
        key="initialp",
        name="イニシャルP (FC2)",
        base_url="https://initialp.cart.fc2.com",
        kind="fc2cart",
        start_urls=["https://initialp.cart.fc2.com/"],
        # 這站在偵查時 robots.txt 回 503，代表對機器人較敏感 → 節奏放到最慢
        delay=4.0,
    ),
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "SlotPriceWatch/0.1 (personal price comparison; contact: owner)"
)

# 抓到的商品數比上一次掉超過這個比例就發出告警（代表網站可能改版了）
DROP_ALERT_RATIO = 0.5

# 機種模糊比對門檻
MATCH_AUTO = 90     # >= 自動視為同一機種
MATCH_REVIEW = 75   # >= 進人工待確認佇列，< 視為不同機種

# ---------------------------------------------------------------------------
# 「這筆到底是不是一台機器」的判斷
#
# 三站除了實機，也賣配件與服務：コイン不要機、データカウンター、
# 音量加工、不要台回收、10 円玉加工⋯⋯這些跟實機混在同一批分類頁裡。
# 拿它們去比價毫無意義，還會把畫面洗版，所以在入庫前就濾掉。
# ---------------------------------------------------------------------------

# 中古實機再便宜也不會低於這個價；¥3,000 的「回收服務」一定不是機台
MIN_MACHINE_PRICE = 15000

# 機種名不會這麼長。過長的多半是抓到廣告文案或規格說明
MAX_NAME_LENGTH = 45

# 命中任一個就排除。選詞刻意保守，只放明確不可能出現在機種名裡的字眼
NON_MACHINE_KEYWORDS = [
    "お客様の声", "レビュー",
    "サービス", "回収", "加工", "取付", "延長保証", "保証書",
    "コイン不要機", "データカウンター", "カウンター",
    "ドル箱", "スタンド", "キャビネット", "テーブル", "椅子", "イス",
    "電源スイッチ", "スイッチ", "リモコン", "アダプター", "変換",
    "送料", "配送料", "代引", "設置費",
    "メダル", "パチンコ玉", "球貸", "補給",
    "クリーナー", "洗浄", "清掃", "工具", "部品", "パーツ",
]
