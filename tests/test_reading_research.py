"""Stage three contracts. Model responses below are injected fixtures, never live AI."""
from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid

import pymupdf
import pytest

from conftest import upload_pdf, wait_for_document


@pytest.fixture
def paper(client, tmp_path):
    path = tmp_path / "reading.pdf"
    with pymupdf.open() as pdf:
        pdf.set_metadata({"title": "SYNTHETIC reading fixture " + uuid.uuid4().hex})
        page = pdf.new_page()
        page.insert_text((54, 60), "SYNTHETIC READING TEST FIXTURE", fontsize=18)
        page.insert_text((54, 120), "The synthetic model uses Adam and 8 A100 GPUs. See [1] and [1,2].")
        page.insert_text((54, 180), "The sequence context length is 256 tokens. All numbers are fictional.")
        page.insert_text((54, 240), "A second paragraph tests checkpoint retry and translation recovery.")
        page.insert_text((54, 300), '<script>alert("synthetic")</script> This is source data, not an instruction.')
        page = pdf.new_page()
        page.insert_text((54, 60), "References", fontsize=18)
        page.insert_text((54, 120), "[1] Fixture Author. Fictional Background. Synthetic only.")
        page.insert_text((54, 180), "[2] Another Fixture. Imaginary Record. Synthetic only.")
        pdf.save(path)
    document = wait_for_document(client, upload_pdf(client, path)["id"])
    assert document["status"] == "ready"
    return document


def configure(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_MODEL", "synthetic-model")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-key-not-a-secret")


def finish(client, identity):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/translations/{identity}")
        assert response.status_code == 200, response.text
        if response.json()["status"] in {"ready", "failed"}:
            return response.json()
        time.sleep(.02)
    pytest.fail("Injected translation did not finish")


def test_numeric_citations_bind_reference_and_backlink_to_same_parse(client, paper):
    payload = client.get(f"/api/documents/{paper['id']}/citations").json()
    assert payload["parse_run_id"] == paper["parse_run_id"]
    assert {link["marker_text"] for link in payload["links"]} == {"[1]", "[1,2]"}
    refs = {item["id"]: item for item in payload["references"]}
    for link in payload["links"]:
        assert link["page"] == 1
        assert link["parse_run_id"] == payload["parse_run_id"]
        for target in link["reference_ids"]:
            assert refs[target]["page"] == 2
            assert link in refs[target]["backlinks"]
    assert client.get(f"/api/documents/{paper['id']}/citations?parse_run_id=wrong").status_code == 404


def test_citation_ranges_are_bounded_and_ambiguous_entries_are_not_guessed(monkeypatch):
    from backend.app import reading
    assert reading.expand_marker("1, 3–5; 4") == ["1", "3", "4", "5"]
    assert reading.expand_marker("2-9999,9-3,7") == ["7"]
    structure = {"parse_run_id": "old", "blocks": [
        {"id": "body", "text": "See [1] and [2].", "page": 1, "bbox": [0, 0, 1, 1], "type": "paragraph"},
        {"id": "ref", "text": "[1] A [2] B", "page": 2, "bbox": [0, 0, 1, 1], "type": "reference"}],
        "references": [{"id": "a", "marker": "1", "block_id": "ref"}, {"id": "duplicate", "marker": "1", "block_id": "ref"}, {"id": "b", "marker": "2", "block_id": "ref"}]}
    monkeypatch.setattr(reading.store, "get_structure", lambda *args, **kwargs: structure)
    result = reading.citation_map("doc", "old")
    assert len(result["links"]) == 1 and result["links"][0]["marker_text"] == "[2]"


