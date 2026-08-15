"""跨站機種比對。

策略是「寧可不配，也不要配錯」：
  key 完全相同        → 直接同群
  相似度 >= 90 且規格相容 → 自動同群
  相似度 75~90        → 進人工待確認佇列（不自動配）
  相似度 < 75         → 視為不同機種

人工確認過的配對寫進 aliases.json，之後永久生效，不必再確認第二次。
實務上跑一兩輪之後，需要人工介入的量就會趨近於零。
"""

import json
import os

from rapidfuzz import fuzz, process

from .config import MATCH_AUTO, MATCH_REVIEW
from .normalize import normalize, spec_compatible

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ALIAS_FILE = os.path.join(DATA_DIR, "aliases.json")


def load_aliases() -> dict:
    """{ 正規化key: 群組ID } — 人工確認過的對應表。"""
    if os.path.exists(ALIAS_FILE):
        try:
            with open(ALIAS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_aliases(aliases: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ALIAS_FILE, "w", encoding="utf-8") as f:
        json.dump(aliases, f, ensure_ascii=False, indent=2, sort_keys=True)


def build_groups(rows: list):
    """rows 是所有站抓到的商品。回傳 (groups, review_queue)。"""
    aliases = load_aliases()

    enriched = []
    for row in rows:
        norm = normalize(row["raw_name"])
        if not norm["key"]:
            continue
        enriched.append({**row, **norm})

    # 長名稱先處理：資訊多的當群代表，比較不會誤收
    enriched.sort(key=lambda r: (-len(r["key"]), r["key"]))

    groups = {}          # gid -> {"key","name","spec","maker","members":[...]}
    rep_keys = []        # 群代表 key 清單，供 rapidfuzz 搜尋
    rep_gids = []
    exact = {}           # key -> gid
    review = []

    for row in enriched:
        key, spec = row["key"], row["spec"]

        # 1) 人工確認過的 alias 最優先
        gid = aliases.get(key)
        if gid is not None and gid in groups:
            groups[gid]["members"].append(row)
            continue

        # 2) key 完全相同
        gid = exact.get(key)
        if gid is not None and spec_compatible(groups[gid]["spec"], spec):
            groups[gid]["members"].append(row)
            continue

        # 3) 模糊比對。一次取多個候選，因為分數最高的那個規格可能不相容
        matched_gid = None
        if rep_keys:
            candidates = process.extract(
                key, rep_keys, scorer=fuzz.token_set_ratio, limit=5, score_cutoff=MATCH_REVIEW
            )
            best_reviewable = None
            for cand_key, score, idx in candidates:
                cand_gid = rep_gids[idx]
                if not spec_compatible(groups[cand_gid]["spec"], spec):
                    continue
                # 續作編號不同（からくりサーカス vs からくりサーカス2）→
                # 不管相似度多高都不自動配，只丟人工確認
                same_digits = groups[cand_gid]["digits"] == row["digits"]
                if score >= MATCH_AUTO and same_digits:
                    matched_gid = cand_gid
                    break
                if best_reviewable is None:
                    best_reviewable = (cand_gid, score)
            if matched_gid is None and best_reviewable:
                cand_gid, score = best_reviewable
                reason = "相似度不足"
                if score >= MATCH_AUTO:
                    reason = "續作編號不同" if groups[cand_gid]["digits"] != row["digits"] else "規格不同"
                review.append({
                    "candidate_gid": cand_gid,
                    "candidate_name": groups[cand_gid]["name"],
                    "score": round(score, 1),
                    "reason": reason,
                    "key": key,
                    "raw_name": row["raw_name"],
                    "site": row["site"],
                    "url": row["url"],
                })

        if matched_gid is not None:
            groups[matched_gid]["members"].append(row)
            continue

        # 4) 開新群
        gid = len(groups) + 1
        groups[gid] = {
            "gid": gid,
            "key": key,
            "name": row["core"] or row["raw_name"],
            "spec": spec,
            "maker": row["maker"],
            "digits": row["digits"],
            "members": [row],
        }
        exact.setdefault(key, gid)
        rep_keys.append(key)
        rep_gids.append(gid)

    return groups, review


# 同一台機在不同店的報價差到這個倍數以上，就不是一般行情差異了。
#
# 但注意：這**不代表抓錯**。實際查證過 ソードアート オンラインII ——
# 兩站報 7～8 萬，FC2 站上白紙黑字就是標 3,580,000 円。日本店家有時會把
# 不想賣或缺貨的品項掛上天價，那是真實標價。
#
# 所以這裡只「標記」不「刪除」：價格照實呈現（那是網站上真的寫的），
# 但不讓它去污染價差中位數、最大價差、可省金額這些統計數字。
OUTLIER_RATIO = 8


def _flag_outlier_prices(by_site: dict) -> int:
    """把明顯偏離行情的報價標記起來。回傳標記筆數。"""
    for val in by_site.values():
        val["outlier"] = False
    if len(by_site) < 2:
        return 0
    low = min(v["price"] for v in by_site.values())
    flagged = 0
    for val in by_site.values():
        if val["price"] > low * OUTLIER_RATIO:
            val["outlier"] = True
            flagged += 1
    return flagged


def summarize(groups: dict, site_keys: list) -> list:
    """把群組整理成前端要的比價列。"""
    out = []
    dropped_total = []
    for group in groups.values():
        in_stock = [m for m in group["members"] if not m["sold_out"]]
        pool = in_stock or group["members"]     # 全部售完時退而列出售完價，但會標記

        by_site = {}
        for member in pool:
            current = by_site.get(member["site"])
            if current is None or member["price"] < current["price"]:
                by_site[member["site"]] = {
                    "price": member["price"],
                    "url": member["url"],
                    "raw_name": member["raw_name"],
                    "sold_out": member["sold_out"],
                }

        flagged = _flag_outlier_prices(by_site)
        if flagged:
            for key, val in by_site.items():
                if val["outlier"]:
                    dropped_total.append({"name": group["name"], "site": key, **val})

        prices = [v["price"] for v in by_site.values()]
        if not prices:
            continue
        # 價差統計只看正常報價，天價品項照樣顯示但不參與計算，
        # 否則「最大價差」「可省金額」會被一兩筆天價灌成假數字
        normal = [v["price"] for v in by_site.values() if not v["outlier"]] or prices
        low, high = min(normal), max(normal)

        out.append({
            "gid": group["gid"],
            "name": group["name"],
            "spec": group["spec"],
            "maker": group["maker"],
            "site_count": len(by_site),
            "in_stock": bool(in_stock),
            "min_price": low,
            "max_price": high,
            "spread": high - low,
            "spread_pct": round((high - low) / high * 100, 1) if high else 0.0,
            "has_outlier": bool(flagged),
            "cheapest_site": min(
                (kv for kv in by_site.items() if not kv[1]["outlier"]),
                key=lambda kv: kv[1]["price"], default=min(by_site.items(), key=lambda kv: kv[1]["price"]))[0],
            "sites": {k: by_site.get(k) for k in site_keys},
        })

    # 可比價（多站都有）且價差大的排前面 —— 這就是客戶真正想看的東西
    out.sort(key=lambda r: (-r["site_count"], -r["spread"]))
    return out, dropped_total
