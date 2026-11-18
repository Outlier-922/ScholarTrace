"""A source removed during an awaited model call must not leak into the reply."""

from contextlib import asynccontextmanager
import json

import httpx

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


def test_stream_publishes_provider_progress_and_only_checked_answer(client, sample_pdfs, monkeypatch):
    from backend.app import model_client

    document = wait_for_document(client, upload_pdf(client, sample_pdfs["distinct"])["id"])
    monkeypatch.setenv("LLM_BASE_URL", "https://fictional.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_MODEL", "synthetic-test-model")

    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

        @asynccontextmanager
        async def stream(self, method, url, **options):
            assert options["json"]["stream"]
            payload = json.loads(options["json"]["messages"][1]["content"])
            evidence = next(item for item in payload['evidence'] if "seven synthetic records" in item['text'])
            proposed = json.dumps({"claims":[
                {"text":"The retrieval index contains seven synthetic records.", "evidence_ids":[evidence["id"]]},
                {"text":"The retrieval index contains 999 synthetic records.", "evidence_ids":[evidence["id"]]},
            ]})

            class Stream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    for delta in ({"reasoning_content":"PRIVATE REASONING"}, {"content":proposed[:35]},
                                  {"content":proposed[35:]}):
                        yield ('data: ' + json.dumps({"choices":[{"index":0,"delta":delta}]}) + '\n\n').encode()
                    yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

            response = httpx.Response(200, headers={"content-type":"text/event-stream"}, stream=Stream(),
                                      request=httpx.Request(method, url))
            try:
                yield response
            finally:
                await response.aclose()

    monkeypatch.setattr(model_client.httpx, "AsyncClient", Provider)
    body = {"question":"How many records are in the retrieval index?", "document_ids":[document["id"]]}
    reply = client.post("/api/ask/stream", json=body)
    events = [json.loads(line) for line in reply.text.splitlines()]
    assert "PRIVATE REASONING" not in reply.text
    assert [event["phase"] for event in events if event["type"] == "progress"][-1] == "validating", events
    streamed = "".join(event["text"] for event in events if event["type"] == "answer_delta")
    assert streamed == events[-1]["result"]["answer"] and "seven synthetic records" in streamed
    assert "999" not in streamed
    raw = client.post("/api/ask/stream", json={**body, "use_model":False})
    assert [json.loads(line)["type"] for line in raw.text.splitlines()] == ["progress", "result"]