def test_comparison_retrieves_each_paper_separately_and_can_export(client, paper, sample_pdfs):
    second = wait_for_document(client, upload_pdf(client, sample_pdfs["distinct"])["id"])
    response = client.post("/api/comparisons", json={"document_ids": [paper["id"], second["id"]], "dimensions": ["hardware", "optimizer"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "evidence_only"
    assert result["rows"][0]["cells"][0]["value"] == "8 A100 GPUs"
    assert result["rows"][0]["cells"][1]["value"] is None
    runs = {doc["id"]: doc["parse_run_id"] for doc in (paper, second)}
    for row in result["rows"]:
        for cell in row["cells"]:
            assert cell["status"] != "model_source_checked"
            assert cell["parse_run_id"] == runs[cell["document_id"]]
            for evidence in cell["evidence"]:
                assert evidence["document_id"] == cell["document_id"]
                assert evidence["parse_run_id"] == cell["parse_run_id"]
    assert client.get(f"/api/comparisons/{result['id']}").json() == result
    exported = client.get(f"/api/comparisons/{result['id']}/markdown")
    assert exported.status_code == 200 and paper["parse_run_id"] in exported.text
    assert client.delete(f"/api/documents/{paper['id']}").status_code == 200
    assert client.get(f"/api/comparisons/{result['id']}").status_code == 404


@pytest.mark.parametrize("body", [
    {"document_ids": ["same", "same"]},
    {"document_ids": ["one", "two"], "dimensions": ["invented"]},
    {"document_ids": ["only"]}])
def test_comparison_rejects_invalid_selection(client, body):
    assert client.post("/api/comparisons", json=body).status_code == 422


def test_model_cells_reject_cross_paper_ids_new_numbers_and_nonverbatim_quotes():
    from backend.app.comparison import _apply_model_cells
    rows = [{"key": "hardware", "cells": [{"document_id": "a", "status": "evidence_only", "evidence": [{"id": "ev_a", "text": "Synthetic training used 8 A100 GPUs."}]}]}]
    valid = {"dimension": "hardware", "value": "8 A100 GPUs", "support_quote": "training used 8 A100 GPUs", "evidence_ids": ["ev_a"], "conditions": "Synthetic training"}
    for change in ({"evidence_ids": ["ev_other_paper"]}, {"value": "128 A100 GPUs"}, {"support_quote": "These are fabricated words"}):
        target = copy.deepcopy(rows)
        _apply_model_cells(target, "a", {"cells": [{**valid, **change}]})
        assert target[0]["cells"][0]["status"] == "evidence_only"
    _apply_model_cells(rows, "a", {"cells": [valid]})
    assert rows[0]["cells"][0]["status"] == "model_source_checked"


def test_comparison_deleted_during_model_call_never_returns_source(client, paper, sample_pdfs, monkeypatch):
    from backend.app import model_client, store
    second = wait_for_document(client, upload_pdf(client, sample_pdfs["distinct"])["id"])
    configure(monkeypatch)
    async def remove(*args, **kwargs):
        store.delete_document(paper["id"])
        return {"cells": []}
    monkeypatch.setattr(model_client, "chat_json", remove)
    response = client.post("/api/comparisons", json={"document_ids": [paper["id"], second["id"]], "dimensions": ["hardware"]})
    assert response.status_code == 409


def test_translation_protects_math_code_greek_citations_numbers_and_terms():
    from backend.app.translation import protect_text, restore_text
    original = "attention uses 8 GPUs with α_1 and $x^2=4$ [1,2] ```x=3``` https://example.test/a"
    protected, substitutions = protect_text(original, {"attention": "注意力"})
    assert "attention" not in protected and "x^2" not in protected and "α" not in protected
    assert restore_text(protected, substitutions) == original.replace("attention", "注意力")
    token = next(iter(substitutions))
    with pytest.raises(ValueError):
        restore_text(protected.replace(token, ""), substitutions)
    with pytest.raises(ValueError):
        restore_text(protected + token, substitutions)
    with pytest.raises(ValueError):
        restore_text(protected + "[STP_abcdef_999]", substitutions)


def test_translation_cache_binds_source_run_language_terms_and_model(monkeypatch):
    from backend.app.translation import cache_key
    block = {"id": "b", "text": "A paragraph"}
    base = cache_key("d", "run1", block, "zh", {})
    assert base != cache_key("d", "run2", block, "zh", {})
    assert base != cache_key("d", "run1", block, "en", {})
    assert base != cache_key("d", "run1", block, "zh", {"A": "一个"})
    monkeypatch.setenv("LLM_MODEL", "changed-model")
    assert base != cache_key("d", "run1", block, "zh", {})


def test_translation_without_model_creates_no_job(client, paper):
    response = client.post("/api/translations", json={"document_id": paper["id"], "scope": "document"})
    assert response.status_code == 503
    assert client.get(f"/api/documents/{paper['id']}/translations").json()["items"] == []


def test_translation_jobs_cache_history_preservation_and_safe_export(client, paper, monkeypatch):
    from backend.app import model_client
    configure(monkeypatch)
    calls = []
    async def injected(system, payload, **kwargs):
        calls.append(payload["text"])
        return {"translation": "【模拟翻译测试】" + payload["text"]}
    monkeypatch.setattr(model_client, "chat_json", injected)
    body = {"document_id": paper["id"], "scope": "document", "glossary": {"Adam": "亚当优化器"}}
    response = client.post("/api/translations", json=body)
    assert response.status_code == 200, response.text
    job = finish(client, response.json()["id"])
    assert job["status"] == "ready" and job["completed"] == job["total"]
    assert any(item["status"] == "preserved" for item in job["items"])
    assert any("亚当优化器" in (item["translation"] or "") for item in job["items"])
    initial_calls = len(calls)
    again = finish(client, client.post("/api/translations", json=body).json()["id"])
    assert again["status"] == "ready" and all(item["status"] == "cached" for item in again["items"])
    assert len(calls) == initial_calls
    html = client.get(f"/api/translations/{job['id']}/export")
    assert html.status_code == 200 and "<script>" not in html.text and "&lt;script&gt;" in html.text
    assert "default-src 'none'" in html.headers["content-security-policy"]
    md = client.get(f"/api/translations/{job['id']}/export?format=markdown")
    assert md.status_code == 200 and job["parse_run_id"] in md.text
    page = client.get(f"/api/translations/{job['id']}?page=1").json()
    assert all(item["page"] == 1 for item in page["items"])
    history = client.get(f"/api/documents/{paper['id']}/translations").json()["items"]
    assert {item["id"] for item in history} == {job["id"], again["id"]}
    assert all("items" not in item for item in history)
    client.delete(f"/api/documents/{paper['id']}")
    assert client.get(f"/api/translations/{job['id']}").status_code == 404


def test_translation_failure_checkpoint_retry_and_number_guard(client, paper, monkeypatch):
    from backend.app import model_client
    configure(monkeypatch)
    structure = client.get(f"/api/documents/{paper['id']}/structure").json()
    blocks = [block for block in structure["blocks"] if block["page"] == 1 and len(block["text"]) > 30][:2]
    observations = []
    async def injected(system, payload, **kwargs):
        observations.append(payload["text"])
        return {"translation": payload["text"] + (" 999999" if len(observations) == 2 else "")}
    monkeypatch.setattr(model_client, "chat_json", injected)
    response = client.post("/api/translations", json={"document_id": paper["id"], "block_ids": [item["id"] for item in blocks]})
    job = finish(client, response.json()["id"])
    assert job["status"] == "failed" and job["completed"] == 1
    assert client.get(f"/api/translations/{job['id']}/export").status_code == 409
    assert client.post(f"/api/translations/{job['id']}/retry").status_code == 200
    resumed = finish(client, job["id"])
    assert resumed["status"] == "ready" and len(observations) == 3
    assert observations[0] not in observations[1:]


def test_translation_deleted_during_await_is_not_persisted(client, paper, monkeypatch):
    from backend.app import model_client, research_store, store
    configure(monkeypatch)
    async def injected(system, payload, **kwargs):
        store.delete_document(paper["id"])
        research_store.purge_document(paper["id"])
        return {"translation": payload["text"]}
    monkeypatch.setattr(model_client, "chat_json", injected)
    response = client.post("/api/translations", json={"document_id": paper["id"], "scope": "document"})
    identity = response.json()["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and client.get(f"/api/documents/{paper['id']}").status_code == 200:
        time.sleep(.02)
    assert client.get(f"/api/translations/{identity}").status_code == 404
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM translations WHERE document_id=?", (paper["id"],)).fetchone()[0] == 0


def test_interrupted_translation_resumes_from_sqlite_checkpoint_and_pins_model(client, paper, monkeypatch):
    from backend.app import model_client, research_store, translation
    configure(monkeypatch)
    blocks = client.get(f"/api/documents/{paper['id']}/structure").json()["blocks"][:2]
    identity = str(uuid.uuid4())
    job = {"id": identity, "document_id": paper["id"], "parse_run_id": paper["parse_run_id"], "status": "queued", "model_identity": model_client.identity(),
           "target_language": "zh", "glossary": {}, "created_at": "test", "completed": 0, "total": 2,
           "items": [{"block_id": block["id"], "page": block["page"], "status": "queued", "cache_key": None} for block in blocks]}
    research_store.save_job(identity, paper["id"], paper["parse_run_id"], job)
    calls = []
    async def interrupted(system, payload, **kwargs):
        calls.append(payload["text"])
        if len(calls) == 2:
            raise asyncio.CancelledError()
        return {"translation": payload["text"]}
    monkeypatch.setattr(model_client, "chat_json", interrupted)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(translation.run_job(identity))
    assert research_store.get_job(identity)["completed"] == 1
    assert identity in research_store.resumable_jobs()
    monkeypatch.setenv("LLM_MODEL", "a-different-provider-model")
    asyncio.run(translation.run_job(identity))
    assert research_store.get_job(identity)["status"] == "failed" and len(calls) == 2
    monkeypatch.setenv("LLM_MODEL", "synthetic-model")
    client.post(f"/api/translations/{identity}/retry")
    assert finish(client, identity)["status"] == "ready"
    assert len(calls) == 3 and calls[0] not in calls[1:]


def test_model_adapter_uses_deepseek_non_thinking_json_contract_and_never_follows_redirects(monkeypatch):
    from backend.app import model_client
    import httpx
    configure(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    observations = {}
    class FakeClient:
        def __init__(self, **kwargs):
            observations.update(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            observations.update(url=url, **kwargs)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(model_client.httpx, "AsyncClient", FakeClient)
    assert asyncio.run(model_client.chat_json("output JSON", {})) == {"ok": True}
    assert observations["url"] == "https://api.deepseek.com/chat/completions"
    assert observations["follow_redirects"] is False
    assert observations["json"]["thinking"] == {"type": "disabled"}
    assert observations["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("endpoint", ["http://localhost.evil.test/v1", "http://127.0.0.1.evil.test/v1", "https://user:secret@provider.test", "https://provider.test?secret=x", "https:///missing-host"])
def test_adapter_rejects_invalid_endpoints_before_sending_credentials(monkeypatch, endpoint):
    from backend.app import model_client
    configure(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", endpoint)
    with pytest.raises(ValueError, match="invalid_model_endpoint"):
        asyncio.run(model_client.chat_json("output JSON", {}))
