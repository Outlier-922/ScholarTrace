"""Regressions for batch deduplication, worker failure, and data lifecycle."""

from __future__ import annotations

import hashlib
import uuid
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pymupdf

from conftest import upload_pdf, wait_for_document


def unique_copy(source: Path, target: Path, title: str) -> Path:
    with pymupdf.open(source) as document:
        metadata = dict(document.metadata)
        metadata["title"] = title
        metadata["subject"] = "SYNTHETIC regression fixture " + uuid.uuid4().hex
        document.set_metadata(metadata)
        document.save(target, deflate=True, no_new_id=True)
    return target


def test_same_hash_in_one_batch_reuses_one_document(client, sample_pdfs, tmp_path):
    source = unique_copy(sample_pdfs["v1"], tmp_path / "batch.pdf", "Hash batch fixture " + uuid.uuid4().hex)
    before = {item["id"] for item in client.get("/api/documents").json()["items"]}
    content = source.read_bytes()
    response = client.post(
        "/api/documents",
        files=[("files", ("first.pdf", content, "application/pdf")), ("files", ("renamed.pdf", content, "application/pdf"))],
    )
    assert response.status_code == 201, response.text
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["id"] == items[1]["id"]
    assert items[1]["duplicate_kind"] == "exact_file"
    assert items[1]["upload_outcome"] == "reused"
    assert wait_for_document(client, items[0]["id"])["status"] == "ready"
    after = {item["id"] for item in client.get("/api/documents").json()["items"]}
    assert after - before == {items[0]["id"]}


def test_two_versions_uploaded_together_share_one_work_without_cycles(client, sample_pdfs, tmp_path):
    title = "Parallel Work " + uuid.uuid4().hex
    first = unique_copy(sample_pdfs["v1"], tmp_path / "parallel-v1.pdf", title)
    second = unique_copy(sample_pdfs["v2"], tmp_path / "parallel-v2.pdf", title)
    response = client.post(
        "/api/documents",
        files=[("files", ("random-a.pdf", first.read_bytes(), "application/pdf")), ("files", ("random-b.pdf", second.read_bytes(), "application/pdf"))],
    )
    assert response.status_code == 201, response.text
    items = response.json()["items"]
    assert len(items) == 2
    finished = [wait_for_document(client, item["id"]) for item in items]
    assert all(item["status"] == "ready" for item in finished)
    assert finished[0]["work_id"] == finished[1]["work_id"]
    roots = [item for item in finished if not item.get("duplicate_of")]
    children = [item for item in finished if item.get("duplicate_of")]
    assert len(roots) == len(children) == 1, finished
    assert children[0]["duplicate_of"] == roots[0]["id"]


def test_broken_pool_is_replaced_before_new_upload_is_lost(client, sample_pdfs, tmp_path, monkeypatch):
    from backend.app import main

    source = unique_copy(sample_pdfs["distinct"], tmp_path / "pool-rebuild.pdf", "Pool rebuild " + uuid.uuid4().hex)
    healthy_pool = main.app.state.parse_executor
    observations = {"submits": 0, "shutdowns": 0, "rebuilds": 0}

    class BrokenExecutor:
        def submit(self, *args, **kwargs):
            observations["submits"] += 1
            raise BrokenProcessPool("synthetic worker failure")

        def shutdown(self, **kwargs):
            observations["shutdowns"] += 1

    def replacement_pool(**kwargs):
        observations["rebuilds"] += 1
        return healthy_pool

    monkeypatch.setattr(main.app.state, "parse_executor", BrokenExecutor())
    monkeypatch.setattr(main, "ProcessPoolExecutor", replacement_pool)
    uploaded = upload_pdf(client, source)
    document = wait_for_document(client, uploaded["id"])
    assert document["status"] == "ready", document
    assert observations == {"submits": 1, "shutdowns": 1, "rebuilds": 1}
    assert client.get(f"/api/documents/{document['id']}/pdf").content == source.read_bytes()


def test_submit_failure_retains_pdf_and_allows_retry(client, sample_pdfs, tmp_path, monkeypatch):
    from backend.app import main, store

    source = unique_copy(sample_pdfs["distinct"], tmp_path / "schedule-failure.pdf", "Schedule failure " + uuid.uuid4().hex)
    healthy_pool = main.app.state.parse_executor

    class UnavailableExecutor:
        def submit(self, *args, **kwargs):
            raise RuntimeError("synthetic scheduler unavailable")

    monkeypatch.setattr(main.app.state, "parse_executor", UnavailableExecutor())
    with source.open("rb") as stream:
        response = client.post("/api/documents", files=[("files", (source.name, stream, "application/pdf"))])
    assert response.status_code == 503, response.text
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    failed = store.find_by_hash(digest)
    assert failed is not None
    assert failed["status"] == "failed"
    internal = store.get_internal_document(failed["id"])
    assert Path(internal["file_path"]).read_bytes() == source.read_bytes()
    assert client.get(f"/api/documents/{failed['id']}/pdf").status_code == 200
    monkeypatch.setattr(main.app.state, "parse_executor", healthy_pool)
    retry = client.post(f"/api/documents/{failed['id']}/retry")
    assert retry.status_code == 200, retry.text
    assert wait_for_document(client, failed["id"])["status"] == "ready"


def test_deleted_document_cannot_be_repopulated_by_late_page_or_reference_save(client, sample_pdfs, tmp_path):
    from backend.app import store

    source = unique_copy(sample_pdfs["distinct"], tmp_path / "deleted.pdf", "Deleted write " + uuid.uuid4().hex)
    document = wait_for_document(client, upload_pdf(client, source)["id"])
    structure = client.get(f"/api/documents/{document['id']}/structure").json()
    page = structure["pages"][0]
    original = structure["blocks"][0]
    assert client.delete(f"/api/documents/{document['id']}").status_code == 200
    store.save_page(
        document["id"], document["parse_run_id"], 1, page["width"], page["height"],
        page["rotation"], page["cropbox"], "ready", [original],
    )
    store.save_references(document["id"], [{"text": "Synthetic late reference", "page": 1, "block_id": original["id"]}])
    with store.connection() as connection:
        for table in ("pages", "blocks", "outlines", "reference_entries"):
            count = connection.execute(f"SELECT count(*) FROM {table} WHERE document_id=?", (document["id"],)).fetchone()[0]
            assert count == 0, table
    assert client.get(f"/api/documents/{document['id']}/structure").status_code == 404


def test_structure_page_filter_returns_only_requested_page_blocks(client, sample_pdfs):
    document = wait_for_document(client, upload_pdf(client, sample_pdfs["v1"])["id"])
    full = client.get(f"/api/documents/{document['id']}/structure")
    assert full.status_code == 200, full.text
    assert {block["page"] for block in full.json()["blocks"]} == {1, 2}
    for page in (1, 2):
        response = client.get(f"/api/documents/{document['id']}/structure", params={"page": page})
        assert response.status_code == 200, response.text
        filtered = response.json()
        assert filtered["blocks"]
        assert {block["page"] for block in filtered["blocks"]} == {page}
        assert len(filtered["blocks"]) < len(full.json()["blocks"])
    assert client.get(f"/api/documents/{document['id']}/structure?page=0").status_code == 422
    assert client.get(f"/api/documents/{document['id']}/structure?page=3").status_code == 404
