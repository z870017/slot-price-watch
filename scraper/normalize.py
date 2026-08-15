"""機種名正規化。

三站對同一台機的寫法差很多，例如：
  A-SLOT       SANKYO Lパチスロ からくりサーカス2 中古パチスロ実機［スマスロ］
  ホームスロット  スマスロ からくりサーカス2
  FC2          【中古】L からくりサーカス2 スマスロ 実機

目標是把上面三個都壓成同一個 core：「からくりサーカス2」＋ spec「スマスロ」。

規格（スマスロ / L / 6号機…）必須另外抽出來獨立比對，
因為同名不同規格是**完全不同的機台、不能拿來比價**——這是最容易出錯也最傷的地方。
"""

import re
import unicodedata

# 廠商名（含常見英日文寫法）。抽出來當輔助資訊，同時從名稱中移除。
MAKERS = [
    ("SANKYO", ["SANKYO", "サンキョー", "三共"]),
    ("Sammy", ["サミー", "SAMMY", "Sammy"]),
    ("山佐", ["山佐", "ヤマサ", "YAMASA"]),
    ("京楽", ["京楽", "キョウラク", "KYORAKU"]),
    ("大都技研", ["大都技研", "大都", "DAITO"]),
    ("ユニバーサル", ["ユニバーサル", "UNIVERSAL", "ユニメモ"]),
    ("オリンピア", ["オリンピア", "OLYMPIA", "オリンピアエステート"]),
    ("北電子", ["北電子", "KITADENSHI"]),
    ("平和", ["平和", "HEIWA", "オリンピア平和"]),
    ("藤商事", ["藤商事", "FUJISHOJI"]),
    ("ニューギン", ["ニューギン", "NEWGIN"]),
    ("SANYO", ["SANYO", "三洋"]),
    ("大一", ["大一", "DAIICHI"]),
    ("竹屋", ["竹屋", "TAKEYA"]),
    ("高砂", ["高砂", "TAKASAGO"]),
    ("エンターライズ", ["エンターライズ", "ENTERRISE"]),
    ("ベルコ", ["ベルコ", "BELCO"]),
    ("パイオニア", ["パイオニア", "PIONEER"]),
    ("メーシー", ["メーシー", "MACY"]),
    ("ロデオ", ["ロデオ", "RODEO"]),
    ("ネット", ["ネット企画", "NET企画"]),
    ("スパイキー", ["スパイキー", "SPIKY"]),
    ("ミズホ", ["ミズホ", "MIZUHO"]),
    ("七匠", ["七匠", "NANASHOU"]),
    ("KONAMI", ["KONAMI", "コナミ"]),
    ("BOOOM", ["BOOOM", "ブーム"]),
    ("エレコ", ["エレコ", "ELECO"]),
    ("藤商事", ["藤商事"]),
    ("ダイドー", ["ダイドー", "DAIDO"]),
    ("岡崎産業", ["岡崎産業"]),
]

# 規格 / 世代。順序重要：先比對長的字串。
SPEC_PATTERNS = [
    ("スマスロ", [r"スマスロ", r"スマートパチスロ"]),
    ("スマパチ", [r"スマパチ", r"スマートパチンコ"]),
    ("6号機", [r"6\s*号機", r"六号機"]),
    ("5号機", [r"5\s*号機", r"五号機"]),
    ("4号機", [r"4\s*号機", r"四号機"]),
]

# 型式前綴：L / S / P / e 開頭（L＝6.5号機以降、P＝パチンコ、e＝スマパチ）
#
# 後面「只接日文字元」是刻意的限制。早期版本允許後接英文字母，結果
# 「SANKYO」的 S 被當成型式前綴，害 A-SLOT 一大票商品配對不上。
# 型式前綴後面接的一定是日文機種名（Lパチスロ、L からくり…），不會是英文。
TYPE_PREFIX_RE = re.compile(r"(?:^|[\s\[【（(])([LSPe])\s*(?=[ぁ-んァ-ヶ一-龥])")

# 純雜訊詞，直接刪除
NOISE_WORDS = [
    "中古パチスロ実機", "中古パチンコ実機", "中古スロット実機",
    "パチスロ実機", "パチンコ実機", "スロット実機",
    "中古実機", "実機販売", "実機", "中古品", "中古", "新品", "未使用",
    "パチスロ", "パチンコ", "スロット", "スロット機",
    "本体", "台", "販売", "送料無料", "在庫あり", "在庫限り",
    "まるごと配送限定", "まるごと配送", "配送限定",
    "コイン不要機付", "コイン不要機セット", "コイン不要機",
    "フルセット", "セット", "特価", "限定", "人気", "おすすめ",
    "8ch対応", "4ch対応", "8ch", "4ch",
    "PRICEDOWN", "PRICE DOWN", "SALE", "NEW", "本日の目玉",
]

BRACKET_RE = re.compile(r"[【】［］\[\]（）()〔〕《》〈〉「」『』]")

