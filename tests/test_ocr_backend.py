"""OCR coordinate mapping, profile upgrades and immutable parse snapshots."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest

from conftest import upload_pdf, wait_for_document


@pytest.fixture(autouse=True)
def isolate_added_documents(client):
    before = {item["id"] for item in client.get("/api/documents").json()["items"]}
    yield
    for item in client.get("/api/documents").json()["items"]:
        if item["id"] not in before:
            client.delete(f"/api/documents/{item['id']}")


def test_ocr_coordinates_use_unrotated_cropbox_and_retain_confidence(monkeypatch):
    from backend.app import ocr

    observed = {}

    def fake_engine(image):
        observed["shape"] = image.shape
        height, width = image.shape[:2]
        return SimpleNamespace(boxes=np.array([[[width * .1, height * .2], [width * .6, height * .2],
                                                [width * .6, height * .3], [width * .1, height * .3]]]),
                               txts=("SYNTHETIC OCR LINE",), scores=(.91,))

    monkeypatch.setattr(ocr, "_engine", lambda: fake_engine)
    monkeypatch.setenv("SCHOLARTRACE_OCR_SCALE", "2")
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.set_cropbox(pymupdf.Rect(60, 80, 540, 720))
        page.set_rotation(270)
        result = ocr.extract_ocr(page)
    assert observed["shape"] == (1280, 960, 3)
    assert result[0]["rect"] == pytest.approx([48, 128, 288, 192])
    assert result[0]["confidence"] == .91


def test_ocr_paragraphs_keep_line_level_source_boxes():
    from backend.app.parser import _ocr_paragraphs

    def line(text, rectangle):
        return {"text": text, "rect": rectangle, "font_size": 10, "body_size": 10,
                "bold": False, "confidence": .9}

    paragraphs = _ocr_paragraphs([line("The learning rate is", [50, 100, 250, 114]),
                                  line("0.0003 in this synthetic fixture.", [50, 118, 300, 132])], 600, 800)
    assert len(paragraphs) == 1
    assert "0.0003" in paragraphs[0]["text"]
    assert len(paragraphs[0]["ocr_lines"]) == 2
    assert paragraphs[0]["ocr_lines"][1]["bbox"] == pytest.approx([50 / 600, 118 / 800, .5, 132 / 800], abs=.000001)


def _unique_pdf(path, destination):
    with pymupdf.open(path) as source:
        metadata = dict(source.metadata)
        metadata["subject"] = "SYNTHETIC OCR PROFILE TEST " + uuid.uuid4().hex
        source.set_metadata(metadata)
        source.save(destination, no_new_id=True)
    return destination


def test_reprocess_preserves_old_blocks_outline_and_references(client, sample_pdfs, tmp_path, monkeypatch):
    from backend.app import main, parser, store

    source = _unique_pdf(sample_pdfs["v1"], tmp_path / "history.pdf")
    document = wait_for_document(client, upload_pdf(client, source)["id"])
    old_run = document["parse_run_id"]
    old = client.get(f"/api/documents/{document['id']}/structure").json()

    def synchronously_parse(application, document_id):
        parser.parse_document(document_id)
        return True

    monkeypatch.setattr(main, "_submit_parse", synchronously_parse)
    response = client.post(f"/api/documents/{document['id']}/reprocess", json={"ocr": False, "tables": False})
    assert response.status_code == 200, response.text
    current = wait_for_document(client, document["id"])
    assert current["parse_run_id"] != old_run
    previous = client.get(f"/api/documents/{document['id']}/structure", params={"parse_run_id": old_run}).json()
    assert previous["blocks"] == old["blocks"]
    assert previous["outline"] == old["outline"]
    assert previous["references"] == old["references"]
    new = store.get_structure(document["id"])
    assert set(block["id"] for block in new["blocks"]).isdisjoint(block["id"] for block in old["blocks"])
    history = client.get(f"/api/documents/{document['id']}/parse-runs").json()
    assert len(history["items"]) == 2
    assert history["current_parse_run_id"] == current["parse_run_id"]
    assert client.get(f"/api/documents/{document['id']}/structure?parse_run_id=parse_nonexistent").status_code == 404


def test_explicit_scan_ocr_upgrade_records_lines_and_new_parse_run(client, sample_pdfs, tmp_path, monkeypatch):
    from backend.app import main, ocr, parser

    source = _unique_pdf(sample_pdfs["scan"], tmp_path / "scan-upgrade.pdf")
    document = wait_for_document(client, upload_pdf(client, source)["id"])
    assert document["status"] == "partial"
    old_run = document["parse_run_id"]

    observed = {"calls": 0}

    def recognized(page):
        observed["calls"] += 1
        return [{"text": "Synthetic OCR Research", "rect": [50, 60, 350, 90], "font_size": 22.5,
                 "body_size": 10, "bold": False, "confidence": .96},
                {"text": "The synthetic learning rate is 0.0003.", "rect": [50, 130, 460, 145],
                 "font_size": 11.25, "body_size": 10, "bold": False, "confidence": .93}]

    monkeypatch.setattr(ocr, "runtime_status", lambda: {"ready": True})
    monkeypatch.setattr(ocr, "extract_ocr", recognized)
    monkeypatch.setattr(main, "_submit_parse", lambda app, identity: (parser.parse_document(identity) or True))
    response = client.post(f"/api/documents/{document['id']}/reprocess", json={"ocr": True, "tables": False})
    assert response.status_code == 200, response.text
    result = wait_for_document(client, document["id"])
    assert result["status"] == "ready", result
    structure = client.get(f"/api/documents/{document['id']}/structure").json()
    assert structure["pages"][0]["status"] == "ocr_ready"
    textual = [block for block in structure["blocks"] if block["text"]]
    assert textual
    assert all(block["metadata"]["extraction"] == "rapidocr" for block in textual)
    assert all(block["metadata"]["lines"] for block in textual)
    old = client.get(f"/api/documents/{document['id']}/structure", params={"parse_run_id": old_run}).json()
    assert old["pages"][0]["status"] == "needs_ocr"
    assert not any(block["text"] for block in old["blocks"])
    parser.parse_document(document["id"])
    assert observed["calls"] == 1, "completed OCR pages must be reused after restart/retry"
    after_resume = client.get(f"/api/documents/{document['id']}/structure").json()
    assert [block["id"] for block in after_resume["blocks"]] == [block["id"] for block in structure["blocks"]]


def test_unprepared_ocr_does_not_create_new_run(client, sample_pdfs, tmp_path, monkeypatch):
    from backend.app import ocr

    source = _unique_pdf(sample_pdfs["scan"], tmp_path / "unavailable-scan.pdf")
    document = wait_for_document(client, upload_pdf(client, source)["id"])
    monkeypatch.setattr(ocr, "runtime_status", lambda: {"ready": False, "reason": "Synthetic model unavailable"})
    response = client.post(f"/api/documents/{document['id']}/reprocess", json={"ocr": True})
    assert response.status_code == 503
    assert client.get(f"/api/documents/{document['id']}").json()["parse_run_id"] == document["parse_run_id"]


def test_stale_parse_job_does_not_change_new_active_run(client, sample_pdfs, tmp_path):
    from backend.app import parser, store

    source = _unique_pdf(sample_pdfs["distinct"], tmp_path / "stale.pdf")
    document = wait_for_document(client, upload_pdf(client, source)["id"])
    replacement = store.start_reprocess(document["id"], {"ocr": False, "tables": False})
    parser.parse_document(document["id"], expected_run_id=document["parse_run_id"])
    assert store.get_document(document["id"])["parse_run_id"] == replacement["parse_run_id"]
    assert store.get_document(document["id"])["status"] == "queued"
    parser.parse_document(document["id"], expected_run_id=replacement["parse_run_id"])
    assert store.get_document(document["id"])["status"] == "ready"


def test_health_reports_embedding_preparation_without_loading_model(client, monkeypatch):
    from backend.app import semantic

    status = {"supported": True, "enabled": False, "ready": True,
              "model": "synthetic-embedding-model", "reason": "prepared"}
    monkeypatch.setattr(semantic, "runtime_status", lambda: status)
    monkeypatch.setattr(semantic, "_load_model", lambda **kwargs: pytest.fail("health must not load ONNX"))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["embedding_status"] == status
    assert response.json()["capabilities"]["dense_ready"] is True
    assert response.json()["capabilities"]["dense_enabled"] is False


def test_document_deletion_purges_derived_vectors(client, sample_pdfs, tmp_path, monkeypatch):
    from backend.app import semantic

    source = _unique_pdf(sample_pdfs["distinct"], tmp_path / "delete-vectors.pdf")
    document = wait_for_document(client, upload_pdf(client, source)["id"])
    purged = []
    monkeypatch.setattr(semantic, "purge_document", purged.append)
    response = client.delete(f"/api/documents/{document['id']}")
    assert response.status_code == 200
    assert purged == [document["id"]]


def test_concurrent_reprocess_creation_is_mutually_exclusive(client, sample_pdfs, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from backend.app import store

    source = _unique_pdf(sample_pdfs["distinct"], tmp_path / "parallel-profile.pdf")
    document = wait_for_document(client, upload_pdf(client, source)["id"])

    def attempt():
        try:
            return store.start_reprocess(document["id"], {"ocr": False, "tables": False})
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))
    created = [result for result in results if result]
    assert len(created) == 1
    assert len(store.list_parse_runs(document["id"])) == 2
    assert store.get_document(document["id"])["parse_run_id"] == created[0]["parse_run_id"]
