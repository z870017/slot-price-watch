"""SQLite 儲存層。

Phase 0 只需要「這次抓到什麼」，但價格歷史從第一天就存下來，
不然等 Phase 2 要畫走勢圖時會發現沒有歷史資料可畫。
資料量很小（每次數千列），存進 repo 完全沒問題。
"""

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "prices.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    trigger     TEXT,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    run_id    INTEGER NOT NULL REFERENCES runs(id),
    site      TEXT NOT NULL,
    url       TEXT NOT NULL,
    raw_name  TEXT NOT NULL,
    price     INTEGER NOT NULL,
    sold_out  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, url)
);
CREATE INDEX IF NOT EXISTS idx_obs_url  ON observations(url);
CREATE INDEX IF NOT EXISTS idx_obs_site ON observations(site, run_id);
CREATE TABLE IF NOT EXISTS changes (
    run_id     INTEGER NOT NULL REFERENCES runs(id),
    site       TEXT NOT NULL,
    url        TEXT NOT NULL,
    raw_name   TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- price_down | price_up | listed | delisted | sold_out | restocked
    old_price  INTEGER,
    new_price  INTEGER,
    detected_at TEXT NOT NULL
);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(conn: sqlite3.Connection, trigger: str = "manual") -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, trigger) VALUES (?, ?)", (now(), trigger)
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, note: str = "") -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, note = ? WHERE id = ?", (now(), note, run_id)
    )
    conn.commit()


def save_observations(conn: sqlite3.Connection, run_id: int, rows: list) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO observations (run_id, site, url, raw_name, price, sold_out)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(run_id, r["site"], r["url"], r["raw_name"], r["price"], int(r["sold_out"])) for r in rows],
    )
    conn.commit()


def previous_run_id(conn: sqlite3.Connection, run_id: int):
    row = conn.execute(
        "SELECT id FROM runs WHERE id < ? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return row["id"] if row else None


def detect_changes(conn: sqlite3.Connection, run_id: int) -> list:
    """跟上一輪比對，產生價格 / 上下架 / 售完 事件。"""
    prev = previous_run_id(conn, run_id)
    if prev is None:
        return []

    old = {
        r["url"]: r
        for r in conn.execute("SELECT * FROM observations WHERE run_id = ?", (prev,))
    }
    new = {
        r["url"]: r
        for r in conn.execute("SELECT * FROM observations WHERE run_id = ?", (run_id,))
    }

    events = []
    for url, row in new.items():
        before = old.get(url)
        if before is None:
            events.append((row["site"], url, row["raw_name"], "listed", None, row["price"]))
            continue
        if row["price"] < before["price"]:
            events.append((row["site"], url, row["raw_name"], "price_down", before["price"], row["price"]))
        elif row["price"] > before["price"]:
            events.append((row["site"], url, row["raw_name"], "price_up", before["price"], row["price"]))
        if row["sold_out"] and not before["sold_out"]:
            events.append((row["site"], url, row["raw_name"], "sold_out", before["price"], row["price"]))
        elif before["sold_out"] and not row["sold_out"]:
            events.append((row["site"], url, row["raw_name"], "restocked", before["price"], row["price"]))

    for url, before in old.items():
        if url not in new:
            events.append((before["site"], url, before["raw_name"], "delisted", before["price"], None))

    stamp = now()
    conn.executemany(
        "INSERT INTO changes (run_id, site, url, raw_name, kind, old_price, new_price, detected_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id, *e, stamp) for e in events],
    )
    conn.commit()

    return [
        {"site": e[0], "url": e[1], "raw_name": e[2], "kind": e[3],
         "old_price": e[4], "new_price": e[5]}
        for e in events
    ]


def price_history(conn: sqlite3.Connection, url: str) -> list:
    rows = conn.execute(
        "SELECT r.started_at AS ts, o.price FROM observations o"
        " JOIN runs r ON r.id = o.run_id WHERE o.url = ? ORDER BY r.id",
        (url,),
    ).fetchall()
    return [{"ts": r["ts"], "price": r["price"]} for r in rows]


def site_counts(conn: sqlite3.Connection, run_id: int) -> dict:
    rows = conn.execute(
        "SELECT site, COUNT(*) AS n FROM observations WHERE run_id = ? GROUP BY site", (run_id,)
    ).fetchall()
    return {r["site"]: r["n"] for r in rows}


def latest_run_id(conn: sqlite3.Connection):
    row = conn.execute(
        "SELECT id FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def load_observations(conn: sqlite3.Connection, run_id: int) -> list:
    """把某一輪抓到的原始資料讀回來，供 --reprocess 重新產表用。"""
    rows = conn.execute("SELECT * FROM observations WHERE run_id = ?", (run_id,)).fetchall()
    return [
        {
            "site": r["site"], "site_name": r["site"], "url": r["url"],
            "raw_name": r["raw_name"], "price": r["price"], "sold_out": bool(r["sold_out"]),
        }
        for r in rows
    ]
