"""Optional, offline-first multilingual ONNX embeddings with a bounded cache.

Model preparation is explicit. Normal API calls never download weights. The
cache is keyed by immutable source/run/block/text identifiers and model name.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import threading
from collections import Counter
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import store

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
ROOT = Path(__file__).resolve().parents[2]
_model = None
_model_key = None
_lock = threading.RLock()


@lru_cache(maxsize=32)
def _digest_file(path: str, size: int, modified_ns: int) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _cache_model_id() -> str:
    marker = model_dir() / "prepared.json"
    fingerprint = hashlib.sha256(marker.read_bytes() if marker.is_file() else b"unprepared").hexdigest()[:20]
    return MODEL + "@" + fingerprint


def model_dir() -> Path:
    return Path(os.getenv("SCHOLARTRACE_EMBED_CACHE", str(ROOT / ".cache" / "embedding"))).resolve()


def runtime_status() -> dict[str, Any]:
    supported = importlib.util.find_spec("fastembed") is not None
    enabled = os.getenv("SCHOLARTRACE_ENABLE_DENSE", "false").lower() in {"1", "true", "yes"}
    marker = model_dir() / "prepared.json"
    ready = False
    try:
        prepared = json.loads(marker.read_text(encoding="utf-8"))
        ready = supported and prepared.get("model") == MODEL and prepared.get("dimension") == 384
        ready = ready and prepared.get("python") == f"{sys.version_info.major}.{sys.version_info.minor}"
        for item in prepared["files"]:
            path = Path(item["path"])
            stat = path.stat()
            ready = ready and stat.st_size == item["size"] and _digest_file(str(path), stat.st_size, stat.st_mtime_ns) == item["sha256"]
        ready = ready and bool(prepared["files"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"supported": supported, "enabled": enabled, "ready": bool(ready), "model": MODEL,
            "reason": "ready" if ready else "需要显式准备本地语义模型；请求期间不会下载模型。"}


def _load_model(*, download: bool = False):
    global _model, _model_key
    key = (str(model_dir()), _cache_model_id())
    with _lock:
        if _model is None or _model_key != key:
            if not download and not runtime_status()["ready"]:
                raise RuntimeError("embedding_model_not_prepared")
            from fastembed import TextEmbedding
            _model = TextEmbedding(MODEL, cache_dir=str(model_dir()), threads=2,
                                   providers=["CPUExecutionProvider"], local_files_only=not download)
            _model_key = key
        return _model


def prepare_model() -> dict[str, Any]:
    """Explicit setup step; this is the only embedding function allowing network."""
    with _lock:
        directory = model_dir()
        directory.mkdir(parents=True, exist_ok=True)
        model = _load_model(download=True)
        vector = next(iter(model.embed(["A document with verifiable research evidence."])))
        if len(vector) != 384:
            raise RuntimeError("unexpected_embedding_dimension")
        files = [{"path": str(path.resolve()), "size": path.stat().st_size, "sha256": _digest_file(str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)} for path in directory.rglob("*")
                 if path.is_file() and path.suffix in {".onnx", ".json", ".txt", ".model"} and path.name != "prepared.json"]
        if not any(item["path"].endswith(".onnx") for item in files):
            raise RuntimeError("embedding_weights_not_found")
        (directory / "prepared.json").write_text(json.dumps({"model": MODEL, "dimension": 384, "python": f"{sys.version_info.major}.{sys.version_info.minor}", "files": files}, indent=2), encoding="utf-8")
        return runtime_status()


@contextmanager
def _connection():
    connection = sqlite3.connect(store.data_dir() / "vectors.sqlite3", timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""CREATE TABLE IF NOT EXISTS vectors (
        chunk_id TEXT NOT NULL, model TEXT NOT NULL, document_id TEXT NOT NULL,
        parse_run_id TEXT NOT NULL, block_id TEXT NOT NULL, vector BLOB NOT NULL,
        PRIMARY KEY(chunk_id,model))""")
    connection.execute("CREATE INDEX IF NOT EXISTS vector_scope ON vectors(document_id,parse_run_id,model)")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def purge_document(document_id: str) -> None:
    if not (store.data_dir() / "vectors.sqlite3").exists():
        return
    with _lock:
        with _connection() as connection:
            connection.execute("DELETE FROM vectors WHERE document_id=?", (document_id,))


@lru_cache(maxsize=4)
def _read_tokenizer(path: str, modified_ns: int):
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(path)
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def _tokenizer():
    prepared = json.loads((model_dir() / "prepared.json").read_text(encoding="utf-8"))
    path = next(Path(item["path"]) for item in prepared["files"] if Path(item["path"]).name == "tokenizer.json")
    return _read_tokenizer(str(path), path.stat().st_mtime_ns)


