"""FastAPI entry point for ScholarTrace's local research workspace."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import logging
import multiprocessing
import os
import sqlite3
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pymupdf as fitz
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from . import __version__, ocr, semantic, store
from .parser import parse_document

logger = logging.getLogger(__name__)
_upload_lock = threading.Lock()
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    research_store.init_db()
    translation.resume_jobs()
    literature.start()
    graph.repo.init_db()
    studio.repo.init_db()
    workflow.repo.init_db()
    model_status.init_db()
    workflow.start()
    workflow.repo.recover()
    store.cleanup_deleted_files()
    workers = max(1, min(4, int(os.environ.get("SCHOLARTRACE_PARSE_WORKERS", "2"))))
    # PyMuPDF must not run concurrently in multiple threads. Each parse worker
    # has its own interpreter/native state; SQLite remains shared via WAL.
    executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    app.state.parse_workers = workers
    app.state.pool_lock = threading.Lock()
    app.state.parse_executor = executor
    for document_id in store.resumable_documents():
        _submit_parse(app, document_id)
    yield
    await workflow.shutdown()
    await translation.shutdown()
    await literature.shutdown()
    app.state.parse_executor.shutdown(wait=True, cancel_futures=True)


def _submit_parse(application: FastAPI, document_id: str) -> bool:
    scheduled_document = store.get_document(document_id)
    if not scheduled_document:
        return False
    scheduled_run_id = scheduled_document["parse_run_id"]
    try:
        with application.state.pool_lock:
            try:
                future = application.state.parse_executor.submit(parse_document, document_id, scheduled_run_id)
            except BrokenProcessPool:
                # The old pool can never accept work again after a native crash.
                # Replace it once; original uploads remain available for retry.
                application.state.parse_executor.shutdown(wait=False, cancel_futures=True)
                application.state.parse_executor = ProcessPoolExecutor(
                    max_workers=application.state.parse_workers,
                    mp_context=multiprocessing.get_context("spawn"))
                future = application.state.parse_executor.submit(parse_document, document_id, scheduled_run_id)
    except Exception as exc:
        logger.error("Cannot schedule PDF job %s: %s", document_id, type(exc).__name__)
        document = store.get_document(document_id)
        if document and document["parse_run_id"] == scheduled_run_id:
            message = "暂时无法启动解析进程；原 PDF 已保留，可稍后重试。"
            store.update_document(document_id, status="failed", error=message)
            store.set_run_status(document["parse_run_id"], "failed")
            store.update_job(document["job_id"], "failed", message, error=type(exc).__name__)
        return False

    def completed(result):
        if result.cancelled():
            return  # A queued job remains resumable after server shutdown.
        error = result.exception()
        document = store.get_document(document_id)
        if error is not None and document and document["parse_run_id"] == scheduled_run_id:
            logger.error("PDF worker stopped for %s: %s", document_id, type(error).__name__)
            message = "解析进程异常退出；可重试任务，若持续失败请检查文件与日志。"
            store.update_document(document_id, status="failed", error=message)
            store.set_run_status(document["parse_run_id"], "failed")
            store.update_job(document["job_id"], "failed", message, error=type(error).__name__)

    future.add_done_callback(completed)
    return True


app = FastAPI(title="ScholarTrace · 研迹", version=__version__, lifespan=lifespan,
              description="版本化文献与可定位原文证据。本地开发版：多文对比与联读。")
origins = os.environ.get("SCHOLARTRACE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
                   allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                   allow_headers=["Content-Type", "Last-Event-ID"])


@app.get("/api/health")
def health() -> dict[str, Any]:
    ocr_status = ocr.runtime_status()
    embedding_status = semantic.runtime_status()
    return {"status": "ok", "version": __version__, "edition": "local-development",
            "stage": "intent-workflow-and-model-readiness",
            "model_configured": all(os.environ.get(key, "").strip()
                                    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")),
            "capabilities": {"pdf_upload": True, "native_pdf_parsing": True, "source_anchors": True,
                             "content_dedup_baseline": True, "page_checkpoint": True,
                             "ocr": ocr_status["ready"], "ocr_supported": ocr_status["supported"],
                             "ocr_enabled": ocr_status["enabled"], "ocr_ready": ocr_status["ready"],
                             "dense_supported": embedding_status["supported"],
                             "dense_enabled": embedding_status["enabled"], "dense_ready": embedding_status["ready"],
                             "structured_tables": importlib.util.find_spec(f"{__package__}.tables") is not None,
                             "formula_latex": False, "semantic_tables": False,
                             "citation_graph": True, "evidence_outline": True, "full_translation": False,
                             "writing_plans": True, "csv_plotting": True, "architecture_diagrams": True,
                             "intent_workflow": True, "model_diagnostics": True,
                             "multi_user_auth": False, "comparison_matrix": True,
                             "citation_navigation": True, "translation": True,
                             "literature_search": True, "arxiv_import": True, "topic_tracking": True},
            "ocr_status": {key: value for key, value in ocr_status.items() if key != "model_dir"},
            "embedding_status": embedding_status}


@app.get("/api/stats")
def stats() -> dict[str, int]:
    return store.stats()


@app.get("/api/documents")
def documents() -> dict[str, Any]:
    return {"items": store.list_documents()}


def _require_document(document_id: str) -> dict[str, Any]:
    document = store.get_document(document_id)
    if not document:
        raise HTTPException(404, "文献不存在或已删除")
    return document


async def _accept_upload(upload: UploadFile, request: Request) -> dict[str, Any]:
    filename = (upload.filename or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1][:255]
    filename = "".join(character for character in filename if ord(character) >= 32)
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(422, "仅支持 PDF 文件")
    max_bytes = max(1, int(os.environ.get("SCHOLARTRACE_MAX_UPLOAD_MB", "100"))) * 1024 * 1024
    max_pages = max(1, int(os.environ.get("SCHOLARTRACE_MAX_PAGES", "1500")))
    path = store.data_dir() / "uploads" / f"{store.new_id('asset')}.pdf"
    total, digest, first_chunk = 0, hashlib.sha256(), b""
    persisted_document_id = None
    try:
        with path.open("xb") as target:
            while chunk := await upload.read(1024 * 1024):
                if not first_chunk:
                    first_chunk = chunk[:1024]
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, f"文件超过 {max_bytes // (1024 * 1024)} MB 限制")
                digest.update(chunk)
                target.write(chunk)
        if not total or not first_chunk.lstrip().startswith(b"%PDF-"):
            raise HTTPException(422, "文件不是有效的 PDF")
        try:
            with fitz.open(path) as pdf:
                if pdf.needs_pass:
                    raise HTTPException(422, "暂不支持加密 PDF，请先提供可读取的版本")
                page_count = len(pdf)
                if page_count == 0:
                    raise HTTPException(422, "PDF 没有可读取的页面")
                if page_count > max_pages:
                    raise HTTPException(413, f"PDF 超过 {max_pages} 页限制")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "PDF 损坏或无法读取") from exc
        # Serialize the small hash-check/insert window; file I/O and parsing are
        # outside this lock. Unique SHA256 also protects cross-process races.
        with _upload_lock:
            previous = store.find_by_hash(digest.hexdigest())
            if previous:
                path.unlink(missing_ok=True)
                return {**previous, "duplicate_of": previous["id"], "duplicate_kind": "exact_file",
                        "version_recommendation": "该文件已存在，已复用原有解析结果。", "upload_outcome": "reused"}
            try:
                document = store.create_document(filename, str(path.resolve()), digest.hexdigest(), page_count)
                persisted_document_id = document["id"]
            except sqlite3.IntegrityError:
                previous = store.find_by_hash(digest.hexdigest())
                if not previous:
                    raise
                path.unlink(missing_ok=True)
                return {**previous, "duplicate_of": previous["id"], "duplicate_kind": "exact_file",
                        "version_recommendation": "该文件已存在，已复用原有解析结果。", "upload_outcome": "reused"}
        if not _submit_parse(request.app, document["id"]):
            raise HTTPException(503, {"message": "解析服务暂不可用，原 PDF 已保留，请重试该任务。",
                                      "document_id": document["id"]})
        return {**document, "upload_outcome": "created"}
    except Exception:
        if persisted_document_id is None:
            path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@app.post("/api/documents", status_code=201)
async def upload_documents(request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if len(files) > 20:
        for upload in files:
            await upload.close()
        raise HTTPException(422, "每批最多上传 20 个 PDF")
    items, errors = [], []
    for upload in files:
        try:
            items.append(await _accept_upload(upload, request))
        except HTTPException as exc:
            errors.append({"filename": upload.filename, "status": exc.status_code, "message": exc.detail})
    if not items and errors:
        raise HTTPException(errors[0]["status"], {"message": errors[0]["message"], "errors": errors})
    return {"items": items, "errors": errors}


@app.get("/api/documents/{document_id}")
def document_detail(document_id: str) -> dict[str, Any]:
    return _require_document(document_id)


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    authors: list[str] | None = None


@app.patch("/api/documents/{document_id}")
def update_document(document_id: str, patch: DocumentPatch) -> dict[str, Any]:
    document = _require_document(document_id)
    if document["status"] in {"queued", "processing"}:
        raise HTTPException(409, "请等待解析完成后修改元数据")
    values = patch.model_dump(exclude_none=True)
    if "title" in values:
        values["title"] = values["title"].strip()
        if not values["title"]:
            raise HTTPException(422, "标题不能为空")
    if "authors" in values:
        if len(values["authors"]) > 200 or any(len(author) > 300 for author in values["authors"]):
            raise HTTPException(422, "作者信息过长")
        values["authors"] = [author.strip() for author in values["authors"] if author.strip()]
    store.update_document(document_id, **values)
    return _require_document(document_id)


@app.delete("/api/documents/{document_id}")
def remove_document(document_id: str) -> dict[str, Any]:
    _require_document(document_id)
    if not store.delete_document(document_id):
        raise HTTPException(404, "文献不存在")
    semantic.purge_document(document_id)
    research_store.purge_document(document_id)
    literature.repo.purge_document(document_id)
    graph.repo.purge_document(document_id)
    studio.repo.purge_document(document_id)
    workflow.repo.purge_document(document_id)
    return {"deleted": True, "document_id": document_id}


@app.get("/api/documents/{document_id}/structure")
def document_structure(document_id: str, page: int | None = Query(default=None, ge=1),
                       parse_run_id: str | None = Query(default=None, max_length=100)) -> dict[str, Any]:
    document = _require_document(document_id)
    if page is not None and page > document["page_count"]:
        raise HTTPException(404, "页码超出文档范围")
    structure = store.get_structure(document_id, page=page, parse_run_id=parse_run_id)
    if structure is None:
        raise HTTPException(404, "文献不存在或已删除")
    return structure


@app.get("/api/documents/{document_id}/parse-runs")
def document_parse_runs(document_id: str) -> dict[str, Any]:
    document = _require_document(document_id)
    return {"items": store.list_parse_runs(document_id), "current_parse_run_id": document["parse_run_id"]}


class ReprocessRequest(BaseModel):
    model_config = {"extra": "forbid"}
    ocr: bool = False
    tables: bool = False


@app.post("/api/documents/{document_id}/reprocess")
def reprocess_document(document_id: str, options: ReprocessRequest, request: Request) -> dict[str, Any]:
    document = _require_document(document_id)
    if document["status"] in {"queued", "processing"}:
        raise HTTPException(409, "文献正在处理中，请等待当前任务完成")
    if options.ocr:
        status = ocr.runtime_status()
        if not status["ready"]:
            raise HTTPException(503, status["reason"])
    try:
        document = store.start_reprocess(document_id, options.model_dump())
    except ValueError as exc:
        raise HTTPException(409, "文献正在处理中，请稍后重试") from exc
    if document is None:
        raise HTTPException(404, "文献不存在或已删除")
    if not _submit_parse(request.app, document_id):
        raise HTTPException(503, "解析进程暂不可用，原 PDF 和历史解析均已保留")
    return document


@app.get("/api/documents/{document_id}/pdf")
def document_pdf(document_id: str) -> FileResponse:
    document = store.get_internal_document(document_id)
    if not document:
        raise HTTPException(404, "文献不存在或已删除")
    path = Path(document["file_path"]).resolve()
    if not path.is_relative_to((store.data_dir() / "uploads").resolve()) or not path.is_file():
        raise HTTPException(404, "原始 PDF 不可用")
    return FileResponse(path, media_type="application/pdf", filename=document["filename"],
                        content_disposition_type="inline")


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    if not store.get_job(job_id):
        raise HTTPException(404, "任务不存在")

    async def events():
        previous = None
        while not await request.is_disconnected():
            job = store.get_job(job_id)
            if job is None:
                yield "event: deleted\ndata: {}\n\n"
                break
            serialized = json.dumps(job, ensure_ascii=False)
            if serialized != previous:
                yield f"event: progress\ndata: {serialized}\n\n"
                previous = serialized
            if job["status"] in {"ready", "partial", "failed"}:
                break
            await asyncio.sleep(.5)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/documents/{document_id}/retry")
def retry_document(document_id: str, request: Request) -> dict[str, Any]:
    document = _require_document(document_id)
    if document["status"] != "failed":
        raise HTTPException(409, "仅失败任务可以重试；等待 OCR 的页面需要后续 OCR 模块")
    store.update_document(document_id, status="queued", error=None)
    store.update_job(document["job_id"], "queued", "等待重试")
    if not _submit_parse(request.app, document_id):
        raise HTTPException(503, "解析进程暂不可用，原 PDF 已保留，请稍后重试")
    return _require_document(document_id)


try:
    qa = importlib.import_module(f"{__package__}.qa")
except ModuleNotFoundError as exc:
    if exc.name != f"{__package__}.qa":
        raise
else:
    app.include_router(qa.router)

from . import comparison, reading, research_store, translation, literature, graph, studio, workflow, model_status
app.include_router(comparison.router)
app.include_router(reading.router)
app.include_router(translation.router)
app.include_router(literature.router)
app.include_router(graph.router)
app.include_router(studio.router)
app.include_router(workflow.router)
app.include_router(model_status.router)
