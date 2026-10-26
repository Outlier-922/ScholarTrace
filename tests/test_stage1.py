"""Behavioral checks for the first real upload/read/evidence vertical slice."""

from __future__ import annotations

import hashlib

import pymupdf as fitz

from conftest import upload_pdf, wait_for_document


def test_health_reports_actual_capabilities(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"]
    assert isinstance(payload["capabilities"], dict)
    assert payload["model_configured"] is False


def test_sample_scan_has_no_native_text(sample_pdfs):
    with fitz.open(sample_pdfs["scan"]) as document:
        assert len(document) == 1
        assert not document[0].get_text().strip()


def test_upload_parses_actual_pdf_and_normalized_source_boxes(client, sample_pdfs):
    uploaded = upload_pdf(client, sample_pdfs["v1"])
    document = wait_for_document(client, uploaded["id"])
    assert document["status"] == "ready", document
    assert document["page_count"] == 2
    assert document["progress_pages"] == 2
    assert document["blocks_count"] > 0
    assert document["sha256"] == hashlib.sha256(sample_pdfs["v1"].read_bytes()).hexdigest()

    response = client.get(f"/api/documents/{document['id']}/structure")
    assert response.status_code == 200
    structure = response.json()
    assert len(structure["pages"]) == 2
    assert len(structure["blocks"]) == document["blocks_count"]
    text = "\n".join(block["text"] for block in structure["blocks"])
    assert "0.0003" in text
    assert "32" in text
    for block in structure["blocks"]:
        assert 1 <= block["page"] <= 2
        x0, y0, x1, y1 = block["bbox"]
        assert 0 <= x0 <= x1 <= 1
        assert 0 <= y0 <= y1 <= 1
    assert structure["outline"]

    source = client.get(f"/api/documents/{document['id']}/pdf")
    assert source.status_code == 200
    assert source.headers["content-type"].startswith("application/pdf")
    assert hashlib.sha256(source.content).hexdigest() == document["sha256"]


def test_renamed_exact_file_is_reused(client, sample_pdfs):
    first = upload_pdf(client, sample_pdfs["v1"])
    wait_for_document(client, first["id"])
    renamed = upload_pdf(client, sample_pdfs["v1"], "unrelated-filename.pdf")
    assert renamed["id"] == first["id"]
    assert renamed["duplicate_kind"] == "exact_file"
    assert renamed["duplicate_of"] == first["id"]


def test_changed_revision_is_flagged_without_deleting_either_file(client, sample_pdfs):
    first = wait_for_document(client, upload_pdf(client, sample_pdfs["v1"])["id"])
    second = wait_for_document(
        client, upload_pdf(client, sample_pdfs["v2"], "renamed-revision.pdf")["id"]
    )
    assert second["id"] != first["id"]
    assert second["duplicate_of"] == first["id"]
    assert second["duplicate_kind"] in {"content_overlap", "same_work_version"}
    assert client.get(f"/api/documents/{first['id']}/pdf").status_code == 200
    assert client.get(f"/api/documents/{second['id']}/pdf").status_code == 200


def test_related_but_distinct_paper_is_not_merged(client, sample_pdfs):
    source = wait_for_document(client, upload_pdf(client, sample_pdfs["v1"])["id"])
    distinct = wait_for_document(client, upload_pdf(client, sample_pdfs["distinct"])["id"])
    assert source["id"] != distinct["id"]
    assert not distinct.get("duplicate_of"), distinct


def test_scan_is_honestly_reported_as_needing_ocr(client, sample_pdfs):
    document = wait_for_document(client, upload_pdf(client, sample_pdfs["scan"])["id"])
    assert document["status"] == "partial", document
    assert document["warnings"], document
    structure = client.get(f"/api/documents/{document['id']}/structure").json()
    assert any(page["status"] == "needs_ocr" for page in structure["pages"])
    assert not any(block["text"].strip() for block in structure["blocks"])


def test_corrupt_pdf_fails_cleanly(client, sample_pdfs):
    with sample_pdfs["corrupt"].open("rb") as stream:
        response = client.post(
            "/api/documents", files=[("files", ("corrupt.pdf", stream, "application/pdf"))]
        )
    assert response.status_code == 422, response.text


def test_evidence_mode_returns_real_source_instead_of_claiming_llm_answer(client, sample_pdfs):
    document = wait_for_document(client, upload_pdf(client, sample_pdfs["v1"])["id"])
    response = client.post(
        "/api/ask",
        json={"question": "What learning rate and beta_1 are reported?", "document_ids": [document["id"]], "limit": 6},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "evidence_only"
    assert result["evidence"]
    structure = client.get(f"/api/documents/{document['id']}/structure").json()
    source_blocks = {block["id"]: block for block in structure["blocks"]}
    for evidence in result["evidence"]:
        assert evidence["document_id"] == document["id"]
        source = source_blocks[evidence["block_id"]]
        assert evidence["text"] in source["text"]
        assert evidence["page"] == source["page"]
        assert evidence["bbox"] == source["bbox"]
    assert any("0.0003" in item["text"] for item in result["evidence"])


def test_bibliography_is_not_evidence_for_training_hardware(client, sample_pdfs):
    document = wait_for_document(client, upload_pdf(client, sample_pdfs["v1"])["id"])
    response = client.post(
        "/api/ask",
        json={"question": "How many A100 GPUs were used for training?", "document_ids": [document["id"]], "limit": 6},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert not any("128 A100" in item["text"] for item in result["evidence"]), result
    assert not any("128" in claim["text"] for claim in result.get("claims", [])), result


def test_deleted_document_is_unavailable_to_pdf_structure_and_qa(client, sample_pdfs):
    document = wait_for_document(client, upload_pdf(client, sample_pdfs["distinct"])["id"])
    response = client.delete(f"/api/documents/{document['id']}")
    assert response.status_code in (200, 204), response.text
    for suffix in ("", "/pdf", "/structure"):
        assert client.get(f"/api/documents/{document['id']}{suffix}").status_code == 404
    response = client.post(
        "/api/ask",
        json={"question": "retrieval index", "document_ids": [document["id"]], "limit": 6},
    )
    if response.status_code == 200:
        assert not response.json()["evidence"]
    else:
        assert response.status_code in (400, 404, 422)


def test_claim_validator_rejects_fabricated_evidence_ids_and_numbers():
    from backend.app.qa import _validate_claims

    evidence = [{"id": "ev_known", "text": "The batch size is 32. The learning rate is 0.0003."}]
    result = _validate_claims(
        {
            "claims": [
                {"text": "The batch size is 32.", "evidence_ids": ["ev_missing"]},
                {"text": "The batch size is 999.", "evidence_ids": ["ev_known"]},
                {"text": "The batch size is 32.", "evidence_ids": ["ev_known"]},
            ]
        },
        evidence,
    )
    assert len(result) == 1
    assert result[0]["text"] == "The batch size is 32."
    assert result[0]["evidence_ids"] == ["ev_known"]
    assert _validate_claims({"claims": "invalid"}, evidence) == []


def test_historical_run_hides_prior_answer_after_its_source_is_deleted(client, sample_pdfs, monkeypatch):
    from backend.app import qa

    document = wait_for_document(client, upload_pdf(client, sample_pdfs["distinct"])["id"])

    async def synthetic_model_response(question, evidence):
        selected = next(item for item in evidence if "seven synthetic records" in item["text"])
        return {
            "claims": [
                {
                    "text": "The retrieval index contains seven synthetic records.",
                    "evidence_ids": [selected["id"]],
                }
            ],
            "missing_information": [],
        }

    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-key-not-a-secret")
    monkeypatch.setenv("LLM_MODEL", "synthetic-test-model")
    monkeypatch.setattr(qa, "_generate", synthetic_model_response)
    response = client.post(
        "/api/ask",
        json={"question": "How many records are in the retrieval index?", "document_ids": [document["id"]], "limit": 6},
    )
    assert response.status_code == 200, response.text
    original = response.json()
    assert original["claims"]
    assert "seven synthetic records" in original["answer"]
    assert client.delete(f"/api/documents/{document['id']}").status_code in (200, 204)
    historical = client.get(f"/api/agent-runs/{original['run_id']}")
    assert historical.status_code == 200, historical.text
    assert historical.json()["claims"] == []
    assert historical.json()["evidence"] == []
    assert "seven synthetic records" not in historical.text


def test_resuming_completed_pages_preserves_source_anchor_ids(client, sample_pdfs):
    from backend.app.parser import parse_document

    document = wait_for_document(client, upload_pdf(client, sample_pdfs["v1"])["id"])
    before = client.get(f"/api/documents/{document['id']}/structure").json()
    parse_document(document["id"])
    after = client.get(f"/api/documents/{document['id']}/structure").json()
    assert after["parse_run_id"] == before["parse_run_id"]
    assert [(item["id"], item["page"], item["bbox"]) for item in after["blocks"]] == [
        (item["id"], item["page"], item["bbox"]) for item in before["blocks"]
    ]
    assert len(after["pages"]) == len(before["pages"]) == 2