def _chunks(sources: list[dict[str, Any]], tokenizer) -> list[dict[str, Any]]:
    result = []
    for source in sources:
        text = source["text"]
        # Actual model-tokenizer offsets, with room for special tokens. This
        # also covers Chinese text and facts near the tail of long paragraphs.
        offsets = tokenizer.encode(text, add_special_tokens=False).offsets
        for token_start in range(0, len(offsets), 100):
            token_end = min(token_start + 120, len(offsets))
            start, end = offsets[token_start][0], offsets[token_end - 1][1]
            part = text[start:end].strip()
            if len(part) < 12:
                continue
            identity = hashlib.sha256((source["document_id"] + source.get("parse_run_id", "") + source["block_id"] + str(start) + part).encode()).hexdigest()
            result.append({**source, "chunk_id": identity, "chunk_text": part, "token_count": token_end - token_start})
            if token_end >= len(offsets):
                break
    return result


def search(question: str, sources: list[dict[str, Any]], *, limit: int = 30,
           force_index: bool = False, progress=None) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    status = runtime_status()
    details = {**status, "used": False, "indexed_chunks": 0, "missing_chunks": 0}
    if not status["ready"] or (not status["enabled"] and not force_index):
        return [], details
    import numpy as np
    tokenizer = _tokenizer()
    chunks = _chunks(sources, tokenizer)
    if not chunks:
        return [], details
    # Keep each request bounded. Books can be pre-indexed explicitly by CLI.
    vectors: dict[str, Any] = {}
    cache_model = _cache_model_id()
    with _connection() as connection:
        for document_id, run in {(item["document_id"], item.get("parse_run_id", "")) for item in chunks}:
            for identity, blob in connection.execute(
                "SELECT chunk_id,vector FROM vectors WHERE document_id=? AND parse_run_id=? AND model=?",
                (document_id, run, cache_model),
            ):
                vector = np.frombuffer(blob, dtype=np.float32)
                if vector.size == 384:
                    vectors[identity] = vector
    missing = [item for item in chunks if item["chunk_id"] not in vectors]
    details["missing_chunks"] = len(missing)
    missing_per_run = Counter((item["document_id"], item.get("parse_run_id", "")) for item in missing)
    details["index_required"] = [
        {"document_id": identity, "parse_run_id": run, "missing_chunks": count}
        for (identity, run), count in sorted(missing_per_run.items())
    ]
    if progress:progress(len(chunks)-len(missing),len(chunks))
    maximum = int(os.getenv("SCHOLARTRACE_MAX_INLINE_EMBED_CHUNKS", "384"))
    if len(missing) > maximum and not force_index:
        details["reason"] = f"尚有 {len(missing)} 个片段需要预索引，当前请求使用 BM25，避免阻塞长书阅读。"
        return [], details
    with _lock:
        model = _load_model()
        # The model pads each batch to its longest sequence. Keep lengths close
        # without changing chunk keys, chunk boundaries or final ranking order.
        missing.sort(key=lambda item: item["token_count"])
        for offset in range(0, len(missing), 24):
            batch = missing[offset:offset + 24]
            embeddings = list(model.embed([item["chunk_text"] for item in batch], batch_size=24, parallel=None))
            document_ids = {item["document_id"] for item in batch}
            surviving = {identity for identity in document_ids if store.get_document(identity)}
            writes = []
            for item, vector in zip(batch, embeddings, strict=True):
                if item["document_id"] not in surviving:
                    continue
                vector = np.asarray(vector, dtype=np.float32)
                norm = np.linalg.norm(vector)
                if not np.isfinite(norm) or norm <= 0 or vector.size != 384:
                    raise RuntimeError("invalid_embedding_vector")
                vector /= norm
                vectors[item["chunk_id"]] = vector
                writes.append((item["chunk_id"], cache_model, item["document_id"], item.get("parse_run_id", ""),
                               item["block_id"], vector.tobytes()))
            with _connection() as connection:
                connection.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?,?)", writes)
            # Compensate across CLI/API processes: deletion can commit in the
            # source DB between the pre-insert check and this separate cache DB.
            for document_id in surviving:
                if not store.get_document(document_id):
                    purge_document(document_id)
            if progress:progress(min(len(chunks),len(chunks)-len(missing)+offset+len(batch)),len(chunks))
        query_offsets = tokenizer.encode(question, add_special_tokens=False).offsets
        if len(query_offsets) > 120:
            question = question[:query_offsets[119][1]]
            details["query_truncated"] = True
        query = np.asarray(next(iter(model.query_embed(question))), dtype=np.float32)
    query = query / max(float(np.linalg.norm(query)), 1e-12)
    scores: dict[str, float] = {}
    for item in chunks:
        vector = vectors.get(item["chunk_id"])
        if vector is None:
            continue
        score = float(np.dot(vector, query))
        key = item["id"]
        scores[key] = max(score, scores.get(key, -1.0))
    ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    details.update(used=True, indexed_chunks=len(chunks), missing_chunks=0,index_required=[], reason="已使用真实多语言 ONNX 语义向量；相似度不是事实置信度。")
    return ranking, details


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, identity in enumerate(dict.fromkeys(ranking), start=1):
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + rank)
    return scores
