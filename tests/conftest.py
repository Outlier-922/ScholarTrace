from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def sample_pdfs(tmp_path_factory):
    from scripts.create_sample_pdfs import generate_samples

    return generate_samples(tmp_path_factory.mktemp("synthetic-pdfs"))


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient

    variable_names = ("SCHOLARTRACE_DATA_DIR", "LLM_API_KEY", "SCHOLARTRACE_ENABLE_OCR", "SCHOLARTRACE_ENABLE_TABLES", "SCHOLARTRACE_ENABLE_DENSE")
    previous = {name: os.environ.get(name) for name in variable_names}
    os.environ["SCHOLARTRACE_DATA_DIR"] = str(tmp_path_factory.mktemp("scholartrace-data"))
    os.environ["LLM_API_KEY"] = ""
    os.environ["SCHOLARTRACE_ENABLE_OCR"] = "false"
    os.environ["SCHOLARTRACE_ENABLE_TABLES"] = "false"
    os.environ["SCHOLARTRACE_ENABLE_DENSE"] = "false"
    from backend.app.main import app

    try:
        with TestClient(app) as api_client:
            yield api_client
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def upload_pdf(client, path: Path, filename: str | None = None):
    with path.open("rb") as stream:
        response = client.post(
            "/api/documents",
            files=[("files", (filename or path.name, stream, "application/pdf"))],
        )
    assert response.status_code in (200, 201, 202), response.text
    items = response.json()["items"]
    assert len(items) == 1
    return items[0]


def wait_for_document(client, document_id: str, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/documents/{document_id}")
        assert response.status_code == 200, response.text
        document = response.json()
        if document["status"] in {"ready", "partial", "failed"}:
            return document
        time.sleep(0.05)
    pytest.fail(f"Document {document_id} did not finish within {timeout}s")
