"""A source removed during an awaited model call must not leak into the reply."""

from conftest import upload_pdf, wait_for_document


def test_source_deleted_while_model_is_running_is_filtered_before_response(client, sample_pdfs, monkeypatch):
    from backend.app import qa, store

    document = wait_for_document(client, upload_pdf(client, sample_pdfs["distinct"])["id"])

    async def delete_during_generation(question, evidence):
        selected = next(item for item in evidence if "seven synthetic records" in item["text"])
        store.delete_document(document["id"])
        return {"claims": [{"text": "The retrieval index contains seven synthetic records.", "evidence_ids": [selected["id"]]}]}

    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-key-not-a-secret")
    monkeypatch.setenv("LLM_MODEL", "synthetic-test-model")
    monkeypatch.setattr(qa, "_generate", delete_during_generation)
    response = client.post("/api/ask", json={"question": "How many records are in the retrieval index?", "document_ids": [document["id"]]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"] == []
    assert payload["claims"] == []
    assert "seven synthetic records" not in payload["answer"]
