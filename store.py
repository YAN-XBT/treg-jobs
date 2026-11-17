"""Local state — posting cache, tracker, profile, spend ledger.

One SQLite file in the plugin data dir; falls back to an in-memory dict when the
host's plugin_storage import is unavailable, so a tool never fails because state
could not be opened.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_MEM: dict[str, Any] = {"jobs": {}, "tracker": {}, "kv": {}}
_CONN: sqlite3.Connection | None = None
_DB: Path | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore

        return Path(plugin_data_dir("hermes-jobs"))
    except Exception:
        home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        return Path(home) / "plugin-data" / "hermes-jobs"


def connect() -> sqlite3.Connection | None:
    global _CONN, _DB
    if _CONN is not None:
        return _CONN
    with _LOCK:
        if _CONN is not None:
            return _CONN
        try:
            d = _data_dir()
            d.mkdir(parents=True, exist_ok=True)
            _DB = d / "jobs.sqlite3"
            conn = sqlite3.connect(str(_DB), timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    company TEXT, title TEXT, location TEXT, location_type TEXT,
                    url TEXT, board TEXT, posted TEXT, description TEXT,
                    source TEXT, first_seen TEXT, last_seen TEXT
                );
                CREATE TABLE IF NOT EXISTS tracker (
                    job_id TEXT PRIMARY KEY, status TEXT, note TEXT, person TEXT, updated TEXT
                );
                CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
                CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company);
                CREATE INDEX IF NOT EXISTS jobs_first_seen ON jobs(first_seen);
                """
            )
            conn.commit()
            _CONN = conn
        except Exception:
            _CONN = None
    return _CONN


def db_path() -> str:
    connect()
    return str(_DB) if _DB else ":memory:"


def upsert_jobs(rows: list[dict[str, Any]], source: str = "") -> int:
    """Store normalized postings; returns how many were new."""
    conn = connect()
    now = _now()
    new = 0
    if conn is None:
        for r in rows:
            prev = _MEM["jobs"].get(r["id"])
            if prev is None:
                new += 1
                r = dict(r, first_seen=now, last_seen=now, source=source)
            else:
                r = dict(r, first_seen=prev.get("first_seen", now), last_seen=now, source=source or prev.get("source", ""))
            _MEM["jobs"][r["id"]] = r
        return new
    with _LOCK:
        cur = conn.cursor()
        for r in rows:
            row = cur.execute("SELECT first_seen FROM jobs WHERE id=?", (r["id"],)).fetchone()
            first = row["first_seen"] if row else now
            if row is None:
                new += 1
            cur.execute(
                """INSERT INTO jobs (id,company,title,location,location_type,url,board,posted,description,source,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     company=excluded.company, title=excluded.title, location=excluded.location,
                     location_type=excluded.location_type, url=excluded.url, board=excluded.board,
                     posted=excluded.posted,
                     description=CASE WHEN excluded.description != '' THEN excluded.description ELSE jobs.description END,
                     source=excluded.source, last_seen=excluded.last_seen""",
                (
                    r["id"], r.get("company", ""), r.get("title", ""), r.get("location", ""),
                    r.get("location_type", ""), r.get("url", ""), r.get("board", ""), r.get("posted", ""),
                    r.get("description", ""), source, first, now,
                ),
            )
        conn.commit()
    return new


def get_job(job_id: str) -> dict[str, Any] | None:
    conn = connect()
    if conn is None:
        return _MEM["jobs"].get(job_id)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(terms: list[str] | None = None, location: str = "", since: str = "", limit: int = 500) -> list[dict[str, Any]]:
    conn = connect()
    rows = list(_MEM["jobs"].values()) if conn is None else [dict(r) for r in conn.execute("SELECT * FROM jobs").fetchall()]
    out: list[dict[str, Any]] = []
    for j in rows:
        if since and (j.get("first_seen") or "") <= since:
            continue
        if location and location.lower() not in str(j.get("location") or "").lower():
            continue
        if terms:
            hay = " ".join(str(j.get(k) or "") for k in ("title", "company", "location", "description")).lower()
            if not all(t.lower() in hay for t in terms):
                continue
        out.append(j)
        if len(out) >= limit:
            break
    return out


def set_profile(profile: dict[str, Any]) -> None:
    _put("profile", json.dumps(profile))


def get_profile() -> dict[str, Any]:
    raw = _get("profile")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def set_meta(key: str, value: str) -> None:
    _put(f"meta:{key}", value)


def get_meta(key: str) -> str:
    return _get(f"meta:{key}")


def _put(k: str, v: str) -> None:
    conn = connect()
    if conn is None:
        _MEM["kv"][k] = v
        return
    with _LOCK:
        conn.execute("INSERT INTO kv (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        conn.commit()


def _get(k: str) -> str:
    conn = connect()
    if conn is None:
        return str(_MEM["kv"].get(k, "") or "")
    row = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return row["v"] if row else ""


STATUSES = ("saved", "researching", "applied", "interview", "offer", "rejected", "skipped")


def track(job_id: str, status: str = "", note: str = "", person: str = "") -> dict[str, Any]:
    conn = connect()
    now = _now()
    if conn is None:
        prev = _MEM["tracker"].get(job_id, {})
        entry = {"job_id": job_id, "status": status or prev.get("status", "saved"),
                 "note": note or prev.get("note", ""), "person": person or prev.get("person", ""), "updated": now}
        _MEM["tracker"][job_id] = entry
        return entry
    with _LOCK:
        prev = conn.execute("SELECT * FROM tracker WHERE job_id=?", (job_id,)).fetchone()
        cur_status = status or (prev["status"] if prev else "saved")
        cur_note = note or (prev["note"] if prev else "")
        cur_person = person or (prev["person"] if prev else "")
        conn.execute(
            "INSERT INTO tracker (job_id,status,note,person,updated) VALUES (?,?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, note=excluded.note, "
            "person=excluded.person, updated=excluded.updated",
            (job_id, cur_status, cur_note, cur_person, now),
        )
        conn.commit()
    return {"job_id": job_id, "status": cur_status, "note": cur_note, "person": cur_person, "updated": now}


def tracker_rows(status: str = "") -> list[dict[str, Any]]:
    conn = connect()
    if conn is None:
        rows = list(_MEM["tracker"].values())
    else:
        sql, args = "SELECT * FROM tracker", ()
        if status:
            sql, args = "SELECT * FROM tracker WHERE status=?", (status,)
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        job = get_job(r["job_id"]) or {}
        r["title"] = job.get("title", "")
        r["company"] = job.get("company", "")
        r["url"] = job.get("url", "")
        r["location"] = job.get("location", "")
    rows.sort(key=lambda r: r.get("updated", ""), reverse=True)
    return rows
