"""Saved order comparisons for the web workspace only."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import uuid

from planner import ROOT
import os


def connect():
    path = Path(os.environ.get("PARTBASKET_DB", str(ROOT / "instance" / "orders.db")))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=4)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, created TEXT NOT NULL,"
               " title TEXT NOT NULL, total TEXT NOT NULL, mode TEXT NOT NULL, result TEXT NOT NULL)")
    return db


def save(result):
    identifier = str(uuid.uuid4())
    title = " + ".join(item["name"] for item in result["projects"])
    optimized = result["plans"]["optimized"]
    with closing(connect()) as db, db:
        db.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                   (identifier, result["calculated_at"], title, optimized["total"],
                    result["mode"], json.dumps(result, ensure_ascii=False)))
    return identifier


def recent():
    with closing(connect()) as db:
        return [dict(row) for row in db.execute(
            "SELECT id,created,title,total,mode FROM orders ORDER BY created DESC,rowid DESC LIMIT 100"
        )]


def get(identifier):
    with closing(connect()) as db:
        row = db.execute("SELECT result FROM orders WHERE id = ?", (identifier,)).fetchone()
    return json.loads(row["result"]) if row else None


def delete(identifier):
    with closing(connect()) as db, db:
        return db.execute("DELETE FROM orders WHERE id = ?", (identifier,)).rowcount == 1
