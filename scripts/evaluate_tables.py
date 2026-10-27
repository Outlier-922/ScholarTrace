"""Evaluate explicit PDF pages/regions without changing the source document.

Example:
  python scripts/evaluate_tables.py --pdf <paper.pdf> --pages 6 8 9 10 \
    --text-region 8:130,96,480,244 --text-region 9:107,126,510,391

Regions are manual evaluation inputs, never a hidden source-specific extraction
rule. Reports preserve raw cells and limitations rather than claim accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymupdf

from backend.app.tables import TableExtractionError, extract_tables


def _region(value: str) -> tuple[int, tuple[float, ...]]:
    page, coordinates = value.split(":", 1)
    result = tuple(float(item) for item in coordinates.split(","))
    if len(result) != 4:
        raise argparse.ArgumentTypeError("Region format: PAGE:x0,y0,x1,y1")
    return int(page), result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pages", type=int, nargs="+", required=True)
    parser.add_argument("--text-region", type=_region, action="append", default=[])
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "table_report.json")
    arguments = parser.parse_args()
    path = arguments.pdf.resolve()
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "source_file": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "source_modified": False, "engine": f"PyMuPDF {pymupdf.VersionBind}",
              "verification_status": "unverified_candidates", "pages": [],
              "limitations": ["Line detection can miss borderless tables or keep multiple physical rows in one cell.",
                              "Text-position detection can split model names, mathematical notation, and multi-level headers.",
                              "Selected text regions are manual evaluation inputs, not an automatic caption detector.",
                              "Sparse fragmented grids with weak headers are filtered as suspected graphics, not proven to be non-tables.",
                              "Unlabelled sparse categorical matrices can be missed by this negative filter; reviewed extraction can disable it.",
                              "No inferred blank-cell propagation, benchmark conclusions, or global accuracy estimate."]}
    with pymupdf.open(path) as document:
        selected = list(dict.fromkeys(arguments.pages + [page for page, _ in arguments.text_region]))
        for number in selected:
            if not 1 <= number <= len(document):
                parser.error(f"PDF page {number} is outside the document")
            page = document[number - 1]
            runs = [("lines", None)] + [("text", region) for page_number, region in arguments.text_region if page_number == number]
            for strategy, clip in runs:
                started = time.perf_counter()
                item = {"page": number, "strategy": strategy, "manual_clip": list(clip) if clip else None}
                try:
                    rejected = []
                    tables = extract_tables(page, strategy=strategy, clip=clip, diagnostics=rejected)
                    item.update({"status": "completed", "count": len(tables), "tables": tables,
                                 "filtered_candidates": rejected})
                except TableExtractionError as error:
                    item.update({"status": "failed", "error_code": error.code, "error": str(error), "tables": []})
                item["elapsed_seconds"] = round(time.perf_counter() - started, 4)
                report["pages"].append(item)
                print(f"page={number} strategy={strategy} count={item.get('count', 0)} status={item['status']}")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {arguments.output.resolve()}")


if __name__ == "__main__":
    main()
