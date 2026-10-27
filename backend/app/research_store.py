"""Version-bound reading artifacts in the same transactional source database."""
from __future__ import annotations

import json

from . import store


def init_db():
    with store.connection() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS comparisons (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS translations (
            cache_key TEXT PRIMARY KEY, document_id TEXT NOT NULL, parse_run_id TEXT NOT NULL,
            block_id TEXT NOT NULL, language TEXT NOT NULL, translated_text TEXT NOT NULL,
            metadata TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS translation_jobs (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, parse_run_id TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL);
        """)


def require_sources(connection, document_ids):
    for identity in document_ids:
        if not connection.execute("SELECT 1 FROM documents WHERE id=? AND deleted=0", (identity,)).fetchone():
            raise ValueError("source_deleted")


def save_comparison(payload):
    init_db()
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_sources(connection, [item["id"] for item in payload["documents"]])
        connection.execute("INSERT OR REPLACE INTO comparisons VALUES (?,?,?)", (payload["id"], json.dumps(payload, ensure_ascii=False), store.now()))


def get_comparison(identity):
    init_db()
    with store.connection() as connection:
        row = connection.execute("SELECT payload FROM comparisons WHERE id=?", (identity,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        try:
            require_sources(connection, [item["id"] for item in payload["documents"]])
        except ValueError:
            return None
        return payload


def get_translation(cache_key):
    init_db()
    with store.connection() as connection:
        row = connection.execute("""SELECT translations.* FROM translations JOIN documents ON documents.id=translations.document_id
                                  WHERE cache_key=? AND documents.deleted=0""", (cache_key,)).fetchone()
        return dict(row) if row else None


def save_translation(cache_key, document_id, parse_run_id, block_id, language, text, metadata):
    init_db()
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_sources(connection, [document_id])
        if not connection.execute("SELECT 1 FROM blocks WHERE id=? AND document_id=? AND parse_run_id=?", (block_id, document_id, parse_run_id)).fetchone():
            raise ValueError("source_block_missing")
        connection.execute("INSERT OR REPLACE INTO translations VALUES (?,?,?,?,?,?,?,?)", (cache_key, document_id, parse_run_id, block_id, language, text, json.dumps(metadata, ensure_ascii=False), store.now()))


def save_job(identity, document_id, parse_run_id, payload):
    init_db()
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_sources(connection, [document_id])
        connection.execute("INSERT OR REPLACE INTO translation_jobs VALUES (?,?,?,?,?)", (identity, document_id, parse_run_id, json.dumps(payload, ensure_ascii=False), store.now()))


def get_job(identity):
    init_db()
    with store.connection() as connection:
        row = connection.execute("""SELECT translation_jobs.payload FROM translation_jobs JOIN documents ON documents.id=translation_jobs.document_id
                                  WHERE translation_jobs.id=? AND documents.deleted=0""", (identity,)).fetchone()
        return json.loads(row["payload"]) if row else None


def resumable_jobs():
    init_db()
    with store.connection() as connection:
        rows = connection.execute("SELECT payload FROM translation_jobs JOIN documents ON documents.id=translation_jobs.document_id WHERE documents.deleted=0").fetchall()
        return [item["id"] for row in rows if (item := json.loads(row["payload"]))["status"] in {"queued", "processing"}]


def list_jobs(document_id, parse_run_id=None):
    init_db()
    with store.connection() as connection:
        try:
            require_sources(connection, [document_id])
        except ValueError:
            return []
        rows = connection.execute("SELECT payload FROM translation_jobs WHERE document_id=? ORDER BY created_at DESC LIMIT 50", (document_id,)).fetchall()
        jobs = [json.loads(row["payload"]) for row in rows]
        return [{key: value for key, value in job.items() if key not in {"items", "glossary"}} for job in jobs if parse_run_id is None or job["parse_run_id"] == parse_run_id]


def purge_document(document_id):
    init_db()
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM translations WHERE document_id=?", (document_id,))
        connection.execute("DELETE FROM translation_jobs WHERE document_id=?", (document_id,))
        for row in connection.execute("SELECT id,payload FROM comparisons").fetchall():
            if any(item["id"] == document_id for item in json.loads(row["payload"])["documents"]):
                connection.execute("DELETE FROM comparisons WHERE id=?", (row["id"],))
