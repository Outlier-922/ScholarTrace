"""Run actual local OCR/table reprocessing and preserve source-history checks."""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def wait(client, identity, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/documents/{identity}")
        response.raise_for_status()
        document = response.json()
        if document["status"] in {"ready", "partial", "failed"}:
            return document
        time.sleep(0.5)
    raise TimeoutError(f"Document did not finish: {identity}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "stage2-official-report.json")
    parser.add_argument("--only", choices=["all", "scan", "tables"], default="all")
    arguments = parser.parse_args()
    report = {"date": datetime.now(timezone.utc).isoformat(), "official_input": True,
              "stage": 2, "model_facts_accuracy_evaluated": False, "checks": []}
    if arguments.only != "all" and arguments.output.exists():
        report = json.loads(arguments.output.read_text(encoding="utf-8"))
    report["passed"] = False
    report["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    with httpx.Client(base_url=arguments.api_url, timeout=60) as client:
        health = client.get("/api/health")
        health.raise_for_status()
        report["health"] = health.json()
        documents = client.get("/api/documents").json()["items"]
        for filename, options in [("1706.03762v1_img.pdf", {"ocr": True, "tables": False}),
                                  ("1706.03762v7.pdf", {"ocr": False, "tables": True})]:
            if arguments.only == "scan" and not options["ocr"]:
                continue
            if arguments.only == "tables" and not options["tables"]:
                continue
            document = next(item for item in documents if item["filename"] == filename)
            identity = document["id"]
            old_run = document["parse_run_id"]
            before = client.get(f"/api/documents/{identity}/structure", params={"page": 1}).json()
            original_blocks = [block["id"] for block in before["blocks"]]
            started = time.monotonic()
            response = client.post(f"/api/documents/{identity}/reprocess", json=options)
            response.raise_for_status()
            completed = wait(client, identity)
            assert completed["status"] != "failed", completed
            structure_response = client.get(f"/api/documents/{identity}/structure")
            structure_response.raise_for_status()
            structure = structure_response.json()
            historical_response = client.get(f"/api/documents/{identity}/structure", params={"page": 1, "parse_run_id": old_run})
            historical_response.raise_for_status()
            historical = historical_response.json()
            assert [block["id"] for block in historical["blocks"]] == original_blocks
            assert completed["parse_run_id"] != old_run
            ocr_blocks = [block for block in structure["blocks"] if block.get("metadata", {}).get("extraction") == "rapidocr"]
            tables = [block for block in structure["blocks"] if block["type"] == "table"]
            check = {"filename": filename, "document_id": identity, "options": options,
                     "status": completed["status"], "old_parse_run_id": old_run,
                     "new_parse_run_id": completed["parse_run_id"], "old_anchors_preserved": True,
                     "elapsed_seconds": round(time.monotonic() - started, 3),
                     "page_states": dict(Counter(page["status"] for page in structure["pages"])),
                     "blocks": len(structure["blocks"]), "ocr_blocks": len(ocr_blocks),
                     "ocr_characters": sum(len(block["text"]) for block in ocr_blocks),
                     "tables": len(tables), "warnings": completed["warnings"]}
            if options["ocr"]:
                assert ocr_blocks, "No real OCR blocks produced"
                assert all(page["status"] == "ocr_ready" for page in structure["pages"]), check
                question = "What GPUs were used for training?"
            else:
                assert tables, "No structured native table detected"
                question = "Transformer WSJ 23 F1 parser"
                check["table_summaries"] = [{"page": block["page"], "header": block["metadata"].get("header"), "row_count": block["metadata"].get("row_count"), "col_count": block["metadata"].get("col_count")} for block in tables]
            answer_response = client.post("/api/ask", json={"question": question, "document_ids": [identity], "limit": 6})
            answer_response.raise_for_status()
            answer = answer_response.json()
            assert answer["evidence"], answer
            assert all(item.get("parse_run_id") == completed["parse_run_id"] for item in answer["evidence"])
            check["retrieval"] = {"mode": answer["mode"], "retrieval": answer.get("retrieval"),
                                  "evidence_count": len(answer["evidence"]),
                                  "pages": [item["page"] for item in answer["evidence"]],
                                  "ocr_evidence": sum(item.get("extraction") == "rapidocr" for item in answer["evidence"])}
            report["checks"] = [item for item in report["checks"] if item["filename"] != filename] + [check]
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(check, ensure_ascii=False), flush=True)
    report["passed"] = True
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
