"""Exercise a running local ScholarTrace API using labelled synthetic fixtures."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

import httpx

from create_sample_pdfs import generate_samples


def wait_for_document(client: httpx.Client, document_id: str) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/documents/{document_id}")
        response.raise_for_status()
        item = response.json()
        if item["status"] in {"ready", "partial", "failed"}:
            return item
        time.sleep(0.1)
    raise TimeoutError("PDF parsing did not finish within 30 seconds")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--keep", action="store_true", help="Keep uploaded synthetic documents after checking")
    arguments = parser.parse_args()
    uploaded_ids: list[str] = []
    created_ids: set[str] = set()
    report: dict = {"synthetic": True, "official_contest_validation": False, "checks": []}
    with tempfile.TemporaryDirectory(prefix="scholartrace-check-") as temporary:
        samples = generate_samples(Path(temporary))
        with httpx.Client(base_url=arguments.base_url, timeout=35) as client:
            try:
                health = client.get("/api/health")
                health.raise_for_status()
                report["health"] = health.json()
                existing_response = client.get("/api/documents")
                existing_response.raise_for_status()
                existing_ids = {item["id"] for item in existing_response.json()["items"]}
                for kind in ("v1", "v2", "scan"):
                    with samples[kind].open("rb") as stream:
                        response = client.post(
                            "/api/documents",
                            files=[("files", (f"verification-{kind}.pdf", stream, "application/pdf"))],
                        )
                    response.raise_for_status()
                    document_id = response.json()["items"][0]["id"]
                    uploaded_ids.append(document_id)
                    if document_id not in existing_ids:
                        created_ids.add(document_id)
                    document = wait_for_document(client, document_id)
                    expected_status = "partial" if kind == "scan" else "ready"
                    assert document["status"] == expected_status, document
                    structure = client.get(f"/api/documents/{document_id}/structure")
                    structure.raise_for_status()
                    report["checks"].append(
                        {"kind": kind, "document_id": document_id, "status": document["status"], "blocks": len(structure.json()["blocks"])}
                    )
                response = client.post(
                    "/api/ask",
                    json={"question": "What is the learning rate?", "document_ids": uploaded_ids[:1], "limit": 6},
                )
                response.raise_for_status()
                result = response.json()
                assert result["evidence"], result
                report["question"] = {"mode": result["mode"], "evidence_count": len(result["evidence"])}
                report["passed"] = True
            finally:
                if not arguments.keep:
                    for document_id in created_ids:
                        client.delete(f"/api/documents/{document_id}")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
