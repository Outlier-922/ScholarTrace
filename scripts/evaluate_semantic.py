"""Small, explicitly scoped retrieval check against user-provided Transformer PDF."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from backend.app import qa, store
    store.init_db()
    document = next(item for item in store.list_documents() if item["filename"] == "1706.03762v7.pdf")
    questions = [
        {"question": "训练采用了哪种图形处理设备？", "expected_phrase": "NVIDIA P100", "expected_pages": [7, 8]},
        {"question": "英德翻译的效果分数是多少？", "expected_phrase": "28.4", "expected_pages": [1, 8]},
        {"question": "What are the Adam optimizer beta settings?", "expected_phrase": "0.98", "expected_pages": [7]},
    ]
    report = {"scope": "3 manually selected development cases, with domain query expansion; not a held-out accuracy benchmark",
              "document_id": document["id"], "parse_run_id": document["parse_run_id"], "questions": []}
    previous = os.environ.get("SCHOLARTRACE_ENABLE_DENSE")
    try:
        for question in questions:
            row = dict(question)
            for name, flag in [("bm25", "false"), ("bm25_dense_rrf", "true")]:
                os.environ["SCHOLARTRACE_ENABLE_DENSE"] = flag
                details = {}
                start = time.monotonic()
                evidence = qa._retrieve(question["question"], [document], 6, details)
                matched = [item for item in evidence if question["expected_phrase"] in item["text"] and item["page"] in question["expected_pages"]]
                row[name] = {"evidence_count": len(evidence), "target_hit_at_6": bool(matched),
                             "pages": [item["page"] for item in evidence], "dense_used": details.get("used", False),
                             "elapsed_seconds": round(time.monotonic() - start, 3)}
            report["questions"].append(row)
    finally:
        if previous is None:
            os.environ.pop("SCHOLARTRACE_ENABLE_DENSE", None)
        else:
            os.environ["SCHOLARTRACE_ENABLE_DENSE"] = previous
    path = ROOT / "data" / "semantic-eval-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
