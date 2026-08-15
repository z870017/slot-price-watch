"""測試用假資料。

機種名與價格取自 2026-08-15 對三站的實際偵查結果，
但**刻意寫成三站各自不同的標題風格**，用來驗證比對邏輯真的能把它們認成同一台。
改動 normalize.py / matcher.py 之後跑 `python -m scraper.main demo` 就能立刻回歸測試。
"""


def _row(site, name, price, sold_out=False, slug=""):
    return {
        "site": site,
        "site_name": site,
        "url": f"https://example.test/{site}/{slug or abs(hash(name)) % 99999}.html",
        "raw_name": name,
        "price": price,
        "sold_out": sold_out,
    }


DEMO_ROWS = [
    # --- からくりサーカス2：三站三種寫法，價差 ¥10,000 ---
    _row("a_slot", "SANKYO Lパチスロ からくりサーカス2 中古パチスロ実機［スマスロ］", 523600, slug="k1"),
    _row("home_slot", "スマスロ からくりサーカス2", 513600, slug="k2"),
    _row("initialp", "【中古】L からくりサーカス2 スマスロ 実機 コイン不要機付", 498000, slug="k3"),

    # --- 甲鉄城のカバネリ：兩站，寫法差異大 ---
    _row("home_slot", "スマスロ 甲鉄城のカバネリ 海門決戦", 576900, slug="kb1"),
    _row("a_slot", "サミー Lパチスロ 甲鉄城のカバネリ 海門決戦 中古パチスロ実機 [スマスロ]", 559800, slug="kb2"),

    # --- 攻殻機動隊 ---
    _row("home_slot", "スマスロ 攻殻機動隊", 576900, slug="kk1"),
    _row("a_slot", "サミー Lパチスロ 攻殻機動隊 中古パチスロ実機［スマスロ］", 588000, slug="kk2"),
    _row("initialp", "L 攻殻機動隊 スマスロ 中古実機", 545000, slug="kk3"),

    # --- 東京リベンジャーズ：一站售完，比價要跳過售完的 ---
    _row("home_slot", "スマスロ 東京リベンジャーズ", 172400, slug="tr1"),
    _row("a_slot", "サミー Lパチスロ 東京リベンジャーズ 中古パチスロ実機", 165000, sold_out=True, slug="tr2"),
    _row("initialp", "【中古】L 東京リベンジャーズ 実機", 181000, slug="tr3"),

    # --- 頭文字D：規格不同必須分開！2nd 與 啓介パネル 是不同台 ---
    _row("home_slot", "サミー パチスロ頭文字D 啓介パネル", 38100, slug="id1"),
    _row("a_slot", "サミー パチスロ 頭文字D 啓介パネル 中古パチスロ実機", 41000, slug="id2"),
    _row("home_slot", "スマスロ頭文字D 2nd", 47800, slug="id3"),
    _row("a_slot", "サミー Lパチスロ 頭文字D 2nd 中古パチスロ実機［スマスロ］", 52000, slug="id4"),

    # --- 單站獨賣，不該出現在比價清單 ---
    _row("home_slot", "回胴黙示録カイジ 狂宴", 40100, slug="kj1"),
    _row("home_slot", "オリンピアエステート L戦国乙女4 戦乱に閃く炯眼の軍師", 88500, slug="sg1"),
    _row("a_slot", "スマスロ コードギアス 反逆のルルーシュ", 74900, slug="cg1"),

    # --- 全形/半形、空白差異的邊界測試 ---
    _row("initialp", "スマスロ　コードギアス　反逆のルルーシュ　【中古実機】", 71000, slug="cg2"),

    # --- 相似但不同機（2 vs 無數字），應該分開或進待確認佇列 ---
    _row("a_slot", "SANKYO パチスロ からくりサーカス 中古パチスロ実機", 88000, slug="k0"),
]
