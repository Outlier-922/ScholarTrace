"""SQLite repository for the local development edition.

All database access is scoped to a short-lived connection. The path is resolved
at call time so isolated tests and a future storage adapter use the same API.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def data_dir() -> Path:
    value = os.environ.get("SCHOLARTRACE_DATA_DIR")
    root = Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[2] / "data"
    root.mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(exist_ok=True)
    return root


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(data_dir() / "scholartrace.sqlite3", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS works (
            id TEXT PRIMARY KEY, canonical_title TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS versions (
            id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
            label TEXT, arxiv_id TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
            version_id TEXT NOT NULL REFERENCES versions(id), parse_run_id TEXT NOT NULL,
            filename TEXT NOT NULL, file_path TEXT NOT NULL, sha256 TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '[]',
            page_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            status TEXT NOT NULL, progress_pages INTEGER NOT NULL DEFAULT 0,
            blocks_count INTEGER NOT NULL DEFAULT 0, version_label TEXT, arxiv_id TEXT,
            duplicate_of TEXT, duplicate_kind TEXT, version_recommendation TEXT,
            warnings TEXT NOT NULL DEFAULT '[]', content_fingerprint TEXT,
            fingerprint_tokens TEXT NOT NULL DEFAULT '[]', job_id TEXT NOT NULL,
            error TEXT, deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS parse_runs (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            parser_name TEXT NOT NULL, parser_version TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pages (
            document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            parse_run_id TEXT NOT NULL, number INTEGER NOT NULL,
            width REAL NOT NULL, height REAL NOT NULL, rotation INTEGER NOT NULL DEFAULT 0,
            cropbox TEXT, status TEXT NOT NULL, text_length INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (document_id, parse_run_id, number)
        );
        CREATE TABLE IF NOT EXISTS blocks (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            parse_run_id TEXT NOT NULL, page INTEGER NOT NULL, type TEXT NOT NULL,
            text TEXT NOT NULL, bbox TEXT NOT NULL, order_index INTEGER NOT NULL,
            heading_level INTEGER, section_path TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_blocks_document ON blocks(document_id, page, order_index);
        CREATE TABLE IF NOT EXISTS outlines (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            title TEXT NOT NULL, level INTEGER NOT NULL, page INTEGER NOT NULL,
            block_id TEXT NOT NULL, order_index INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reference_entries (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            marker TEXT, text TEXT NOT NULL, page INTEGER NOT NULL, block_id TEXT NOT NULL,
            doi TEXT, arxiv_id TEXT
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            type TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL,
            error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """)


