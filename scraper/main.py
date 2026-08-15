"""CLI 進入點。

常用指令：
    python -m scraper.main run                     # 完整跑一輪
    python -m scraper.main run --limit-categories 3  # 快速試跑（只抓前 3 個分類）
    python -m scraper.main run --sites home_slot,a_slot
    python -m scraper.main run --use-cache         # 重用本地快取，改比對邏輯時免重抓
    python -m scraper.main demo                    # 用內建假資料驗證流程（不連外網）
"""

import argparse
import json
import logging
import os
import sys

from . import db, report
from .config import DROP_ALERT_RATIO, SITES
from .matcher import build_groups, summarize
from .sites import SiteScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run")


def run(args) -> int:
    selected = [s for s in SITES if s.enabled]
    if args.sites:
        wanted = {x.strip() for x in args.sites.split(",")}
        selected = [s for s in selected if s.key in wanted]
    if args.limit_categories:
        for site in selected:
            site.max_categories = args.limit_categories

    conn = db.connect()

    # --reprocess：不碰網路，直接拿資料庫裡最新一輪的原始資料重算報表。
    # 調整比對規則、過濾條件、統計方式時用這個，30 秒就能看到結果，
    # 不必為了改一行邏輯重抓 40 分鐘。
    if getattr(args, "reprocess", False):
        run_id = db.latest_run_id(conn)
        if run_id is None:
            log.error("資料庫裡還沒有任何完整的抓取紀錄，無法 reprocess")
            return 1
        all_rows = db.load_observations(conn, run_id)
        warnings = ["本次為重算模式：沿用最近一輪抓取的原始資料，未重新連線各站"]
        log.info("=== reprocess run #%d：%d 筆原始資料 ===", run_id, len(all_rows))
        counts = db.site_counts(conn, run_id)
        changes = []
        return _finish(args, conn, run_id, all_rows, warnings, counts, changes, selected)

    run_id = db.start_run(conn, trigger=args.trigger)
    log.info("=== run #%d 開始（trigger=%s）===", run_id, args.trigger)

    all_rows, warnings = [], []
    for site in selected:
        log.info("--- %s ---", site.name)
        scraper = SiteScraper(site, use_cache=args.use_cache, detail_fallback=not args.no_detail)
        try:
            rows = scraper.scrape()
        except Exception as e:                       # 單站爆掉不能拖垮整輪
            log.exception("[%s] 抓取失敗", site.key)
            warnings.append(f"[{site.key}] 抓取過程發生錯誤：{e}")
            rows = []
        all_rows.extend(rows)
        warnings.extend(scraper.warnings)

    db.save_observations(conn, run_id, all_rows)

    # 護欄：商品數暴跌代表網站可能改版了，要吵，不要安靜地把資料洗空
    counts = db.site_counts(conn, run_id)
    prev = db.previous_run_id(conn, run_id)
    if prev:
        before = db.site_counts(conn, prev)
        for key, old_n in before.items():
            new_n = counts.get(key, 0)
            if old_n >= 20 and new_n < old_n * DROP_ALERT_RATIO:
                warnings.append(
                    f"⚠ [{key}] 商品數從 {old_n} 掉到 {new_n}（-{100 - new_n * 100 // old_n}%）"
                    f"— 該站可能改版，請檢查 parser"
                )

    changes = db.detect_changes(conn, run_id)
    log.info("偵測到 %d 筆變動", len(changes))
    return _finish(args, conn, run_id, all_rows, warnings, counts, changes, selected)


