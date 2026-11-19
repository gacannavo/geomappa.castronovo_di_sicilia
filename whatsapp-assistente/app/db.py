"""Archivio SQLite: contatti, messaggi (con trascrizioni), appuntamenti, riepiloghi."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    wa_id        TEXT PRIMARY KEY,
    name         TEXT,
    summary      TEXT,
    importance   TEXT,            -- alta / media / bassa
    category     TEXT,
    needs_reply  INTEGER DEFAULT 0,
    next_action  TEXT,
    archived     INTEGER DEFAULT 0,
    last_message_at INTEGER,
    analyzed_at  INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,  -- id WhatsApp del messaggio
    wa_id       TEXT NOT NULL,
    direction   TEXT NOT NULL,     -- in (cliente) / out (tu)
    type        TEXT NOT NULL,     -- text / audio / image / document / ...
    text        TEXT,
    transcript  TEXT,
    media_id    TEXT,
    media_path  TEXT,
    status      TEXT DEFAULT 'ok', -- ok / pending / error
    error       TEXT,
    timestamp   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_contact ON messages (wa_id, timestamp);
CREATE TABLE IF NOT EXISTS appointments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id       TEXT NOT NULL,
    title       TEXT NOT NULL,
    start       TEXT,              -- ISO 8601 locale, es. 2026-11-24T09:30
    end         TEXT,
    location    TEXT,
    notes       TEXT,
    status      TEXT DEFAULT 'proposto',  -- proposto / confermato / annullato / fatto
    manual      INTEGER DEFAULT 0, -- 1 = modificato a mano: l'analisi non lo tocca piu'
    created_at  INTEGER,
    updated_at  INTEGER
);
CREATE TABLE IF NOT EXISTS digests (
    day         TEXT PRIMARY KEY,
    text        TEXT,
    created_at  INTEGER
);
"""


def init_db(path: Path | None = None) -> None:
    path = path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


# --- contatti -----------------------------------------------------------------

def upsert_contact(wa_id: str, name: str | None, ts: int) -> None:
    with connect() as c:
        c.execute(
            """INSERT INTO contacts (wa_id, name, last_message_at) VALUES (?, ?, ?)
               ON CONFLICT(wa_id) DO UPDATE SET
                 name = COALESCE(excluded.name, contacts.name),
                 last_message_at = MAX(COALESCE(contacts.last_message_at, 0), excluded.last_message_at),
                 archived = 0""",
            (wa_id, name, ts),
        )


def get_contact(wa_id: str) -> dict[str, Any] | None:
    with connect() as c:
        r = c.execute("SELECT * FROM contacts WHERE wa_id = ?", (wa_id,)).fetchone()
        return dict(r) if r else None


def list_contacts(include_archived: bool = False) -> list[dict[str, Any]]:
    where = "" if include_archived else "WHERE archived = 0"
    with connect() as c:
        return rows(c.execute(
            f"""SELECT c.*,
                  (SELECT COUNT(*) FROM messages m WHERE m.wa_id = c.wa_id) AS message_count,
                  (SELECT COUNT(*) FROM appointments a WHERE a.wa_id = c.wa_id
                      AND a.status IN ('proposto','confermato')) AS open_appointments
                FROM contacts c {where}
                ORDER BY CASE c.importance WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,
                         c.needs_reply DESC, c.last_message_at DESC"""
        ))


def save_analysis(wa_id: str, summary: str, importance: str, category: str,
                  needs_reply: bool, next_action: str | None) -> None:
    with connect() as c:
        c.execute(
            """UPDATE contacts SET summary = ?, importance = ?, category = ?, needs_reply = ?,
                      next_action = ?, analyzed_at = ? WHERE wa_id = ?""",
            (summary, importance, category, int(needs_reply), next_action, int(time.time()), wa_id),
        )


def set_contact_flags(wa_id: str, *, archived: bool | None = None,
                      needs_reply: bool | None = None) -> None:
    with connect() as c:
        if archived is not None:
            c.execute("UPDATE contacts SET archived = ? WHERE wa_id = ?", (int(archived), wa_id))
        if needs_reply is not None:
            c.execute("UPDATE contacts SET needs_reply = ? WHERE wa_id = ?", (int(needs_reply), wa_id))