def _document(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value["authors"] = json.loads(value.get("authors") or "[]")
    value["warnings"] = json.loads(value.get("warnings") or "[]")
    value["pdf_url"] = f"/api/documents/{value['id']}/pdf"
    # Filesystem paths and whole-document fingerprint material are internal.
    for key in ("file_path", "fingerprint_tokens", "deleted", "content_fingerprint"):
        value.pop(key, None)
    return value


def list_documents() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM documents WHERE deleted=0 ORDER BY created_at DESC").fetchall()
        return [_document(row) for row in rows]


def get_document(document_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=? AND deleted=0", (document_id,)).fetchone()
        return _document(row) if row else None


def get_internal_document(document_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=? AND deleted=0", (document_id,)).fetchone()
        return dict(row) if row else None


def find_by_hash(sha256: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM documents WHERE sha256=? AND deleted=0", (sha256,)).fetchone()
        return _document(row) if row else None


def create_document(filename: str, path: str, sha256: str, page_count: int) -> dict[str, Any]:
    document_id, work_id, version_id = new_id("doc"), new_id("work"), new_id("version")
    run_id, job_id, created = new_id("parse"), new_id("job"), now()
    title = Path(filename).stem
    with connection() as conn:
        conn.execute("INSERT INTO works VALUES (?, ?, ?)", (work_id, title, created))
        conn.execute("INSERT INTO versions VALUES (?, ?, NULL, NULL, ?)", (version_id, work_id, created))
        conn.execute("""INSERT INTO documents
            (id, work_id, version_id, parse_run_id, filename, file_path, sha256, title,
             page_count, created_at, status, job_id)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
            (document_id, work_id, version_id, run_id, filename, path, sha256, title, page_count, created, job_id))
        conn.execute("INSERT INTO parse_runs VALUES (?, ?, 'PyMuPDF native baseline', '1', 'queued', ?, NULL)",
                     (run_id, document_id, created))
        conn.execute("INSERT INTO jobs VALUES (?, ?, 'parse_pdf', 'queued', 0, ?, '等待解析', NULL, ?, ?)",
                     (job_id, document_id, page_count, created, created))
    return get_document(document_id)  # type: ignore[return-value]


def update_document(document_id: str, **values: Any) -> None:
    allowed = {"title", "authors", "page_count", "status", "progress_pages", "blocks_count",
               "version_label", "arxiv_id", "duplicate_of", "duplicate_kind", "version_recommendation",
               "warnings", "content_fingerprint", "fingerprint_tokens", "error", "work_id"}
    selected = {key: value for key, value in values.items() if key in allowed}
    if not selected:
        return
    for key in ("authors", "warnings", "fingerprint_tokens"):
        if key in selected:
            selected[key] = json.dumps(selected[key], ensure_ascii=False)
    with connection() as conn:
        conn.execute(f"UPDATE documents SET {', '.join(f'{key}=?' for key in selected)} WHERE id=? AND deleted=0",
                     (*selected.values(), document_id))
        if "title" in selected:
            conn.execute("UPDATE works SET canonical_title=? WHERE id=(SELECT work_id FROM documents WHERE id=?)",
                         (selected["title"], document_id))
        if "version_label" in selected or "arxiv_id" in selected or "work_id" in selected:
            row = conn.execute("SELECT version_id, work_id, version_label, arxiv_id FROM documents WHERE id=?",
                               (document_id,)).fetchone()
            if row:
                conn.execute("UPDATE versions SET work_id=?, label=?, arxiv_id=? WHERE id=?",
                             (row["work_id"], row["version_label"], row["arxiv_id"], row["version_id"]))


def update_job(job_id: str, status: str, message: str, progress: int | None = None,
               error: str | None = None) -> None:
    with connection() as conn:
        if progress is None:
            conn.execute("UPDATE jobs SET status=?, message=?, error=?, updated_at=? WHERE id=?",
                         (status, message, error, now(), job_id))
        else:
            conn.execute("UPDATE jobs SET status=?, message=?, progress=?, error=?, updated_at=? WHERE id=?",
                         (status, message, progress, error, now(), job_id))


def get_job(job_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("""SELECT jobs.* FROM jobs JOIN documents ON documents.id=jobs.document_id
                              WHERE jobs.id=? AND documents.deleted=0""", (job_id,)).fetchone()
        return dict(row) if row else None


def set_run_status(parse_run_id: str, status: str) -> None:
    with connection() as conn:
        conn.execute("UPDATE parse_runs SET status=?, finished_at=? WHERE id=?",
                     (status, now() if status in {"ready", "partial", "failed"} else None, parse_run_id))


def save_page(document_id: str, parse_run_id: str, number: int, width: float, height: float,
              rotation: int, cropbox: list[float], status: str, blocks: list[dict[str, Any]]) -> None:
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM documents WHERE id=? AND deleted=0", (document_id,)).fetchone():
            return
        # Page replacement is atomic, so an interrupted page is safe to retry.
        conn.execute("DELETE FROM reference_entries WHERE document_id=? AND page=?", (document_id, number))
        conn.execute("DELETE FROM outlines WHERE document_id=? AND page=?", (document_id, number))
        conn.execute("DELETE FROM blocks WHERE document_id=? AND parse_run_id=? AND page=?",
                     (document_id, parse_run_id, number))
        conn.execute("INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (document_id, parse_run_id, number, width, height, rotation,
                      json.dumps(cropbox), status, sum(len(block.get("text", "")) for block in blocks)))
        for block in blocks:
            conn.execute("INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (block["id"], document_id, parse_run_id, number, block["type"], block["text"],
                          json.dumps(block["bbox"]), block["order"], block.get("heading_level"),
                          json.dumps(block.get("section_path", []), ensure_ascii=False),
                          json.dumps(block.get("metadata", {}), ensure_ascii=False)))
            if block["type"] in {"heading", "title"}:
                conn.execute("INSERT INTO outlines VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (new_id("outline"), document_id, block["text"], block.get("heading_level") or 1,
                              number, block["id"], block["order"]))
        completed = conn.execute("SELECT count(*) FROM pages WHERE document_id=? AND parse_run_id=?",
                                 (document_id, parse_run_id)).fetchone()[0]
        count = conn.execute("SELECT count(*) FROM blocks WHERE document_id=? AND parse_run_id=?",
                             (document_id, parse_run_id)).fetchone()[0]
        conn.execute("UPDATE documents SET progress_pages=?, blocks_count=? WHERE id=? AND deleted=0",
                     (completed, count, document_id))


def save_references(document_id: str, references: list[dict[str, Any]]) -> None:
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM documents WHERE id=? AND deleted=0", (document_id,)).fetchone():
            return
        conn.execute("DELETE FROM reference_entries WHERE document_id=?", (document_id,))
        for ref in references:
            conn.execute("INSERT INTO reference_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (ref.get("id") or new_id("ref"), document_id, ref.get("marker"), ref["text"],
                          ref["page"], ref["block_id"], ref.get("doi"), ref.get("arxiv_id")))


def get_structure(document_id: str, page: int | None = None) -> dict[str, Any] | None:
    document = get_document(document_id)
    if not document:
        return None
    with connection() as conn:
        pages = [dict(row) for row in conn.execute(
            "SELECT number,width,height,rotation,cropbox,status,text_length FROM pages WHERE document_id=? AND parse_run_id=? ORDER BY number",
            (document_id, document["parse_run_id"]))]
        for page_record in pages:
            page_record["cropbox"] = json.loads(page_record["cropbox"] or "[]")
        blocks = []
        query = "SELECT * FROM blocks WHERE document_id=? AND parse_run_id=?"
        parameters: list[Any] = [document_id, document["parse_run_id"]]
        if page is not None:
            query += " AND page=?"
            parameters.append(page)
        query += " ORDER BY page,order_index"
        for row in conn.execute(query, parameters):
            block = dict(row)
            block["bbox"] = json.loads(block["bbox"])
            block["section_path"] = json.loads(block["section_path"])
            block["metadata"] = json.loads(block["metadata"])
            block["order"] = block.pop("order_index")
            blocks.append(block)
        outlines = [dict(row) for row in conn.execute(
            "SELECT id,title,level,page,block_id FROM outlines WHERE document_id=? ORDER BY page,order_index", (document_id,))]
        references = [dict(row) for row in conn.execute(
            "SELECT id,marker,text,page,block_id,doi,arxiv_id FROM reference_entries WHERE document_id=? ORDER BY page,rowid", (document_id,))]
        for reference in references:
            reference["is_candidate"] = True
            reference["verification_status"] = "unverified_native_candidate"
    return {"document_id": document_id, "parse_run_id": document["parse_run_id"], "pages": pages,
            "blocks": blocks, "outline": outlines, "references": references}


def _dedup_candidates(conn: sqlite3.Connection, document_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("""SELECT id,title,work_id,arxiv_id,version_label,fingerprint_tokens,
                           created_at,duplicate_of,status FROM documents WHERE deleted=0 ORDER BY created_at""").fetchall()
    records = {row["id"]: dict(row) for row in rows}
    result, roots = [], set()
    for value in records.values():
        if value["id"] == document_id or value["status"] not in {"ready", "partial"}:
            continue
        # Canonicalize existing links. Reprocessing a root must never make it
        # point back to one of its own duplicate children.
        root, visited = value, set()
        while root.get("duplicate_of") in records and root["id"] not in visited:
            visited.add(root["id"])
            root = records[root["duplicate_of"]]
        if root["id"] == document_id or root["id"] in roots:
            continue
        roots.add(root["id"])
        candidate = dict(root)
        candidate["fingerprint_tokens"] = json.loads(candidate["fingerprint_tokens"] or "[]")
        result.append(candidate)
    return result


def dedup_candidates(document_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        return _dedup_candidates(conn, document_id)


def finalize_document(document_id: str, status: str, warnings: list[str], content_hash: str,
                      hashes: list[str], resolve_duplicate: Callable[[list[dict[str, Any]]], dict[str, Any]],
                      message: str) -> bool:
    """Atomically compare finished documents, merge identity, and become ready.

    BEGIN IMMEDIATE serializes completion across parse processes. A simultaneous
    upload cannot observe both papers as processing and miss their relationship.
    The resolver is pure and only consumes candidates from this transaction.
    """
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        document = conn.execute("SELECT * FROM documents WHERE id=? AND deleted=0", (document_id,)).fetchone()
        if not document:
            return False
        duplicate = resolve_duplicate(_dedup_candidates(conn, document_id))
        values: dict[str, Any] = {"status": status, "warnings": json.dumps(warnings, ensure_ascii=False),
                                  "content_fingerprint": content_hash,
                                  "fingerprint_tokens": json.dumps(hashes)}
        values.update({key: value for key, value in duplicate.items()
                       if key in {"duplicate_of", "duplicate_kind", "version_recommendation", "work_id"}})
        conn.execute(f"UPDATE documents SET {', '.join(f'{key}=?' for key in values)} WHERE id=? AND deleted=0",
                     (*values.values(), document_id))
        if "work_id" in values:
            conn.execute("UPDATE versions SET work_id=? WHERE id=?", (values["work_id"], document["version_id"]))
        finished = now()
        conn.execute("UPDATE parse_runs SET status=?, finished_at=? WHERE id=?",
                     (status, finished, document["parse_run_id"]))
        conn.execute("UPDATE jobs SET status=?, message=?, progress=?, error=NULL, updated_at=? WHERE id=?",
                     (status, message, document["page_count"], finished, document["job_id"]))
    return True


def delete_document(document_id: str) -> bool:
    with connection() as conn:
        row = conn.execute("SELECT file_path FROM documents WHERE id=? AND deleted=0", (document_id,)).fetchone()
        if not row:
            return False
        # Keep a tombstone ID for historical source references; do not redirect them.
        conn.execute("UPDATE documents SET deleted=1, sha256=sha256 || ':' || id, status='deleted' WHERE id=?", (document_id,))
        conn.execute("DELETE FROM reference_entries WHERE document_id=?", (document_id,))
        conn.execute("DELETE FROM outlines WHERE document_id=?", (document_id,))
        conn.execute("DELETE FROM blocks WHERE document_id=?", (document_id,))
        conn.execute("DELETE FROM pages WHERE document_id=?", (document_id,))
        path = Path(row["file_path"]).resolve()
    uploads = (data_dir() / "uploads").resolve()
    if path.is_relative_to(uploads):
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            # A Windows parse process may still hold the file. Its finally
            # block and the next startup both attempt this tombstone cleanup.
            pass
    return True


def cleanup_deleted_files() -> None:
    with connection() as conn:
        paths = [row[0] for row in conn.execute("SELECT file_path FROM documents WHERE deleted=1")]
    uploads = (data_dir() / "uploads").resolve()
    for value in paths:
        path = Path(value).resolve()
        if path.is_relative_to(uploads):
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                pass


def stats() -> dict[str, int]:
    with connection() as conn:
        row = conn.execute("""SELECT count(*) AS document_count,
            coalesce(sum(CASE WHEN status='ready' THEN 1 ELSE 0 END),0) AS ready_count,
            coalesce(sum(page_count),0) AS total_pages,
            coalesce(sum(blocks_count),0) AS blocks_count
            FROM documents WHERE deleted=0""").fetchone()
        return dict(row)


def resumable_documents() -> list[str]:
    with connection() as conn:
        return [row[0] for row in conn.execute(
            "SELECT id FROM documents WHERE deleted=0 AND status IN ('queued','processing')")]