# 柏青哥（パチンコ）和柏青嫂（パチスロ）是完全不同的機器，名字卻常常一樣
# —— 「新鬼武者」兩種都有。實際跑出來就把 エンターライズ 的スロット機
# 和 平和 的パチンコ機 併成同一台了。這兩類必須硬性分開。
PACHINKO_HINTS = ["パチンコ実機", "パチンコ台実機", "中古パチンコ", "スマパチ", "甘デジ"]
SLOT_HINTS = ["パチスロ実機", "パチスロ台実機", "スロット実機", "中古パチスロ",
              "中古スロット", "スマスロ", "中古スマスロ"]

# 同一機種的面板版本（レムパネル / 剣聖パネル / 双子パネル…）價格差很多，
# 是不同商品，不能混在一起比價
VARIANT_RE = re.compile(r"([ぁ-んァ-ヶ一-龥A-Za-z0-9]{1,10})(パネル|バージョン|Ver\.?)")


def detect_kind(raw_name: str):
    """判斷這是柏青嫂還是柏青哥。判斷不出來回 None（視為相容）。"""
    has_p = any(h in raw_name for h in PACHINKO_HINTS)
    has_s = any(h in raw_name for h in SLOT_HINTS)
    if has_p and not has_s:
        return "pachinko"
    if has_s and not has_p:
        return "slot"
    return None


def extract_variant(text: str):
    """抽出面板／版本標記，例如「レムパネル」的 レム。"""
    m = VARIANT_RE.search(text)
    return m.group(1) if m else None


MULTISPACE_RE = re.compile(r"\s+")


def to_halfwidth(s: str) -> str:
    """全形英數字→半形；同時把日文長音、波浪號等統一。"""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("〜", "~").replace("～", "~")
    s = s.replace("ー", "ー")  # 統一長音符
    return s


def extract_spec(name: str):
    """抽出規格標記。回傳 (spec, 去掉規格詞的名稱)。"""
    spec = None
    working = name
    for label, patterns in SPEC_PATTERNS:
        for pat in patterns:
            if re.search(pat, working):
                spec = spec or label
                working = re.sub(pat, " ", working)
    # 型式前綴一律抽出並移除，即使前面已經拿到規格。
    # 因為有的站寫「L からくりサーカス2」、有的寫「スマスロ からくりサーカス2」，
    # 留著 L 會讓兩邊的比對字串長得不一樣。
    m = TYPE_PREFIX_RE.search(working)
    if m:
        spec = spec or {"L": "L", "S": "S", "P": "P", "e": "e"}[m.group(1)]
        working = working[: m.start(1)] + " " + working[m.end(1):]
    return spec, working


def extract_maker(name: str):
    """抽出廠商。回傳 (maker, 去掉廠商名的名稱)。"""
    for canonical, aliases in MAKERS:
        for alias in sorted(aliases, key=len, reverse=True):
            if alias in name:
                return canonical, name.replace(alias, " ")
    return None, name


def strip_noise(name: str) -> str:
    for word in sorted(NOISE_WORDS, key=len, reverse=True):
        name = name.replace(word, " ")
    name = BRACKET_RE.sub(" ", name)
    name = re.sub(r"[\/,、･・\-–—_|｜:：]+", " ", name)
    return MULTISPACE_RE.sub(" ", name).strip()


def normalize(raw_name: str) -> dict:
    """把原始商品標題拆成結構化的比對特徵。"""
    text = to_halfwidth(raw_name)
    # 括號要先拆掉。「【中古】L からくりサーカス2」的 L 緊貼在 】 後面，
    # 不先拆括號的話型式前綴抓不到，比對字串就會殘留一個孤零零的 L。
    text = BRACKET_RE.sub(" ", text)
    # 先拔廠商再抽型式前綴：廠商名裡的字母（SANKYO 的 S）不能被誤認成型式
    maker, text = extract_maker(text)
    spec, text = extract_spec(text)
    core = strip_noise(text)

    # 用於模糊比對的字串：去掉所有空白與符號，只留字元本身
    key = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龥]", "", core).lower()

    return {
        "raw": raw_name,
        "core": core,
        "key": key,
        "spec": spec,
        "maker": maker,
        "digits": digit_fingerprint(core),
        # 用原始標題判斷機種類型：清洗過的 core 已經把「パチスロ実機」這種字樣刪掉了
        "kind": detect_kind(raw_name),
        "variant": extract_variant(to_halfwidth(raw_name)),
    }


def digit_fingerprint(core: str) -> tuple:
    """機種名裡的數字指紋。

    「からくりサーカス」與「からくりサーカス2」的文字相似度高達 96 分，
    模糊比對會很開心地把它們併成同一台 —— 但那是無印版與續作，
    價格差好幾倍。續作編號必須當成硬條件，不能交給相似度決定。
    """
    return tuple(sorted(re.findall(r"\d+", core)))


def spec_compatible(a, b) -> bool:
    """規格是否可視為同一台機。

    一邊沒標規格時視為相容（很多店家標題就是不寫），
    但兩邊都標了且不同 → 明確是不同機台，絕對不能配對。
    """
    if a is None or b is None:
        return True
    if a == b:
        return True
    # L 與 スマスロ 常混用（スマスロ 機台型式名就是以 L 開頭）
    equivalent = ({"L", "スマスロ"}, {"P", "スマパチ"}, {"e", "スマパチ"})
    return any({a, b} <= group for group in equivalent)