# --- messaggi -----------------------------------------------------------------

def insert_message(msg: dict[str, Any]) -> bool:
    """Salva un messaggio. Ritorna False se era gia' presente (Meta a volte reinvia)."""
    with connect() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO messages
               (id, wa_id, direction, type, text, media_id, status, timestamp)
               VALUES (:id, :wa_id, :direction, :type, :text, :media_id, :status, :timestamp)""",
            msg,
        )
        return cur.rowcount == 1


def update_message(msg_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE messages SET {cols} WHERE id = ?", (*fields.values(), msg_id))


def get_messages(wa_id: str, limit: int = 200, since: int | None = None) -> list[dict[str, Any]]:
    with connect() as c:
        return rows(c.execute(
            """SELECT * FROM (
                 SELECT * FROM messages WHERE wa_id = ? AND timestamp >= ?
                 ORDER BY timestamp DESC LIMIT ?)
               ORDER BY timestamp ASC""",
            (wa_id, since or 0, limit),
        ))


def pending_audio() -> list[dict[str, Any]]:
    with connect() as c:
        return rows(c.execute(
            "SELECT * FROM messages WHERE type = 'audio' AND status = 'pending' ORDER BY timestamp"
        ))


def messages_between(start: int, end: int) -> list[dict[str, Any]]:
    with connect() as c:
        return rows(c.execute(
            """SELECT m.*, c.name FROM messages m LEFT JOIN contacts c ON c.wa_id = m.wa_id
               WHERE m.timestamp >= ? AND m.timestamp < ? ORDER BY m.wa_id, m.timestamp""",
            (start, end),
        ))


# --- appuntamenti -------------------------------------------------------------

APPOINTMENT_FIELDS = ("title", "start", "end", "location", "notes", "status")


def list_appointments(wa_id: str | None = None, include_closed: bool = True) -> list[dict[str, Any]]:
    sql = """SELECT a.*, c.name AS contact_name FROM appointments a
             LEFT JOIN contacts c ON c.wa_id = a.wa_id WHERE 1=1"""
    params: list[Any] = []
    if wa_id:
        sql += " AND a.wa_id = ?"
        params.append(wa_id)
    if not include_closed:
        sql += " AND a.status IN ('proposto','confermato')"
    sql += " ORDER BY a.start IS NULL, a.start"
    with connect() as c:
        return rows(c.execute(sql, params))


def create_appointment(wa_id: str, data: dict[str, Any], manual: bool = False) -> int:
    now = int(time.time())
    with connect() as c:
        cur = c.execute(
            """INSERT INTO appointments (wa_id, title, start, end, location, notes, status,
                                         manual, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (wa_id, data.get("title") or "Appuntamento", data.get("start"), data.get("end"),
             data.get("location"), data.get("notes"), data.get("status") or "proposto",
             int(manual), now, now),
        )
        return int(cur.lastrowid)


def update_appointment(appt_id: int, data: dict[str, Any], manual: bool = False) -> bool:
    fields = {k: data[k] for k in APPOINTMENT_FIELDS if k in data}
    if not fields:
        return False
    fields["updated_at"] = int(time.time())
    if manual:
        fields["manual"] = 1
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        cur = c.execute(f"UPDATE appointments SET {cols} WHERE id = ?", (*fields.values(), appt_id))
        return cur.rowcount == 1


def delete_appointment(appt_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM appointments WHERE id = ?", (appt_id,))


# --- riepiloghi giornalieri ---------------------------------------------------

def save_digest(day: str, text: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO digests (day, text, created_at) VALUES (?, ?, ?)",
            (day, text, int(time.time())),
        )


def get_digest(day: str) -> dict[str, Any] | None:
    with connect() as c:
        r = c.execute("SELECT * FROM digests WHERE day = ?", (day,)).fetchone()
        return dict(r) if r else None


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