def _finish(args, conn, run_id, all_rows, warnings, counts, changes, selected):
    """比對 → 統計 → 輸出。正常抓取與 --reprocess 共用這一段。"""
    groups, review = build_groups(all_rows)
    site_keys = [s.key for s in selected]
    comparison, price_outliers = summarize(groups, site_keys)
    if price_outliers:
        log.info("標記 %d 筆天價報價（同機種報價超過最低價 8 倍，不列入價差統計）", len(price_outliers))
        for o in price_outliers[:10]:
            log.info("  天價：%s @ %s ¥%s  %s", o["name"][:28], o["site"], f'{o["price"]:,}', o["url"])
        warnings.append(
            f"有 {len(price_outliers)} 筆報價明顯偏離行情（例如店家對缺貨品掛天價），"
            f"價格照實顯示但不列入價差統計")
    summary = report.poc_summary(comparison, counts, site_keys)

    site_meta = [{"key": s.key, "name": s.name, "base_url": s.base_url} for s in selected]
    json_path = report.write_frontend_json(
        comparison, summary, changes, site_meta, warnings,
        {"id": run_id, "trigger": args.trigger, "review_pending": len(review)},
    )
    csv_paths = report.write_csv(comparison, summary, changes, site_meta)
    db.finish_run(conn, run_id, note=f"{len(all_rows)} items, {len(changes)} changes")

    xlsx_path = None
    if not args.no_excel:
        xlsx_path = report.write_excel(comparison, summary, site_meta, review)

    os.makedirs(os.path.join(report.ROOT, "out"), exist_ok=True)
    with open(os.path.join(report.ROOT, "out", "review_queue.json"), "w", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=2)
    with open(os.path.join(report.ROOT, "out", "price_outliers.json"), "w", encoding="utf-8") as f:
        json.dump(price_outliers, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 62)
    print(f"  抓取商品         {summary['total_products']:>6} 件  {summary['per_site']}")
    print(f"  辨識機種         {summary['total_models']:>6} 台")
    print(f"  可跨站比價       {summary['comparable_models']:>6} 台")
    print(f"  三站都有         {summary['three_site_models']:>6} 台")
    print(f"  價差中位數       ¥{summary['median_spread_yen']:>9,}")
    print(f"  最大價差         ¥{summary['max_spread_yen']:>9,}")
    print(f"  全買最便宜可省   ¥{summary['total_potential_saving_yen']:>9,}")
    print(f"  待人工確認       {len(review):>6} 筆")
    print("=" * 62)
    print(f"  {summary['verdict']}")
    print("=" * 62)
    for w in warnings:
        print(f"  ⚠ {w}")
    print(f"\n  → {json_path}")
    for p in csv_paths.values():
        print(f"  → {p}")
    if xlsx_path:
        print(f"  → {xlsx_path}")

    return 0 if all_rows else 1


def demo(args) -> int:
    """不連外網，用假資料把整條流程跑一遍。

    用途：驗證正規化 / 比對 / 報表 / 前端是否接得起來，
    以及在改比對邏輯後快速回歸測試。
    """
    from .demo_data import DEMO_ROWS

    conn = db.connect(os.path.join(report.ROOT, "data", "demo.db"))
    run_id = db.start_run(conn, trigger="demo")
    db.save_observations(conn, run_id, DEMO_ROWS)
    changes = db.detect_changes(conn, run_id)
    counts = db.site_counts(conn, run_id)

    groups, review = build_groups(DEMO_ROWS)
    site_keys = [s.key for s in SITES]
    comparison, price_outliers = summarize(groups, site_keys)
    summary = report.poc_summary(comparison, counts, site_keys)
    site_meta = [{"key": s.key, "name": s.name, "base_url": s.base_url} for s in SITES]

    report.write_frontend_json(
        comparison, summary, changes, site_meta,
        ["這是 demo 假資料，不是真實抓取結果"],
        {"id": run_id, "trigger": "demo", "review_pending": len(review)},
    )
    report.write_csv(comparison, summary, changes, site_meta)
    db.finish_run(conn, run_id, note="demo")
    xlsx = report.write_excel(comparison, summary, site_meta, review)

    print(f"demo 完成：{len(DEMO_ROWS)} 筆假資料 → {summary['total_models']} 台機種，"
          f"{summary['comparable_models']} 台可比價，待確認 {len(review)} 筆")
    print(f"  → {xlsx}")
    for row in comparison[:8]:
        sites = " / ".join(
            f"{k}:¥{v['price']:,}" for k, v in row["sites"].items() if v
        )
        print(f"  [{row['site_count']}站] {row['name'][:34]:<34} 價差 ¥{row['spread']:>8,}  {sites}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="scraper", description="スロット實機跨站比價")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="實際抓取三站")
    p_run.add_argument("--sites", help="只跑指定站，逗號分隔（home_slot,a_slot,initialp）")
    p_run.add_argument("--limit-categories", type=int, default=0, help="每站只抓前 N 個分類（試跑用）")
    p_run.add_argument("--use-cache", action="store_true", help="重用本地 HTTP 快取")
    p_run.add_argument("--no-detail", action="store_true", help="不補抓商品明細頁（更快但資料較少）")
    p_run.add_argument("--no-excel", action="store_true", help="不產出 Excel")
    p_run.add_argument("--reprocess", action="store_true",
                       help="不連網路，用資料庫最新一輪的原始資料重新產出報表")
    p_run.add_argument("--trigger", default="manual", help="觸發來源標記（schedule / manual / web）")
    p_run.set_defaults(func=run)

    p_demo = sub.add_parser("demo", help="用假資料驗證整條流程")
    p_demo.set_defaults(func=demo)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
