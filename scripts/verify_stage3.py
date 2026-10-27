"""Smoke-check already imported official PDFs; never uploads or translates them."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000/api")
    parser.add_argument("--output", type=Path, default=Path("data/stage3_official_report.json"))
    args = parser.parse_args()
    with httpx.Client(base_url=args.api.rstrip("/"), timeout=180) as client:
        def get(route):
            response = client.get(route)
            response.raise_for_status()
            return response.json()
        docs = get("/documents")["items"]
        transformer = next((item for item in docs if item["filename"] == "1706.03762v7.pdf"), None)
        bert = next((item for item in docs if item["filename"] == "N19-1423.pdf"), None)
        if not transformer or not bert:
            raise SystemExit("请先导入官方 1706.03762v7.pdf 和 N19-1423.pdf。")
        health = get("/health")
        report = {"health": health, "documents": len(docs), "citation_runs": []}
        for document in [transformer, bert]:
            payload = get(f"/documents/{document['id']}/citations")
            assert payload["parse_run_id"] == document["parse_run_id"]
            refs = {item["id"]: item for item in payload["references"]}
            for link in payload["links"]:
                assert link["parse_run_id"] == document["parse_run_id"]
                assert all(link in refs[target]["backlinks"] for target in link["reference_ids"])
            report["citation_runs"].append({"document_id": document["id"], "title": document["title"],
                "parse_run_id": payload["parse_run_id"], "reference_candidates": len(refs), "links": len(payload["links"]),
                "backlinks": sum(len(item["backlinks"]) for item in refs.values())})
        start = time.monotonic()
        response = client.post("/comparisons", json={"document_ids": [transformer["id"], bert["id"]], "dimensions": ["architecture", "objective", "hardware", "optimizer"]})
        response.raise_for_status()
        comparison = response.json()
        runs = {document["id"]: document["parse_run_id"] for document in [transformer, bert]}
        for row in comparison["rows"]:
            for cell in row["cells"]:
                assert cell["parse_run_id"] == runs[cell["document_id"]]
                assert all(source["document_id"] == cell["document_id"] and source["parse_run_id"] == cell["parse_run_id"] for source in cell["evidence"])
        exported = client.get(f"/comparisons/{comparison['id']}/markdown")
        exported.raise_for_status()
        assert transformer["parse_run_id"] in exported.text and bert["parse_run_id"] in exported.text
        report["comparison"] = {"id": comparison["id"], "mode": comparison["mode"], "seconds": round(time.monotonic()-start, 3), "rows": comparison["rows"]}
        if not health["model_configured"]:
            status = client.post("/translations", json={"document_id": transformer["id"], "scope": "document"}).status_code
            assert status == 503
            report["unconfigured_translation_status"] = status
        else:
            report["translation"] = "not called: use a small paragraph for separate live model evaluation"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report": str(args.output), "citation_links": [item["links"] for item in report["citation_runs"]],
                          "comparison_mode": comparison["mode"], "seconds": report["comparison"]["seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
